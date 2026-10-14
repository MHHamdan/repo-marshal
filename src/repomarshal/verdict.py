"""Verdict folding (owner: lead; architecture §6; product-definition "Output").

Precedence BLOCKED > FAIL > UNKNOWN > PASS (BLOCKED is decided by the caller before folding).
- REQUIRED decisions fold in their GateResult; a REQUIRED decision without a result is UNKNOWN.
- UNAVAILABLE decisions fold in as UNKNOWN.
- SKIPPED / NOT_APPLICABLE never count, in either direction.
- No REQUIRED and no UNAVAILABLE decision -> UNKNOWN ("nothing was verified").
- Tree changed during the run -> at least UNKNOWN.
- Any ChangeSet.opaque path (content RepoMarshal could not inspect) -> at least UNKNOWN.
- WAIVED counts as PASS only when the policy permits; otherwise UNKNOWN.
- v0.3 waivers (model.Waiver) apply to one decision (by gate id, or by kind for a kind-level
  decision) whose outcome is UNKNOWN or UNAVAILABLE. A FAIL is never waivable, and neither are
  "tree changed", opaque content or "nothing verified".
Fail closed: anything unrecognised is UNKNOWN, never PASS.
"""

from __future__ import annotations

from repomarshal.model import (
    DecisionStatus,
    GateDecision,
    GateResult,
    Plan,
    ResultStatus,
    Verdict,
    Waiver,
)


def _waiver_for(d: GateDecision, waivers: tuple[Waiver, ...]) -> Waiver | None:
    for w in waivers:
        if d.gate_id is not None and w.target == d.gate_id:
            return w
        if d.gate_id is None and w.target == d.kind.value:
            return w
    return None


def fold(
    plan: Plan,
    results: tuple[GateResult, ...],
    tree_changed: bool,
    waivers_permit_pass: bool = False,
    waivers: tuple[Waiver, ...] = (),
) -> tuple[Verdict, tuple[str, ...]]:
    by_id = {r.gate_id: r for r in results}
    fails: list[str] = []
    unknowns: list[str] = []
    passes = 0
    counted = 0
    executed = 0  # gates RepoMarshal ran that passed on their own, without a waiver
    waived: list[str] = []
    for d in plan.gates:
        label = d.gate_id or f"{d.kind.value} (no gate)"
        waiver = _waiver_for(d, waivers)
        if d.status == DecisionStatus.UNAVAILABLE:
            counted += 1
            why = d.reasons[-1].text if d.reasons else "unavailable"
            if waiver is not None and waivers_permit_pass:
                passes += 1
                waived.append(
                    f"{label}: unavailable, WAIVED by {waiver.identity} "
                    f"({waiver.source.value}): {waiver.reason}"
                )
            elif waiver is not None:
                unknowns.append(
                    f"{label}: required but unavailable ({why}); waived by "
                    f"{waiver.identity}, but policy does not let waivers count as PASS"
                )
            else:
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
            executed += 1
        elif r.status in (ResultStatus.UNKNOWN, ResultStatus.WAIVED) and waiver is not None:
            if waivers_permit_pass:
                passes += 1
                waived.append(
                    f"{label}: {r.status.value} ({r.reason}), WAIVED by "
                    f"{waiver.identity} ({waiver.source.value}): {waiver.reason}"
                )
            else:
                unknowns.append(
                    f"{label}: UNKNOWN ({r.reason}); waived by {waiver.identity}, "
                    "but policy does not let waivers count as PASS"
                )
        elif r.status == ResultStatus.WAIVED:
            unknowns.append(f"{label}: marked WAIVED without a recorded waiver")
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
    elif executed == 0 and not fails:
        # v0.3 fail-open review #3: a waiver accepts one gap, it is not evidence. A PASS needs at
        # least one gate that actually ran and passed; "nothing verified" is never waivable.
        unknowns.append("nothing was verified: no required gate ran and passed (waivers alone)")
    if fails:
        return Verdict.FAIL, tuple(fails + unknowns + waived)
    if unknowns:
        return Verdict.UNKNOWN, tuple(unknowns + waived)
    if passes == 0:  # defensive; unreachable when counted > 0 and nothing failed or was unknown
        return Verdict.UNKNOWN, ("nothing was verified",)
    passed = f"all {passes} required gates passed for tree {plan.change.tree_digest}"
    if waived:
        return Verdict.PASS, (f"{passed}, {len(waived)} of them by human waiver", *waived)
    return Verdict.PASS, (passed,)
