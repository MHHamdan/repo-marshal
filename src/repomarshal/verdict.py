"""Verdict folding (owner: lead; architecture §6; product-definition "Output").

Precedence BLOCKED > FAIL > UNKNOWN > PASS (BLOCKED is decided by the caller before folding).
- REQUIRED decisions fold in their GateResult; a REQUIRED decision without a result is UNKNOWN.
- UNAVAILABLE decisions fold in as UNKNOWN.
- SKIPPED / NOT_APPLICABLE never count, in either direction.
- No REQUIRED and no UNAVAILABLE decision -> UNKNOWN ("nothing was verified").
- Tree changed during the run -> at least UNKNOWN.
- Any ChangeSet.opaque path (content RepoMarshal could not inspect) -> at least UNKNOWN.
- WAIVED counts as PASS only when the policy permits; otherwise UNKNOWN.
Fail closed: anything unrecognised is UNKNOWN, never PASS.
"""

from __future__ import annotations

from repomarshal.model import DecisionStatus, GateResult, Plan, ResultStatus, Verdict


def fold(
    plan: Plan,
    results: tuple[GateResult, ...],
    tree_changed: bool,
    waivers_permit_pass: bool = False,
) -> tuple[Verdict, tuple[str, ...]]:
    by_id = {r.gate_id: r for r in results}
    fails: list[str] = []
    unknowns: list[str] = []
    passes = 0
    counted = 0
    for d in plan.gates:
        label = d.gate_id or f"{d.kind.value} (no gate)"
        if d.status == DecisionStatus.UNAVAILABLE:
            counted += 1
            why = d.reasons[-1].text if d.reasons else "unavailable"
            unknowns.append(f"{label}: required but unavailable ({why})")
            continue
        if d.status != DecisionStatus.REQUIRED:
            continue
        counted += 1
        r = by_id.get(d.gate_id) if d.gate_id else None
        if r is None:
            unknowns.append(f"{label}: required but no result was produced")
        elif r.status == ResultStatus.FAIL:
            fails.append(f"{label}: FAIL ({r.reason})")
        elif r.status == ResultStatus.PASS:
            passes += 1
        elif r.status == ResultStatus.WAIVED and waivers_permit_pass:
            passes += 1
        elif r.status == ResultStatus.WAIVED:
            unknowns.append(f"{label}: waived, and policy does not let waivers count as PASS")
        else:
            unknowns.append(f"{label}: UNKNOWN ({r.reason})")
    for path in plan.change.opaque:
        unknowns.append(f"content not inspectable: {path}")
    if tree_changed:
        unknowns.append(
            "working tree changed during verification; evidence does not match one tree"
        )
    if counted == 0:
        unknowns.append("nothing was verified: the plan has no required gates")
    if fails:
        return Verdict.FAIL, tuple(fails + unknowns)
    if unknowns:
        return Verdict.UNKNOWN, tuple(unknowns)
    if passes == 0:  # defensive; unreachable when counted > 0 and nothing failed or was unknown
        return Verdict.UNKNOWN, ("nothing was verified",)
    return Verdict.PASS, (f"all {passes} required gates passed for tree {plan.change.tree_digest}",)
