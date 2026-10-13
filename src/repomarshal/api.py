"""Public Python API (owner: lead). The acceptance tests (agent "fixture") code against this and
the CLI only.

    plan(repo, task=None, base=None, env=None) -> Plan
        Compile the plan. Never executes repository code. Raises PolicyInvalid / GitError /
        NotAGitRepository.

    done(repo, task=None, base=None, env=None, dry_run=False) -> DoneResult
        1. tree_digest_before; 2. compile the binding plan; 3. run every REQUIRED gate (cheap
        first, all of them; no fail-fast); 4. tree_digest_after; 5. fold the verdict; 6. write the
        evidence record under .repomarshal/evidence/ (not when dry_run). PolicyInvalid ->
        DoneResult with verdict BLOCKED and no gates run. dry_run: no gate runs, verdict UNKNOWN,
        result lists the gates that would run.

`env` is passed to gate subprocesses and its PATH is used for tool availability (tests use it to
put stub executables on PATH). None means os.environ.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from repomarshal.model import EvidenceRecord, Plan, Verdict


@dataclass(frozen=True)
class DoneResult:
    verdict: Verdict
    reasons: tuple[str, ...]
    plan: Plan | None  # None when BLOCKED before a plan could be compiled
    record: EvidenceRecord | None  # None on dry_run or BLOCKED-before-plan
    evidence_path: Path | None
    would_run: tuple[str, ...] = ()  # gate ids (dry_run only)


def plan(
    repo: str | Path,
    task: str | None = None,
    base: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Plan:
    raise NotImplementedError


def done(
    repo: str | Path,
    task: str | None = None,
    base: str | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
) -> DoneResult:
    raise NotImplementedError
