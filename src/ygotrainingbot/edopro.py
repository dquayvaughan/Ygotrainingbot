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
from ygotrainingbot.models import DuelTrace, GameAction, MatchResult, VisibleGameState


class EdoproError(RuntimeError):
    """Base error for EDOPro integration failures."""


class EdoproGatewayError(EdoproError):
    """Raised when the headless gateway sends invalid data or exits early."""


class EdoproGatewayTimeout(EdoproGatewayError):
    """Raised when the headless gateway does not produce a result in time."""


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
        )
        traces: list[DuelTrace] = []
        gateway_logs: list[object] = []
        line_queue = _start_stdout_reader(process)

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
            if seed is not None:
                start_payload["seed"] = [str(part) for part in seed]
            self._send(process, start_payload)
            agents = {first_player.name: first_player, second_player.name: second_player}

            while True:
                self._ensure_time_remaining(started_at)
                line = self._readline(process, line_queue, started_at)
                message = _decode_message(line)
                message_type = message.get("type")

                if message_type == "state":
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
                    explain = getattr(agent, "explain_decision", None)
                    if callable(explain):
                        explanation = str(explain(state, action))
                    traces.append(
                        DuelTrace(
                            state=state,
                            action=action,
                            agent_name=agent.name,
                            note=explanation,
                        )
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

                if message_type == "log":
                    gateway_logs.append(message.get("message", ""))
                    continue

                raise EdoproGatewayError(f"Unknown gateway message type: {message_type!r}")
        finally:
            _terminate_process(process)

    def _ensure_time_remaining(self, started_at: float) -> None:
        if time.monotonic() - started_at > self._config.timeout_seconds:
            raise EdoproGatewayTimeout(
                f"EDOPro gateway exceeded {self._config.timeout_seconds:.1f}s timeout."
            )

    def _readline(
        self,
        process: subprocess.Popen[str],
        line_queue: "queue.Queue[str | None]",
        started_at: float,
    ) -> str:
        remaining = self._config.timeout_seconds - (time.monotonic() - started_at)
        if remaining <= 0:
            self._ensure_time_remaining(started_at)
        try:
            line = line_queue.get(timeout=remaining)
        except queue.Empty as exc:
            self._ensure_time_remaining(started_at)
            raise EdoproGatewayTimeout("EDOPro gateway did not emit data before timeout.") from exc

        if line:
            return line

        stderr = process.stderr.read() if process.stderr else ""
        raise EdoproGatewayError(
            f"EDOPro gateway exited before producing a result. stderr: {stderr.strip()}"
        )

    def _send(self, process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise EdoproGatewayError("Gateway stdin pipe was not available.")
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()


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
    return VisibleGameState(
        state_id=str(payload["state_id"]),
        turn=int(payload.get("turn", 0)),
        active_player=str(payload["active_player"]),
        summary=str(payload.get("summary", "")),
        legal_actions=legal_actions,
        public_zones=_string_sequence_mapping(payload.get("public_zones", {})),
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
