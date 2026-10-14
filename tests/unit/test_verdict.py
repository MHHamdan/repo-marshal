"""Verdict folding tests (owner: lead): precedence and fail-closed rules."""

import dataclasses

from repomarshal.model import (
    ChangeSet,
    ContextPlan,
    GateDecision,
    GateKind,
    GateResult,
    Plan,
    PlanMode,
    PolicyRef,
    Reason,
    Risk,
    Verdict,
)
from repomarshal.model import (
    DecisionStatus as D,
)
from repomarshal.model import (
    ResultStatus as R,
)
from repomarshal.verdict import fold


def plan(*decisions):
    return Plan(
        mode=PlanMode.BINDING,
        task=None,
        change=ChangeSet(None, None, "0" * 40, (), False),
        signals=(),
        domains=(),
        risk=Risk.LOW,
        inventory=(),
        gates=tuple(decisions),
        context=ContextPlan((), (), 1, 0, 0),
        skills=(),
        evidence_level="standard",
        policy=PolicyRef("default", "sha256:" + "0" * 64),
        generator={"name": "t", "version": "0"},
    )


def dec(gid, status=D.REQUIRED, kind=GateKind.UNIT):
    return GateDecision(kind, status, (Reason("because"),), gid)


def res(gid, status, kind=GateKind.UNIT):
    return GateResult(gid, kind, status, "r")


def test_all_pass():
    v, _ = fold(plan(dec("a"), dec("b")), (res("a", R.PASS), res("b", R.PASS)), False)
    assert v is Verdict.PASS


def test_fail_beats_unknown():
    v, reasons = fold(plan(dec("a"), dec("b")), (res("a", R.FAIL), res("b", R.UNKNOWN)), False)
    assert v is Verdict.FAIL and len(reasons) == 2


def test_unknown_result():
    v, _ = fold(plan(dec("a"), dec("b")), (res("a", R.PASS), res("b", R.UNKNOWN)), False)
    assert v is Verdict.UNKNOWN


def test_missing_result_for_required_gate_is_unknown():
    v, reasons = fold(plan(dec("a"), dec("b")), (res("a", R.PASS),), False)
    assert v is Verdict.UNKNOWN and any("no result" in r for r in reasons)


def test_unavailable_decision_is_unknown():
    v, _ = fold(
        plan(dec("a"), dec(None, D.UNAVAILABLE, GateKind.SECURITY_SCAN)), (res("a", R.PASS),), False
    )
    assert v is Verdict.UNKNOWN


def test_skipped_and_not_applicable_do_not_count():
    p = plan(dec("a", D.SKIPPED), dec(None, D.NOT_APPLICABLE, GateKind.LINT))
    v, reasons = fold(p, (), False)
    assert v is Verdict.UNKNOWN and any("nothing was verified" in r for r in reasons)


def test_skipped_gate_with_failing_result_is_ignored_but_not_pass_alone():
    p = plan(dec("a"), dec("b", D.SKIPPED))
    v, _ = fold(p, (res("a", R.PASS), res("b", R.FAIL)), False)
    assert v is Verdict.PASS


def test_tree_changed_downgrades_pass():
    v, reasons = fold(plan(dec("a")), (res("a", R.PASS),), True)
    assert v is Verdict.UNKNOWN and any("working tree changed" in r for r in reasons)


def _waiver(target):
    from repomarshal.model import Waiver, WaiverSource

    return Waiver(target, "hardware unavailable", "maintainer", WaiverSource.GITHUB_LABEL, "t")


def test_waived_status_without_waiver_record_is_unknown():
    p = plan(dec("a"))
    assert fold(p, (res("a", R.WAIVED),), False, waivers_permit_pass=True)[0] is Verdict.UNKNOWN


def test_waiver_needs_policy():
    p = plan(dec("a"), dec("b"))
    results = (res("a", R.UNKNOWN), res("b", R.PASS))
    assert fold(p, results, False, waivers=(_waiver("a"),))[0] is Verdict.UNKNOWN
    v, reasons = fold(p, results, False, waivers_permit_pass=True, waivers=(_waiver("a"),))
    assert v is Verdict.PASS and any("WAIVED by maintainer" in r for r in reasons)


def test_waivers_alone_are_not_a_pass():
    """v0.3 fail-open review #3: with every counted decision waived, nothing was verified."""
    p = plan(dec("a"))
    v, reasons = fold(p, (res("a", R.UNKNOWN),), False, True, (_waiver("a"),))
    assert v is Verdict.UNKNOWN and any("waivers alone" in r for r in reasons)


def test_unavailable_kind_level_decision_can_be_waived_by_kind():
    p = plan(dec("a"), dec(None, D.UNAVAILABLE, GateKind.INTEGRATION))
    w = (_waiver("integration"),)
    assert fold(p, (res("a", R.PASS),), False, True, w)[0] is Verdict.PASS
    assert fold(p, (res("a", R.PASS),), False, True, (_waiver("unit"),))[0] is Verdict.UNKNOWN


def test_fail_is_never_waivable():
    p = plan(dec("a"))
    assert fold(p, (res("a", R.FAIL),), False, True, (_waiver("a"),))[0] is Verdict.FAIL


def test_tree_change_and_nothing_verified_are_not_waivable():
    p = plan(dec("a"))
    assert fold(p, (res("a", R.UNKNOWN),), True, True, (_waiver("a"),))[0] is Verdict.UNKNOWN
    assert fold(plan(), (), False, True, (_waiver("a"),))[0] is Verdict.UNKNOWN


def test_unrecognised_status_fails_closed():
    weird = dataclasses.replace(res("a", R.PASS), status="MAYBE")
    assert fold(plan(dec("a")), (weird,), False)[0] is Verdict.UNKNOWN
