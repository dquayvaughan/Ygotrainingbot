"""Mobile-friendly web dashboard for launching and monitoring training."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ygotrainingbot.format_training import load_format_pack
from ygotrainingbot.learning import learn_from_report


@dataclass(frozen=True, slots=True)
class DashboardSettings:
    """Runtime paths used by the dashboard."""

    repo_root: Path
    jobs_dir: Path
    edopro_home: Path
    gateway_script: Path
    python_executable: str = sys.executable


@dataclass(slots=True)
class TrainingJob:
    """Persisted training job metadata."""

    job_id: str
    pack: str
    status: str
    games_per_matchup: int
    max_decisions: int
    created_at: float
    started_at: float | None
    finished_at: float | None
    returncode: int | None
    log_path: str
    report_path: str
    summary_path: str
    policy_path: str
    using_learned_policy: str | None
    error: str | None = None


class DashboardState:
    """State and job management for dashboard HTTP handlers."""

    def __init__(self, settings: DashboardSettings) -> None:
        self.settings = settings
        self.settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def format_packs(self) -> list[dict[str, object]]:
        packs: list[dict[str, object]] = []
        for pack_path in sorted((self.settings.repo_root / "configs/format-packs").glob("*.json")):
            pack = load_format_pack(pack_path)
            packs.append(
                {
                    "name": pack.name,
                    "path": str(pack_path.relative_to(self.settings.repo_root)),
                    "description": pack.description,
                    "deck_count": len(pack.decks),
                    "default_games": pack.games,
                    "default_max_decisions": pack.max_decisions,
                }
            )
        return packs

    def jobs(self) -> list[dict[str, object]]:
        jobs = [self._read_job_meta(path) for path in self.settings.jobs_dir.glob("*/meta.json")]
        return sorted(jobs, key=lambda job: str(job["created_at"]), reverse=True)

    def job(self, job_id: str) -> dict[str, object]:
        meta_path = self._job_dir(job_id) / "meta.json"
        if not meta_path.exists():
            raise KeyError(job_id)
        return self._read_job_meta(meta_path)

    def start_job(self, pack_path: str, games_per_matchup: int, max_decisions: int) -> TrainingJob:
        if games_per_matchup < 1:
            raise ValueError("games_per_matchup must be at least 1.")
        if max_decisions < 1:
            raise ValueError("max_decisions must be at least 1.")

        resolved_pack = self._resolve_pack_path(pack_path)
        load_format_pack(resolved_pack)

        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        job = TrainingJob(
            job_id=job_id,
            pack=str(resolved_pack.relative_to(self.settings.repo_root)),
            status="queued",
            games_per_matchup=games_per_matchup,
            max_decisions=max_decisions,
            created_at=time.time(),
            started_at=None,
            finished_at=None,
            returncode=None,
            log_path=self._display_path(job_dir / "training.log"),
            report_path=self._display_path(job_dir / "report.json"),
            summary_path=self._display_path(job_dir / "learning-summary.txt"),
            policy_path=self._display_path(job_dir / "learned-policy.json"),
            using_learned_policy=self._display_path(self._global_policy_path())
            if self._global_policy_path().exists()
            else None,
        )
        self._write_job(job)
        thread = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        thread.start()
        return job

    def log_text(self, job_id: str) -> str:
        log_path = self._job_dir(job_id) / "training.log"
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8", errors="replace")

    def report(self, job_id: str) -> dict[str, object]:
        report_path = self._job_dir(job_id) / "report.json"
        if not report_path.exists():
            raise KeyError(job_id)
        return json.loads(report_path.read_text(encoding="utf-8"))

    def summary_text(self, job_id: str) -> str:
        summary_path = self._job_dir(job_id) / "learning-summary.txt"
        if not summary_path.exists():
            return "Learning summary is not ready yet."
        return summary_path.read_text(encoding="utf-8", errors="replace")

    def _run_job(self, job: TrainingJob) -> None:
        job_dir = self._job_dir(job.job_id)
        log_path = job_dir / "training.log"
        report_path = job_dir / "report.json"

        try:
            job.status = "running"
            job.started_at = time.time()
            self._write_job(job)
            with log_path.open("a", encoding="utf-8") as log:
                self._ensure_gateway_dependencies(log)
                self._ensure_edopro_home(log)
                command = [
                    self.settings.python_executable,
                    "-m",
                    "ygotrainingbot.cli",
                    "train-format-pack",
                    "--pack",
                    job.pack,
                    "--edopro-home",
                    str(self.settings.edopro_home),
                    "--gateway-script",
                    str(self.settings.gateway_script),
                    "--games-per-matchup",
                    str(job.games_per_matchup),
                    "--max-decisions",
                    str(job.max_decisions),
                    "--output",
                    str(report_path),
                ]
                if job.using_learned_policy:
                    command.extend([
                        "--agent-a-weights",
                        str(self._global_policy_path()),
                        "--agent-b-weights",
                        str(self._global_policy_path()),
                    ])
                log.write("$ " + " ".join(command) + "\n")
                log.flush()
                process = subprocess.run(
                    command,
                    cwd=self.settings.repo_root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                )
                job.returncode = process.returncode
                if process.returncode == 0:
                    _analysis, english = learn_from_report(
                        report_path,
                        job_dir / "learned-policy.json",
                    )
                    (job_dir / "learning-summary.txt").write_text(english, encoding="utf-8")
                    self._global_policy_path().write_text(
                        (job_dir / "learned-policy.json").read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
                    log.write("\n$ generated learning-summary.txt and learned-policy.json\n")
                    log.write("$ updated .ygotrain/learned-policy.json for the next run\n")
                    job.status = "completed"
                else:
                    job.status = "failed"
        except Exception as exc:  # pragma: no cover - defensive job boundary
            job.status = "failed"
            job.error = str(exc)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\nERROR: {exc}\n")
        finally:
            job.finished_at = time.time()
            self._write_job(job)

    def _ensure_gateway_dependencies(self, log: Any) -> None:
        node_modules = self.settings.repo_root / "gateways/edopro-ocgcore/node_modules"
        if node_modules.exists():
            return
        command = ["npm", "ci", "--prefix", "gateways/edopro-ocgcore"]
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
        subprocess.run(
            command,
            cwd=self.settings.repo_root,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )

    def _ensure_edopro_home(self, log: Any) -> None:
        if (self.settings.edopro_home / "cards.cdb").exists():
            return
        command = ["scripts/bootstrap_edopro_home.sh", str(self.settings.edopro_home)]
        log.write("$ " + " ".join(command) + "\n")
        log.flush()
        subprocess.run(
            command,
            cwd=self.settings.repo_root,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )

    def _resolve_pack_path(self, pack_path: str) -> Path:
        candidate = (self.settings.repo_root / pack_path).resolve()
        packs_root = (self.settings.repo_root / "configs/format-packs").resolve()
        if packs_root not in candidate.parents or candidate.suffix != ".json":
            raise ValueError("pack must be a JSON file under configs/format-packs.")
        if not candidate.exists():
            raise ValueError(f"pack does not exist: {pack_path}")
        return candidate

    def _job_dir(self, job_id: str) -> Path:
        if "/" in job_id or "\\" in job_id or ".." in job_id:
            raise ValueError("invalid job id.")
        return self.settings.jobs_dir / job_id

    def _global_policy_path(self) -> Path:
        return self.settings.jobs_dir.parent / "learned-policy.json"

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.settings.repo_root))
        except ValueError:
            return str(path)

    def _read_job_meta(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_job(self, job: TrainingJob) -> None:
        with self._lock:
            meta_path = self._job_dir(job.job_id) / "meta.json"
            meta_path.write_text(json.dumps(asdict(job), indent=2, sort_keys=True), encoding="utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP routes for the dashboard UI and JSON API."""

    state: DashboardState

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            self._handle_get()
        except Exception as exc:  # pragma: no cover - HTTP safety net
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            self._handle_post()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pragma: no cover - HTTP safety net
            self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle_get(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(DASHBOARD_HTML)
        elif path == "/api/format-packs":
            self._send_json({"format_packs": self.state.format_packs()})
        elif path == "/api/jobs":
            self._send_json({"jobs": self.state.jobs()})
        elif path.startswith("/api/jobs/") and path.endswith("/log"):
            job_id = path.split("/")[3]
            self._send_text(self.state.log_text(job_id))
        elif path.startswith("/api/jobs/") and path.endswith("/report"):
            job_id = path.split("/")[3]
            self._send_json(self.state.report(job_id))
        elif path.startswith("/api/jobs/") and path.endswith("/summary"):
            job_id = path.split("/")[3]
            self._send_text(self.state.summary_text(job_id))
        elif path.startswith("/api/jobs/"):
            job_id = path.split("/")[3]
            self._send_json({"job": self.state.job(job_id)})
        else:
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _handle_post(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/jobs":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        payload = self._read_json()
        job = self.state.start_job(
            pack_path=str(payload.get("pack", "")),
            games_per_matchup=int(payload.get("games_per_matchup", 5)),
            max_decisions=int(payload.get("max_decisions", 60)),
        )
        self._send_json({"job": asdict(job)}, status=HTTPStatus.CREATED)

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length) if length else b"{}"
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object.")
        return payload

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_dashboard(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    repo_root: Path | None = None,
    edopro_home: Path | None = None,
) -> None:
    """Run the training dashboard HTTP server."""

    root = (repo_root or Path.cwd()).resolve()
    settings = DashboardSettings(
        repo_root=root,
        jobs_dir=root / ".ygotrain/jobs",
        edopro_home=edopro_home or Path("/tmp/ygotrain/edopro-home"),
        gateway_script=root / "gateways/edopro-ocgcore/gateway.mjs",
    )
    DashboardHandler.state = DashboardState(settings)
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"YGO training dashboard running at http://{host}:{port}")
    server.serve_forever()


def main() -> int:
    """Console entry point for the dashboard."""

    parser = argparse.ArgumentParser(prog="ygotrain-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--edopro-home", type=Path, default=Path("/tmp/ygotrain/edopro-home"))
    args = parser.parse_args()
    run_dashboard(
        host=args.host,
        port=args.port,
        repo_root=args.repo_root,
        edopro_home=args.edopro_home,
    )
    return 0


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>YGO Training Dashboard</title>
  <style>
    :root { color-scheme: dark; --bg: #0f172a; --panel: #111827; --muted: #94a3b8; --text: #e5e7eb; --accent: #38bdf8; --ok: #22c55e; --bad: #ef4444; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: linear-gradient(180deg, #020617, var(--bg)); color: var(--text); }
    header { padding: 24px 18px 8px; }
    h1 { margin: 0 0 6px; font-size: 28px; }
    p { color: var(--muted); line-height: 1.45; }
    main { display: grid; gap: 16px; padding: 16px; max-width: 1100px; margin: 0 auto; }
    section { background: rgba(17, 24, 39, .92); border: 1px solid rgba(148, 163, 184, .18); border-radius: 18px; padding: 16px; box-shadow: 0 18px 60px rgba(0,0,0,.25); }
    label { display: block; color: var(--muted); font-size: 13px; margin: 12px 0 6px; }
    select, input, button { width: 100%; border-radius: 12px; border: 1px solid rgba(148, 163, 184, .25); padding: 12px; background: #020617; color: var(--text); font-size: 16px; }
    button { margin-top: 14px; background: var(--accent); color: #082f49; border: 0; font-weight: 800; cursor: pointer; }
    button:disabled { opacity: .6; cursor: wait; }
    .grid { display: grid; gap: 16px; }
    .job { border: 1px solid rgba(148, 163, 184, .16); border-radius: 14px; padding: 12px; margin-top: 10px; background: #020617; }
    .status { display: inline-block; padding: 4px 9px; border-radius: 999px; font-size: 12px; font-weight: 800; background: #334155; }
    .completed { background: rgba(34,197,94,.18); color: var(--ok); }
    .failed { background: rgba(239,68,68,.18); color: var(--bad); }
    .running, .queued { background: rgba(56,189,248,.18); color: var(--accent); }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; background: #020617; border-radius: 12px; padding: 12px; border: 1px solid rgba(148, 163, 184, .16); }
    a { color: var(--accent); }
    @media (min-width: 850px) { .grid { grid-template-columns: 380px 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>YGO Training Dashboard</h1>
    <p>Start format-pack training, monitor jobs, and open reports from your phone.</p>
  </header>
  <main class="grid">
    <section>
      <h2>Start training</h2>
      <label for="pack">Format pack</label>
      <select id="pack"></select>
      <label for="games">Games per matchup</label>
      <input id="games" type="number" min="1" value="5" />
      <label for="decisions">Max decisions per game</label>
      <input id="decisions" type="number" min="1" value="60" />
      <button id="start">Start training</button>
      <p id="message"></p>
    </section>
    <section>
      <h2>Jobs</h2>
      <div id="jobs"></div>
    </section>
    <section style="grid-column: 1 / -1;">
      <h2>Selected job log</h2>
      <pre id="log">Select a job to view logs.</pre>
    </section>
  </main>
  <script>
    const packSelect = document.querySelector('#pack');
    const jobsEl = document.querySelector('#jobs');
    const logEl = document.querySelector('#log');
    const msgEl = document.querySelector('#message');
    let selectedJob = null;

    async function json(url, options) {
      const res = await fetch(url, options);
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }

    async function loadPacks() {
      const data = await json('/api/format-packs');
      packSelect.innerHTML = data.format_packs.map(p =>
        `<option value="${p.path}" data-games="${p.default_games}" data-decisions="${p.default_max_decisions}">${p.name} (${p.deck_count} decks)</option>`
      ).join('');
    }

    packSelect.addEventListener('change', () => {
      const opt = packSelect.selectedOptions[0];
      if (!opt) return;
      document.querySelector('#games').value = Math.min(Number(opt.dataset.games || 5), 25);
      document.querySelector('#decisions').value = opt.dataset.decisions || 60;
    });

    async function startJob() {
      const button = document.querySelector('#start');
      button.disabled = true;
      msgEl.textContent = 'Starting...';
      try {
        const payload = {
          pack: packSelect.value,
          games_per_matchup: Number(document.querySelector('#games').value),
          max_decisions: Number(document.querySelector('#decisions').value)
        };
        const data = await json('/api/jobs', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        selectedJob = data.job.job_id;
        msgEl.textContent = `Started ${selectedJob}`;
        await loadJobs();
      } catch (err) {
        msgEl.textContent = String(err);
      } finally {
        button.disabled = false;
      }
    }

    async function loadJobs() {
      const data = await json('/api/jobs');
      jobsEl.innerHTML = data.jobs.map(j => {
        const report = j.status === 'completed' ? `<a href="/api/jobs/${j.job_id}/report" target="_blank">Report</a> · <a href="/api/jobs/${j.job_id}/summary" target="_blank">What I learned</a>` : '';
        return `<div class="job" data-job="${j.job_id}">
          <strong>${j.pack}</strong><br/>
          <span class="status ${j.status}">${j.status}</span>
          <p>${j.games_per_matchup} games/matchup · ${j.max_decisions} decisions · ${j.job_id}</p>
          ${report}
        </div>`;
      }).join('') || '<p>No jobs yet.</p>';
      document.querySelectorAll('.job').forEach(el => el.addEventListener('click', () => {
        selectedJob = el.dataset.job;
        loadLog();
      }));
    }

    async function loadLog() {
      if (!selectedJob) return;
      const res = await fetch(`/api/jobs/${selectedJob}/log`);
      logEl.textContent = await res.text();
    }

    document.querySelector('#start').addEventListener('click', startJob);
    setInterval(() => { loadJobs(); loadLog(); }, 3000);
    loadPacks().then(loadJobs).catch(err => msgEl.textContent = String(err));
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
