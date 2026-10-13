"""Scenario B: "Improve responsive login form" (MISSION.md §42; architecture §4.3)."""

from __future__ import annotations

from repomarshal.model import DecisionStatus, ResultStatus, Verdict

from .conftest import (
    BACKEND_GATES,
    FRONTEND_GATES,
    explain,
    result,
    skill_names,
    status_of,
)


def test_b_requires_every_frontend_gate(webapp):
    plan = webapp.plan(webapp.scenario_b())
    for gate_id in FRONTEND_GATES:  # lint, typecheck, unit, runtime, a11y
        assert status_of(plan, gate_id) == DecisionStatus.REQUIRED, f"{gate_id}\n{explain(plan)}"


def test_b_skips_backend_gates(webapp):
    plan = webapp.plan(webapp.scenario_b())
    for gate_id in BACKEND_GATES:
        assert status_of(plan, gate_id) == DecisionStatus.SKIPPED, f"{gate_id}\n{explain(plan)}"


def test_b_has_no_security_scan_obligation(webapp):
    plan = webapp.plan(webapp.scenario_b())
    bad = [
        d
        for d in plan.gates
        if d.kind == "security_scan"
        and d.status in (DecisionStatus.REQUIRED, DecisionStatus.UNAVAILABLE)
    ]
    assert bad == [], explain(plan)


def test_b_skills(webapp):
    plan = webapp.plan(webapp.scenario_b())
    names = skill_names(plan)
    assert "ui-forms" in names
    assert "auth-security" not in names


def test_b_done_with_passing_stubs_passes(webapp):
    task = webapp.scenario_b()
    res = webapp.done(task)
    assert res.verdict == Verdict.PASS, res.reasons
    assert res.record is not None
    for gate_id in FRONTEND_GATES:
        assert result(res.record, gate_id).status == ResultStatus.PASS
    ran = {r.gate_id for r in res.record.results}
    assert not ran & set(BACKEND_GATES), "skipped backend gates must not run"
