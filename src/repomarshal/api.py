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

import dataclasses
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from repomarshal import __version__, anchor, evidence, git, planner, runner, verdict
from repomarshal import waivers as waivers_mod
from repomarshal.model import (
    Cost,
    DecisionStatus,
    EvidenceRecord,
    GateResult,
    Plan,
    PolicyInvalid,
    ResultStatus,
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
    root = git.repo_root(repo)
    base_sha, _ = anchor.effective_base(root, base)
    return planner.compile_plan(root, task=task, base=base_sha, path_env=_path_env(env))


def _record(
    root: Path, p: Plan, results, before: str, after: str, v, reasons, granted=()
) -> DoneResult:
    record = EvidenceRecord(
        plan_hash=p.plan_hash,
        base=p.change.base,
        head=p.change.head,
        tree_digest_before=before,
        tree_digest_after=after,
        policy_digest=p.policy.digest,
        results=tuple(results),
        verdict=v,
        verdict_reasons=tuple(reasons),
        repomarshal_version=__version__,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        waivers=tuple(granted),
    ).with_digest()
    evidence.write_plan(root, p)
    path = evidence.write_record(root, record)
    return DoneResult(v, tuple(reasons), p, record, path)


def _nested_problem(root: Path) -> str | None:
    """A repository the agent created inside another project (v0.2 fail-open review #3).

    If `root` sits inside an outer git work tree and the outer project sees it as a new,
    untracked (not ignored) directory, verifying it would judge a throwaway repository instead
    of the project. Registered submodules (tracked gitlinks) and ignored directories are fine.
    """
    outer_r = git._git(root.parent, "rev-parse", "--show-toplevel", check=False)
    if outer_r.returncode != 0 or not outer_r.stdout.strip():
        return None
    outer = Path(outer_r.stdout.strip()).resolve()
    rel = root.relative_to(outer).as_posix() if root.is_relative_to(outer) else None
    if rel is None:
        return None
    st = git._git(
        outer, "status", "--porcelain", "--untracked-files=normal", "--", rel, check=False
    )
    if any(line.startswith("??") for line in st.stdout.splitlines()):
        return (
            f"{root} is a git repository nested, untracked, inside the project {outer}; "
            "verify the project itself"
        )
    return None


def done(
    repo: str | Path,
    task: str | None = None,
    base: str | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
    waivers: str = "none",
) -> DoneResult:
    root = git.repo_root(repo)
    nested = _nested_problem(root)
    if nested:
        return DoneResult(Verdict.UNKNOWN, (nested,), None, None, None)
    base_sha, base_how = anchor.effective_base(root, base)
    before = git.tree_digest(root, base=base_sha)
    try:
        p = planner.compile_plan(root, task=task, base=base_sha, path_env=_path_env(env))
    except PolicyInvalid as e:
        return DoneResult(
            Verdict.BLOCKED,
            tuple(f"invalid repomarshal.toml at BASE: {err}" for err in e.errors),
            None,
            None,
            None,
        )
    except ValueError as e:  # e.g. duplicate gate ids from the inventory: fail closed
        return DoneResult(
            Verdict.UNKNOWN, (f"could not compile a trustworthy plan: {e}",), None, None, None
        )
    if not dry_run:
        anchor.remember(root, base_sha, explicit=base is not None)

    # Fail-open review #1: with no change there is nothing to verify, whatever the task says.
    if not p.change.files:
        reasons = (
            "nothing was verified: no change relative to BASE "
            f"{p.change.base or '(none)'} ({base_how})",
        )
        if dry_run:
            return DoneResult(Verdict.UNKNOWN, reasons, p, None, None)
        return _record(root, p, (), before, before, Verdict.UNKNOWN, reasons)

    # Fail-open review #11: ids must identify gates uniquely, or one gate could stand in for
    # another. Duplicates are an internal inconsistency: fail closed.
    ids = [g.id for g in p.inventory]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        reasons = (f"duplicate gate ids {duplicates}: cannot tell which gate a result belongs to",)
        if dry_run:
            return DoneResult(Verdict.UNKNOWN, reasons, p, None, None)
        return _record(root, p, (), before, before, Verdict.UNKNOWN, reasons)

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
    tree_changed = before != p.change.tree_digest
    for d in required:
        gate = inv.get(d.gate_id)
        if gate is None:
            continue  # verdict.fold reports a REQUIRED gate without a result as UNKNOWN
        result = runner.run_gate(root, gate, p.change, env)
        if d.taint:
            # v0.2 tamper signals: the change weakens what this gate runs (deleted or skipped
            # tests, narrowed discovery), so its result cannot vouch for the change. Taint only
            # ever DOWNGRADES a PASS; a FAIL stays FAIL (v0.2 fail-open review #6).
            taint = "; ".join(t.text for t in d.taint)
            if result.status in (ResultStatus.PASS, ResultStatus.WAIVED):
                result = dataclasses.replace(
                    result,
                    status=ResultStatus.UNKNOWN,
                    reason=f"{taint}; was: {result.status.value} ({result.reason})",
                )
            else:
                result = dataclasses.replace(result, reason=f"{result.reason}; also: {taint}")
        # Fail-open review #3: check the tree after EVERY gate. A gate that swaps files for later
        # gates (and a final gate that restores them) would otherwise go unnoticed.
        now = git.tree_digest(root, base=base_sha)
        if now != before:
            tree_changed = True
        if now != before and result.status in (ResultStatus.PASS, ResultStatus.WAIVED):
            result = dataclasses.replace(
                result,
                status=ResultStatus.UNKNOWN,
                reason=f"gate modified the working tree (digest {before[:12]} -> {now[:12]}); "
                f"was: {result.status.value} ({result.reason})",
            )
        results.append(result)
    after = git.tree_digest(root, base=base_sha)
    tree_changed = tree_changed or before != after
    # v0.3 human override: only the caller decides where waivers may come from (the hook and the
    # MCP server pass "none"); waivers.collect decides which ones are genuine.
    granted: tuple = ()
    waiver_notes: tuple[str, ...] = ()
    if waivers != "none":
        granted, waiver_notes = waivers_mod.collect_report(root, p, waivers, env)
    v, reasons = verdict.fold(
        p, tuple(results), tree_changed, p.policy.waivers_permit_pass, granted
    )
    reasons = tuple(reasons) + tuple(f"waivers: {n}" for n in waiver_notes)
    return _record(root, p, results, before, after, v, reasons, granted)
