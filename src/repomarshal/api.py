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

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from repomarshal import __version__, evidence, git, planner, runner, verdict
from repomarshal.model import (
    Cost,
    DecisionStatus,
    EvidenceRecord,
    GateResult,
    Plan,
    PolicyInvalid,
    Verdict,
)

_COST_ORDER = {Cost.CHEAP: 0, Cost.STANDARD: 1, Cost.EXPENSIVE: 2}


@dataclass(frozen=True)
class DoneResult:
    verdict: Verdict
    reasons: tuple[str, ...]
    plan: Plan | None  # None when BLOCKED before a plan could be compiled
    record: EvidenceRecord | None  # None on dry_run or BLOCKED-before-plan
    evidence_path: Path | None
    would_run: tuple[str, ...] = ()  # gate ids (dry_run only)


def _path_env(env: Mapping[str, str] | None) -> str | None:
    return (env if env is not None else os.environ).get("PATH")


def plan(
    repo: str | Path,
    task: str | None = None,
    base: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Plan:
    return planner.compile_plan(Path(repo), task=task, base=base, path_env=_path_env(env))


def done(
    repo: str | Path,
    task: str | None = None,
    base: str | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
) -> DoneResult:
    root = git.repo_root(repo)
    before = git.tree_digest(root)
    try:
        p = planner.compile_plan(root, task=task, base=base, path_env=_path_env(env))
    except PolicyInvalid as e:
        return DoneResult(
            Verdict.BLOCKED,
            tuple(f"invalid repomarshal.toml at BASE: {err}" for err in e.errors),
            None,
            None,
            None,
        )
    inv = {g.id: g for g in p.inventory}
    required = [d for d in p.gates if d.status == DecisionStatus.REQUIRED and d.gate_id]
    required.sort(
        key=lambda d: (
            (_COST_ORDER.get(inv[d.gate_id].cost, 3), d.gate_id)
            if d.gate_id in inv
            else (9, d.gate_id)
        )
    )
    if dry_run:
        return DoneResult(
            Verdict.UNKNOWN,
            ("dry run: no gates were executed",),
            p,
            None,
            None,
            would_run=tuple(d.gate_id for d in required),
        )
    results: list[GateResult] = []
    for d in required:
        gate = inv.get(d.gate_id)
        if gate is None:
            continue  # verdict.fold reports a REQUIRED gate without a result as UNKNOWN
        results.append(runner.run_gate(root, gate, p.change, env))
    after = git.tree_digest(root)
    tree_changed = before != after or before != p.change.tree_digest
    v, reasons = verdict.fold(p, tuple(results), tree_changed)
    record = EvidenceRecord(
        plan_hash=p.plan_hash,
        base=p.change.base,
        head=p.change.head,
        tree_digest_before=before,
        tree_digest_after=after,
        policy_digest=p.policy.digest,
        results=tuple(results),
        verdict=v,
        verdict_reasons=reasons,
        repomarshal_version=__version__,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    ).with_digest()
    evidence.write_plan(root, p)
    path = evidence.write_record(root, record)
    return DoneResult(v, reasons, p, record, path)
