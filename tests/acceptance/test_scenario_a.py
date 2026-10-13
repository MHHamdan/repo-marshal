"""Scenario A: "Update installation documentation" (MISSION.md §42; architecture §4.3)."""

from __future__ import annotations

from repomarshal.model import DecisionStatus, ResultStatus, Verdict

from .conftest import (
    BACKEND_GATES,
    FRONTEND_GATES,
    TASK_A,
    explain,
    result,
    skill_names,
    status_of,
)


def test_a_requires_docs_links(webapp):
    task = webapp.scenario_a()
    plan = webapp.plan(task)
    assert status_of(plan, "native:docs-links") == DecisionStatus.REQUIRED, explain(plan)


def test_a_skips_code_gates_and_secrets(webapp):
    plan = webapp.plan(webapp.scenario_a())
    for gate_id in (*FRONTEND_GATES, *BACKEND_GATES):
        assert status_of(plan, gate_id) == DecisionStatus.SKIPPED, f"{gate_id}\n{explain(plan)}"
    assert status_of(plan, "native:secrets-diff") == DecisionStatus.SKIPPED, explain(plan)


def test_a_has_no_unavailable_decision(webapp):
    plan = webapp.plan(webapp.scenario_a())
    unavailable = [d for d in plan.gates if d.status == DecisionStatus.UNAVAILABLE]
    assert unavailable == [], explain(plan)


def test_a_skills(webapp):
    plan = webapp.plan(webapp.scenario_a())
    names = skill_names(plan)
    assert "docs-writing" in names
    assert "ui-forms" not in names
    assert "auth-security" not in names


def test_a_done_passes(webapp):
    webapp.scenario_a()
    res = webapp.done(TASK_A)
    assert res.verdict == Verdict.PASS, res.reasons
    assert res.verdict.exit_code == 0
    assert res.record is not None
    assert result(res.record, "native:docs-links").status == ResultStatus.PASS


def test_a_broken_relative_link_fails(webapp):
    webapp.scenario_a_broken_link()
    res = webapp.done(TASK_A)
    assert res.verdict == Verdict.FAIL, res.reasons
    assert res.verdict.exit_code == 1
    assert res.record is not None
    assert result(res.record, "native:docs-links").status == ResultStatus.FAIL
