"""Install and patch the headless EDOPro ocgcore gateway."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import IO, TextIO


def gateway_dir(repo_root: Path) -> Path:
    return (repo_root / "gateways" / "edopro-ocgcore").resolve()


def ensure_gateway_dependencies(
    repo_root: Path,
    *,
    log: IO[str] | TextIO | None = None,
) -> None:
    """Ensure npm deps exist and the SELECT_CARD ocgcore patch is applied."""

    root = repo_root.resolve()
    prefix = gateway_dir(root)
    node_modules = prefix / "node_modules"
    if not node_modules.is_dir():
        command = ["npm", "ci", "--prefix", str(prefix)]
        _log_command(log, command)
        subprocess.run(
            command,
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
    patch_script = prefix / "patch-ocgcore-select.mjs"
    if patch_script.is_file():
        command = ["node", str(patch_script)]
        _log_command(log, command)
        subprocess.run(
            command,
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )


def _log_command(log: IO[str] | TextIO | None, command: list[str]) -> None:
    if log is None:
        return
    log.write("$ " + " ".join(command) + "\n")
    log.flush()
