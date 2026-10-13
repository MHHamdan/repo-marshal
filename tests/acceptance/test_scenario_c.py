"""Scenario C: "Modify refresh-token handling" (MISSION.md §42; architecture §4.3)."""

from __future__ import annotations

from repomarshal.model import DecisionStatus, ResultStatus, Risk, Verdict

from .conftest import (
    BACKEND_GATES,
    FRONTEND_GATES,
    context_paths,
    decisions_of_kind,
    explain,
    kind_level,
    result,
    skill_names,
    status_of,
)


def test_c_risk_and_evidence_level_elevated(webapp):
    plan = webapp.plan(webapp.scenario_c())
    assert plan.risk == Risk.ELEVATED
    assert plan.evidence_level == "elevated"


def test_c_requires_backend_gates_and_secrets(webapp):
    plan = webapp.plan(webapp.scenario_c())
    for gate_id in (*BACKEND_GATES, "native:secrets-diff"):
        assert status_of(plan, gate_id) == DecisionStatus.REQUIRED, f"{gate_id}\n{explain(plan)}"


def test_c_skips_every_frontend_gate(webapp):
    plan = webapp.plan(webapp.scenario_c())
    for gate_id in FRONTEND_GATES:
        assert status_of(plan, gate_id) == DecisionStatus.SKIPPED, f"{gate_id}\n{explain(plan)}"


def test_c_security_scan_kind_unavailable(webapp):
    plan = webapp.plan(webapp.scenario_c())
    levels = kind_level(plan, "security_scan")
    assert [d.status for d in levels] == [DecisionStatus.UNAVAILABLE], explain(plan)


def test_c_runtime_unavailable_and_frontend_runtime_skipped(webapp):
    plan = webapp.plan(webapp.scenario_c())
    runtime = decisions_of_kind(plan, "runtime")
    assert any(d.status == DecisionStatus.UNAVAILABLE for d in runtime), explain(plan)
    assert status_of(plan, "frontend:runtime") == DecisionStatus.SKIPPED, explain(plan)


def test_c_context(webapp):
    plan = webapp.plan(webapp.scenario_c())
    paths = context_paths(plan)
    assert "backend/app/auth/refresh.py" in paths
    assert "backend/tests/test_refresh.py" in paths
    assert "docs/adr/0003-token-refresh.md" in paths


def test_c_skills(webapp):
    plan = webapp.plan(webapp.scenario_c())
    assert "auth-security" in skill_names(plan)


def test_c_zero_config_done_is_unknown(webapp):
    task = webapp.scenario_c()
    res = webapp.done(task)
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.verdict.exit_code == 2
    assert res.record is not None
    # Every REQUIRED gate ran and passed: UNKNOWN comes only from what could not be checked.
    for gate_id in (*BACKEND_GATES, "native:secrets-diff"):
        assert result(res.record, gate_id).status == ResultStatus.PASS


def test_c_configured_plan_requires_policy_gates(configured_webapp):
    plan = configured_webapp.plan(configured_webapp.scenario_c())
    assert status_of(plan, "security") == DecisionStatus.REQUIRED, explain(plan)
    assert status_of(plan, "backend-runtime") == DecisionStatus.REQUIRED, explain(plan)
    assert [d for d in plan.gates if d.status == DecisionStatus.UNAVAILABLE] == [], explain(plan)


def test_c_configured_done_passes(configured_webapp):
    task = configured_webapp.scenario_c()
    res = configured_webapp.done(task)
    assert res.verdict == Verdict.PASS, res.reasons
    assert res.verdict.exit_code == 0
    assert res.record is not None
    assert result(res.record, "security").status == ResultStatus.PASS
    assert result(res.record, "backend-runtime").status == ResultStatus.PASS
