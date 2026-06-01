"""HTTP server for EDOPro WindBot bridge decisions and post-duel learning."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ygotrainingbot.agents import create_agent
from ygotrainingbot.duel_logs import build_game_log_payload, trace_to_dict
from ygotrainingbot.human_duels import (
    DEFAULT_CATALOG_DIR,
    build_learning_report,
    import_human_duel_files,
    write_learning_report,
)
from ygotrainingbot.learning import learn_from_report
from ygotrainingbot.models import DuelTrace, GameAction, MatchResult, VisibleGameState


@dataclass
class DuelSession:
    session_id: str
    human_player: str
    bot_player: str
    format_name: str
    meta: dict[str, Any] = field(default_factory=dict)
    traces: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)


class EdoproBotServer:
    """Serve policy decisions to a WindBot C# bridge and log duels for learning."""

    def __init__(
        self,
        *,
        policy_path: Path,
        catalog_dir: Path = DEFAULT_CATALOG_DIR,
        bot_policy: str = "heuristic",
        learn_after_duel: bool = False,
        study_agent: str | None = None,
    ) -> None:
        self._policy_path = policy_path
        self._catalog_dir = catalog_dir
        self._bot_policy = bot_policy
        self._learn_after_duel = learn_after_duel
        self._study_agent = study_agent
        self._sessions: dict[str, DuelSession] = {}
        self._lock = threading.Lock()
        self._agent = create_agent(bot_policy, "bot", self._load_weights())

    def reload_policy(self) -> None:
        self._agent = create_agent(self._bot_policy, "bot", self._load_weights())

    def _load_weights(self) -> dict[str, float]:
        if not self._policy_path.is_file():
            return {}
        payload = json.loads(self._policy_path.read_text(encoding="utf-8"))
        raw = payload.get("tag_weights", {})
        if not isinstance(raw, dict):
            return {}
        return {str(key): float(value) for key, value in raw.items()}

    def handle(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        route = urlparse(path).path.rstrip("/") or "/"
        if method == "GET" and route in {"/", "/health", "/v1/health"}:
            return 200, {
                "status": "ok",
                "bot_policy": self._bot_policy,
                "policy_path": str(self._policy_path),
                "sessions": len(self._sessions),
            }
        if method == "POST" and route == "/v1/start":
            return self._start(body)
        if method == "POST" and route == "/v1/decide":
            return self._decide(body)
        if method == "POST" and route == "/v1/finish":
            return self._finish(body)
        if method == "POST" and route == "/v1/reload-policy":
            self.reload_policy()
            return 200, {"status": "reloaded"}
        return 404, {"error": f"unknown route {method} {route}"}

    def _start(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        session_id = str(body.get("session_id") or uuid.uuid4())
        human = str(body.get("human_player") or body.get("opponent") or "you")
        bot = str(body.get("bot_player") or "bot")
        fmt = str(body.get("format") or body.get("format_name") or "edopro-live")
        session = DuelSession(
            session_id=session_id,
            human_player=human,
            bot_player=bot,
            format_name=fmt,
            meta={
                "source": "human",
                "format": fmt,
                "player_a": human,
                "player_b": bot,
                "study_agent": self._study_agent or bot,
                "human_player": human,
                "bot_player": bot,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **({} if not isinstance(body.get("meta"), dict) else dict(body["meta"])),
            },
        )
        with self._lock:
            self._sessions[session_id] = session
        return 200, {"session_id": session_id, "bot_player": bot}

    def _decide(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        session_id = str(body.get("session_id") or "")
        session = self._sessions.get(session_id)
        if session is None:
            return 400, {"error": "unknown session_id; POST /v1/start first"}

        legal_raw = body.get("legal_actions")
        if not isinstance(legal_raw, list) or not legal_raw:
            return 400, {"error": "legal_actions must be a non-empty list"}

        legal_actions = tuple(self._action_from_payload(item) for item in legal_raw if isinstance(item, dict))
        if not legal_actions:
            return 400, {"error": "no valid legal_actions"}

        duel_turn = int(body.get("duel_turn") or body.get("turn") or 1)
        decision_index = int(body.get("decision_index") or len(session.traces) + 1)
        state = VisibleGameState(
            state_id=str(body.get("state_id") or f"edopro-{session_id}-{decision_index}"),
            turn=duel_turn,
            active_player=str(body.get("active_player") or session.bot_player),
            summary=str(body.get("summary") or ""),
            legal_actions=legal_actions,
            public_zones={},
            decision_index=decision_index,
        )

        chosen = self._agent.choose_action(state)
        if chosen not in legal_actions:
            chosen = legal_actions[0]

        note = ""
        explain = getattr(self._agent, "explain_decision", None)
        if callable(explain):
            note = str(explain(state, chosen))

        trace = DuelTrace(state=state, action=chosen, agent_name=session.bot_player, note=note)
        session.traces.append(trace_to_dict(trace))

        return 200, {
            "action_id": chosen.action_id,
            "label": chosen.label,
            "selected_tags": list(chosen.tags),
            "evaluation": note,
        }

    def _finish(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        session_id = str(body.get("session_id") or "")
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return 400, {"error": "unknown session_id"}

        winner = body.get("winner")
        loser = body.get("loser")
        result = MatchResult(
            winner=str(winner) if winner is not None else None,
            loser=str(loser) if loser is not None else None,
            turns=int(body.get("turns") or body.get("duel_turns") or 0),
            traces=tuple(),
            tags=("edopro", "human-vs-bot"),
            metadata={"end_reason": body.get("end_reason"), "life_points": body.get("life_points")},
        )
        payload = build_game_log_payload(meta=session.meta, result=result)
        payload["traces"] = list(session.traces)
        if isinstance(body.get("traces"), list):
            payload["traces"] = body["traces"]

        duel_id = f"edopro-live-{int(time.time())}"
        catalog_dir = self._catalog_dir
        duels_dir = catalog_dir / "duels"
        duels_dir.mkdir(parents=True, exist_ok=True)
        dest = duels_dir / f"{duel_id}.json"
        dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        import_result = import_human_duel_files(
            [(dest.name, dest.read_text(encoding="utf-8"))],
            catalog_dir=catalog_dir,
        )

        learning: dict[str, Any] | None = None
        if self._learn_after_duel:
            study = self._study_agent or session.bot_player
            report = build_learning_report(catalog_dir, study_agent=study, bot_agent=session.bot_player)
            report_path = write_learning_report(catalog_dir, report, name=f"live-{duel_id}-report.json")
            _analysis, english = learn_from_report(report_path, self._policy_path)
            summary_path = catalog_dir / "learning-summary.txt"
            summary_path.write_text(english, encoding="utf-8")
            learning = {
                "report_path": str(report_path),
                "summary_path": str(summary_path),
                "policy_path": str(self._policy_path),
            }

        return 200, {
            "duel_log_path": str(dest),
            "imported": len(import_result.imported),
            "learning": learning,
            "winner": result.winner,
        }

    @staticmethod
    def _action_from_payload(payload: dict[str, Any]) -> GameAction:
        expected_value = payload.get("expected_value")
        return GameAction(
            action_id=str(payload.get("action_id", payload.get("id", "0"))),
            label=str(payload.get("label", payload.get("action_id", "action"))),
            expected_value=float(expected_value) if expected_value is not None else None,
            tags=tuple(str(tag) for tag in payload.get("tags", ())),
        )


def run_edopro_bot_server(
    host: str,
    port: int,
    *,
    policy_path: Path,
    catalog_dir: Path,
    bot_policy: str,
    learn_after_duel: bool,
    study_agent: str | None,
) -> None:
    server_impl = EdoproBotServer(
        policy_path=policy_path,
        catalog_dir=catalog_dir,
        bot_policy=bot_policy,
        learn_after_duel=learn_after_duel,
        study_agent=study_agent,
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def _respond(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            status, payload = server_impl.handle("GET", self.path, {})
            self._respond(status, payload)

        def do_POST(self) -> None:  # noqa: N802
            status, payload = server_impl.handle("POST", self.path, self._read_json())
            self._respond(status, payload)

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Ygotrainingbot EDOPro bridge listening on http://{host}:{port}/")
    print(f"  policy: {policy_path}")
    print(f"  catalog: {catalog_dir}")
    print("  WindBot executor should POST /v1/decide while connected to your EDOPro host.")
    httpd.serve_forever()
