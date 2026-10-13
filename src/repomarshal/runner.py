"""Gate execution (owner: lead; roadmap M11; architecture §6, §7.6).

Command gates: subprocess.run(argv, cwd=repo/cwd, shell=False, timeout=timeout_s, env=env) in a
new process group (killed on timeout). exit 0 -> PASS, non-zero -> FAIL, timeout / cannot start
-> UNKNOWN. Output: sha256 of full stdout/stderr, last 40 lines redacted. Native gates dispatch to
repomarshal.native. A gate with definition_changed=True yields UNKNOWN regardless of exit code
(M14), with the exit code still recorded.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from repomarshal.model import ChangeSet, Gate, GateResult

TAIL_LINES = 40


def run_gate(
    repo: Path, gate: Gate, change: ChangeSet, env: Mapping[str, str] | None = None
) -> GateResult:
    raise NotImplementedError


def redact(text: str) -> str:
    """Replace secrets (tokens, keys, Authorization headers, URL passwords) with [REDACTED]."""
    raise NotImplementedError
