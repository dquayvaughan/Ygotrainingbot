"""EDOPro integration helpers.

EDOPro is primarily a graphical client. For bot training, this module connects
to an EDOPro-core-compatible headless gateway process that speaks JSON lines.
The gateway owns the rules engine and legal-action generation; this package owns
agent selection, traces, and coaching data.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from ygotrainingbot.agents import DuelAgent
from ygotrainingbot.duel_live_log import DuelLiveLog
from ygotrainingbot.models import DuelTrace, GameAction, MatchResult, SelectCardPrompt, VisibleGameState


class EdoproError(RuntimeError):
    """Base error for EDOPro integration failures."""


class EdoproGatewayError(EdoproError):
    """Raised when the headless gateway sends invalid data or exits early."""


class EdoproGatewayTimeout(EdoproGatewayError):
    """Raised when the headless gateway does not produce a result in time."""


# Cap each wait for the next gateway line so a blocking ocgcore CONTINUE cannot
# hold the whole training job until the duel-level timeout expires.
PER_READLINE_TIMEOUT_SECONDS = 90.0


@dataclass(frozen=True, slots=True)
class EdoproInstall:
    """Paths for a local EDOPro installation or extracted data directory."""

    root: Path
    executable: Path | None = None
    script_dir: Path | None = None
    deck_dir: Path | None = None
    replay_dir: Path | None = None
    database_paths: tuple[Path, ...] = ()

    @classmethod
    def from_environment(cls) -> "EdoproInstall":
        """Build install paths from common environment variables."""

        root = Path(os.environ.get("EDOPRO_HOME", ".")).expanduser()
        executable = os.environ.get("EDOPRO_BIN")
        return cls(
            root=root,
            executable=Path(executable).expanduser() if executable else None,
        ).with_defaults()

    def with_defaults(self) -> "EdoproInstall":
        """Fill standard EDOPro subdirectories relative to ``root``."""

        database_paths = self.database_paths or tuple(
            path
            for path in (self.root / "cards.cdb", self.root / "expansions")
            if path.exists()
        )
        return EdoproInstall(
            root=self.root,
            executable=self.executable,
            script_dir=self.script_dir or self.root / "script",
            deck_dir=self.deck_dir or self.root / "deck",
            replay_dir=self.replay_dir or self.root / "replay",
            database_paths=database_paths,
        )

    def validation_errors(self) -> tuple[str, ...]:
        """Return missing or suspicious paths without raising."""

        install = self.with_defaults()
        errors: list[str] = []
        if not install.root.exists():
            errors.append(f"EDOPro root does not exist: {install.root}")
        if install.executable is not None and not install.executable.exists():
            errors.append(f"EDOPro executable does not exist: {install.executable}")
        if not install.script_dir or not install.script_dir.exists():
            errors.append(f"EDOPro script directory does not exist: {install.script_dir}")
        if not install.deck_dir or not install.deck_dir.exists():
            errors.append(f"EDOPro deck directory does not exist: {install.deck_dir}")
        if not install.database_paths:
            errors.append("No EDOPro card database path found; expected cards.cdb or expansions.")
        return tuple(errors)

    def validate(self) -> None:
        """Raise if required EDOPro paths are missing."""

        errors = self.validation_errors()
        if errors:
            raise EdoproError("\n".join(errors))


@dataclass(frozen=True, slots=True)
class EdoproGatewayConfig:
    """Configuration for a headless EDOPro gateway subprocess."""

    command: tuple[str, ...]
    working_directory: Path | None = None
    startup_payload: dict[str, Any] | None = None
    timeout_seconds: float = 30.0

    @classmethod
    def from_shell_words(
        cls,
        command: Sequence[str],
        *,
        working_directory: Path | None = None,
        timeout_seconds: float = 30.0,
        startup_payload: dict[str, Any] | None = None,
    ) -> "EdoproGatewayConfig":
        if not command:
            raise ValueError("gateway command cannot be empty.")
        return cls(
            command=tuple(command),
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            startup_payload=startup_payload,
        )


class JsonLineEdoproSimulator:
    """Drive a headless EDOPro gateway using JSON lines.

    Gateway protocol:

    - Bot sends: ``{"type": "start_duel", "players": ["a", "b"], ...}``
    - Gateway sends state messages:
      ``{"type": "state", "state": {"state_id": "...", "legal_actions": [...]}}``
    - Bot replies: ``{"type": "action", "state_id": "...", "action_id": "..."}``
    - Gateway sends final result:
      ``{"type": "result", "winner": "a", "loser": "b", "turns": 4}``
    """

    def __init__(self, config: EdoproGatewayConfig) -> None:
        self._config = config

    def play(
        self,
        first_player: DuelAgent,
        second_player: DuelAgent,
        *,
        deck_a: Sequence[int] | None = None,
        deck_b: Sequence[int] | None = None,
        extra_a: Sequence[int] | None = None,
        extra_b: Sequence[int] | None = None,
        side_a: Sequence[int] | None = None,
        side_b: Sequence[int] | None = None,
        seed: Sequence[int] | None = None,
    ) -> MatchResult:
        started_at = time.monotonic()
        process = subprocess.Popen(
            self._config.command,
            cwd=self._config.working_directory,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        traces: list[DuelTrace] = []
        gateway_logs: list[object] = []
        line_queue = _start_stdout_reader(process)
        _start_stderr_reader(process, gateway_logs)

        try:
            start_payload: dict[str, Any] = {
                "type": "start_duel",
                "players": [first_player.name, second_player.name],
                **(self._config.startup_payload or {}),
            }
            if deck_a is not None:
                start_payload["deck_a"] = list(deck_a)
            if deck_b is not None:
                start_payload["deck_b"] = list(deck_b)
            if extra_a:
                start_payload["extra_a"] = list(extra_a)
            if extra_b:
                start_payload["extra_b"] = list(extra_b)
            if side_a:
                start_payload["side_a"] = list(side_a)
            if side_b:
                start_payload["side_b"] = list(side_b)
            if seed is not None:
                start_payload["seed"] = [str(part) for part in seed]
            startup = self._config.startup_payload or {}
            if startup.get("max_decisions") is not None:
                start_payload["max_decisions"] = int(startup["max_decisions"])
            if startup.get("max_duel_turns") is not None:
                start_payload["max_duel_turns"] = int(startup["max_duel_turns"])
            self._send(process, start_payload)
            agents = {first_player.name: first_player, second_player.name: second_player}
            progress_deadline = time.monotonic() + PER_READLINE_TIMEOUT_SECONDS

            while True:
                self._ensure_time_remaining(started_at)
                message = self._read_progress_message(
                    process,
                    line_queue,
                    started_at,
                    gateway_logs,
                    progress_deadline,
                )
                message_type = message.get("type")

                if message_type == "state":
                    progress_deadline = time.monotonic() + PER_READLINE_TIMEOUT_SECONDS
                    state = _state_from_payload(message.get("state"))
                    try:
                        agent = agents[state.active_player]
                    except KeyError as exc:
                        raise EdoproGatewayError(
                            f"Gateway requested unknown active player {state.active_player!r}."
                        ) from exc
                    action = agent.choose_action(state)
                    if action not in state.legal_actions:
                        raise EdoproGatewayError(
                            f"Agent {agent.name!r} chose illegal action {action.action_id!r}."
                        )
                    explanation = ""
                    traces.append(
                        DuelTrace(
                            state=state,
                            action=action,
                            agent_name=agent.name,
                            note=explanation,
                        )
                    )
                    if live_log is not None:
                        live_log.duel_decision(
                            decision_index=state.decision_index,
                            agent=agent.name,
                            action_id=action.action_id,
                            label=action.label,
                            summary=state.summary,
                        )
                    self._send(
                        process,
                        {
                            "type": "action",
                            "state_id": state.state_id,
                            "agent": agent.name,
                            "action_id": action.action_id,
                        },
                    )
                    continue

                if message_type == "result":
                    return _result_from_payload(message, traces, gateway_logs)

                raise EdoproGatewayError(f"Unknown gateway message type: {message_type!r}")
        finally:
            _terminate_process(process)

    def _ensure_time_remaining(self, started_at: float) -> None:
        if time.monotonic() - started_at > self._config.timeout_seconds:
            raise EdoproGatewayTimeout(
                f"EDOPro gateway exceeded {self._config.timeout_seconds:.1f}s timeout "
                f"waiting for the next JSON line."
            )

    def _read_progress_message(
        self,
        process: subprocess.Popen[str],
        line_queue: "queue.Queue[str | None]",
        started_at: float,
        gateway_logs: list[object],
        progress_deadline: float,
    ) -> dict[str, Any]:
        while True:
            if time.monotonic() >= progress_deadline:
                raise EdoproGatewayTimeout(
                    f"EDOPro gateway produced no state/result for "
                    f"{PER_READLINE_TIMEOUT_SECONDS:.0f}s (likely ocgcore engine stall)."
                )
            duel_remaining = self._config.timeout_seconds - (time.monotonic() - started_at)
            if duel_remaining <= 0:
                self._ensure_time_remaining(started_at)
            wait_seconds = min(
                5.0,
                duel_remaining,
                max(0.05, progress_deadline - time.monotonic()),
            )
            try:
                line = line_queue.get(timeout=wait_seconds)
            except queue.Empty:
                continue

            if not line:
                stderr = process.stderr.read() if process.stderr else ""
                if not stderr.strip():
                    stderr_lines = [
                        entry.get("line", entry)
                        for entry in gateway_logs
                        if isinstance(entry, dict) and entry.get("type") == "stderr"
                    ]
                    stderr = "\n".join(str(item) for item in stderr_lines[-20:])
                detail = stderr.strip() or "(no stderr)"
                raise EdoproGatewayError(
                    f"EDOPro gateway exited before producing a result. stderr: {detail}"
                )

            message = _decode_message(line)
            message_type = message.get("type")
            if message_type == "log":
                gateway_logs.append(message.get("message", ""))
                continue
            if message_type in {"state", "result"}:
                return message
            raise EdoproGatewayError(f"Unknown gateway message type: {message_type!r}")

    def _send(self, process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise EdoproGatewayError("Gateway stdin pipe was not available.")
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()


def _start_stderr_reader(process: subprocess.Popen[str], gateway_logs: list[object]) -> None:
    if process.stderr is None:
        return

    def read_lines() -> None:
        try:
            for line in process.stderr:
                text = line.rstrip()
                if text:
                    gateway_logs.append({"type": "stderr", "line": text})
        except Exception:
            return

    threading.Thread(target=read_lines, daemon=True).start()


def _start_stdout_reader(process: subprocess.Popen[str]) -> "queue.Queue[str | None]":
    if process.stdout is None:
        raise EdoproGatewayError("Gateway stdout pipe was not available.")
    line_queue: queue.Queue[str | None] = queue.Queue()

    def read_lines() -> None:
        try:
            for line in process.stdout:
                line_queue.put(line)
        finally:
            line_queue.put(None)

    threading.Thread(target=read_lines, daemon=True).start()
    return line_queue


def _decode_message(line: str) -> dict[str, Any]:
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise EdoproGatewayError(f"Gateway emitted invalid JSON: {line!r}") from exc
    if not isinstance(message, dict):
        raise EdoproGatewayError(f"Gateway emitted non-object message: {message!r}")
    return message


def _state_from_payload(payload: Any) -> VisibleGameState:
    if not isinstance(payload, dict):
        raise EdoproGatewayError("State message missing object payload.")

    legal_actions = tuple(_action_from_payload(action) for action in payload.get("legal_actions", ()))
    if payload.get("decision_index") is not None:
        decision_index = int(payload["decision_index"])
        duel_turn = int(payload.get("duel_turn", payload.get("turn", 1)))
    else:
        decision_index = int(payload.get("turn", 0))
        duel_turn = 1
    return VisibleGameState(
        state_id=str(payload["state_id"]),
        turn=duel_turn,
        active_player=str(payload["active_player"]),
        summary=str(payload.get("summary", "")),
        legal_actions=legal_actions,
        public_zones=_string_sequence_mapping(payload.get("public_zones", {})),
        decision_index=decision_index,
        select_card=_select_card_from_payload(payload.get("select_card")),
    )


def _select_card_from_payload(payload: Any) -> SelectCardPrompt | None:
    if not isinstance(payload, dict):
        return None
    cards: list[tuple[int, str]] = []
    for entry in payload.get("cards", ()):
        if not isinstance(entry, dict):
            continue
        cards.append((int(entry.get("index", len(cards))), str(entry.get("name", ""))))
    pick_count = int(payload.get("pick_count", payload.get("min", 1)))
    return SelectCardPrompt(
        pick_count=pick_count,
        min_picks=int(payload.get("min", pick_count)),
        max_picks=int(payload.get("max", pick_count)),
        can_cancel=bool(payload.get("can_cancel")),
        cards=tuple(cards),
    )


def _action_from_payload(payload: Any) -> GameAction:
    if not isinstance(payload, dict):
        raise EdoproGatewayError(f"Legal action is not an object: {payload!r}")
    expected_value = payload.get("expected_value")
    return GameAction(
        action_id=str(payload["action_id"]),
        label=str(payload.get("label", payload["action_id"])),
        expected_value=float(expected_value) if expected_value is not None else None,
        tags=tuple(str(tag) for tag in payload.get("tags", ())),
    )


def _string_sequence_mapping(payload: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): tuple(str(item) for item in value)
        for key, value in payload.items()
        if isinstance(value, Iterable) and not isinstance(value, str)
    }


def _result_from_payload(
    payload: dict[str, Any],
    traces: list[DuelTrace],
    gateway_logs: list[object],
) -> MatchResult:
    winner = payload.get("winner")
    loser = payload.get("loser")
    return MatchResult(
        winner=str(winner) if winner is not None else None,
        loser=str(loser) if loser is not None else None,
        turns=int(payload.get("turns", 0)),
        traces=tuple(traces),
        tags=tuple(str(tag) for tag in payload.get("tags", ("edopro",))),
        metadata={
            "gateway_logs": tuple(gateway_logs),
            "end_reason": payload.get("end_reason"),
            "life_points": payload.get("life_points"),
            "decisions": payload.get("decisions"),
            "script_stats": payload.get("script_stats"),
        },
    )


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
