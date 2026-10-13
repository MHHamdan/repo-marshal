"""Plan assembly (owner: lead). Wires git -> policy -> inventory -> signals -> decisions ->
context -> skills into a hashed Plan (architecture §4.2)."""

from __future__ import annotations

from pathlib import Path

from repomarshal.model import Plan


def compile_plan(
    repo: Path, task: str | None = None, base: str | None = None, path_env: str | None = None
) -> Plan:
    """Compile the Change Plan for the current working tree.

    mode = BINDING when the change set has files, FORECAST otherwise. Raises PolicyInvalid,
    GitError, NotAGitRepository (callers map these to BLOCKED).
    """
    raise NotImplementedError
