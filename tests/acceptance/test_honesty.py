"""Verdict honesty (roadmap M13, M14; architecture §5-§7). Each case is its own test."""

from __future__ import annotations

import re
import shutil

import pytest

from repomarshal.model import (
    DecisionStatus,
    PlanMode,
    PolicyInvalid,
    ResultStatus,
    Verdict,
)

from .conftest import (
    CONFIGURED_POLICY,
    FRONTEND_GATES,
    Webapp,
    decision,
    explain,
    required_ids,
    result,
    signal_ids,
    status_of,
)


# 1 ---------------------------------------------------------------------------------------------
def test_failing_unit_gate_fails(webapp):
    webapp.routine_backend_change()
    # pytest stub: the unit run fails, the integration run (if any) passes.
    webapp.script_stub(
        "python",
        'case " $* " in *" tests/integration "*) exit 0 ;; esac\necho \'1 failed\'\nexit 1',
    )
    res = webapp.done()
    assert res.verdict == Verdict.FAIL, res.reasons
    assert res.verdict.exit_code == 1
    assert res.record is not None
    unit = result(res.record, "backend:unit")
    assert unit.status == ResultStatus.FAIL
    assert unit.exit_code == 1


# 2 ---------------------------------------------------------------------------------------------
def test_empty_change_is_unknown_nothing_verified(webapp):
    plan = webapp.plan()
    assert plan.mode == PlanMode.FORECAST
    assert plan.change.files == ()
    assert plan.required() == ()

    res = webapp.done()
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.verdict.exit_code == 2
    assert any(re.search(r"nothing\b.*\bverified", r, re.IGNORECASE) for r in res.reasons), (
        res.reasons
    )


# 3 ---------------------------------------------------------------------------------------------
def test_tree_changed_during_run_is_unknown(webapp):
    webapp.routine_backend_change()
    tracked = webapp.root / "backend" / "app" / "api" / "routes.py"
    # A "formatter" that rewrites a tracked file while the gates run.
    webapp.script_stub("ruff", f"printf '%s\\n' '# reformatted' >> '{tracked}'\nexit 0")
    res = webapp.done()
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.record is not None
    assert res.record.tree_digest_before != res.record.tree_digest_after
    assert any("changed" in r.lower() for r in res.reasons), res.reasons


# 4 ---------------------------------------------------------------------------------------------
TIMEOUT_POLICY = """\
version = 1

[gates.slow-unit]
kind = "unit"
scope = "backend/"
argv = ["slow-tests"]
timeout_s = 1
"""


def test_gate_timeout_is_unknown(tmp_path):
    w = Webapp(tmp_path, policy=TIMEOUT_POLICY)
    sleep = shutil.which("sleep")
    assert sleep
    w.script_stub("slow-tests", f"exec '{sleep}' 5")
    w.routine_backend_change()

    plan = w.plan()
    assert status_of(plan, "slow-unit") == DecisionStatus.REQUIRED, explain(plan)

    res = w.done()
    assert res.record is not None
    assert result(res.record, "slow-unit").status == ResultStatus.UNKNOWN
    assert res.verdict != Verdict.PASS
    assert res.verdict == Verdict.UNKNOWN, res.reasons


# 5 ---------------------------------------------------------------------------------------------
def test_task_text_cannot_remove_gates(webapp):
    webapp.routine_backend_change()
    baseline = webapp.plan()
    plan = webapp.plan("trivial typo, skip tests")
    assert status_of(plan, "backend:unit") == DecisionStatus.REQUIRED, explain(plan)
    assert required_ids(plan) >= required_ids(baseline), explain(plan)


# 6 ---------------------------------------------------------------------------------------------
def _gate_defs(plan):
    return {g.id: (g.kind, tuple(g.argv), g.scope) for g in plan.inventory}


def test_uncommitted_policy_removal_does_not_apply(configured_webapp):
    w = configured_webapp
    w.scenario_c()
    before = w.plan()
    assert status_of(before, "security") == DecisionStatus.REQUIRED, explain(before)

    weakened = CONFIGURED_POLICY.replace(
        '[gates.security]\nkind = "security_scan"\nscope = "backend/"\n'
        'argv = ["bandit", "-r", "app"]\n',
        "",
    )
    assert "gates.security" not in weakened
    w.repo.write("repomarshal.toml", weakened)

    after = w.plan()
    assert "policy.self_modified" in signal_ids(after)
    assert _gate_defs(after) == _gate_defs(before)
    assert after.policy.digest == before.policy.digest
    assert status_of(after, "security") == DecisionStatus.REQUIRED, explain(after)

    w.marker_stub("bandit")
    res = w.done()
    assert w.ran("bandit"), "the BASE security gate must still run"
    assert res.record is not None
    assert result(res.record, "security").status == ResultStatus.PASS


def test_uncommitted_policy_override_does_not_apply(configured_webapp):
    w = configured_webapp
    w.scenario_c()
    before = w.plan()

    w.append(
        "repomarshal.toml",
        '\n[gates.backend-unit-override]\nkind = "unit"\nscope = "backend/"\nargv = ["true"]\n',
    )
    after = w.plan()
    assert "policy.self_modified" in signal_ids(after)
    assert _gate_defs(after) == _gate_defs(before)
    assert after.policy.digest == before.policy.digest
    assert "backend-unit-override" not in {g.id for g in after.inventory}
    assert all(d.gate_id != "backend-unit-override" for d in after.gates)
    unit = decision(after, "backend:unit")
    assert unit.status == DecisionStatus.REQUIRED, explain(after)


# 7 ---------------------------------------------------------------------------------------------
INVALID_POLICY = """\
version = 1

[gates.security]
kind = "security_scan"
scope = "backend/"
argv = "bandit -r app"
"""


def test_invalid_policy_at_base_is_blocked(tmp_path):
    w = Webapp(tmp_path, policy=INVALID_POLICY)
    for name in ("npm", "ruff", "mypy", "python", "bandit"):
        w.marker_stub(name)
    w.scenario_c()

    with pytest.raises(PolicyInvalid):
        w.plan()

    res = w.done()
    assert res.verdict == Verdict.BLOCKED, res.reasons
    assert res.verdict.exit_code == 3
    assert res.record is None or res.record.results == ()
    for name in ("npm", "ruff", "mypy", "python", "bandit"):
        assert not w.ran(name), f"{name} ran although the policy is invalid"


# 8 ---------------------------------------------------------------------------------------------
def test_changed_gate_definition_is_unknown(webapp):
    pkg = webapp.read("frontend/package.json")
    edited = pkg.replace('"test": "jest"', '"test": "exit 0"')
    assert edited != pkg
    webapp.repo.write("frontend/package.json", edited)
    webapp.edit_login_form()

    plan = webapp.plan()
    unit_gate = {g.id: g for g in plan.inventory}["frontend:unit"]
    assert unit_gate.definition_changed
    assert tuple(unit_gate.argv) == ("npm", "run", "test")
    assert status_of(plan, "frontend:unit") == DecisionStatus.REQUIRED, explain(plan)

    res = webapp.done()
    assert res.record is not None
    assert result(res.record, "frontend:unit").status == ResultStatus.UNKNOWN
    assert res.verdict != Verdict.PASS, res.reasons


# 9 ---------------------------------------------------------------------------------------------
def test_missing_tool_is_unavailable_never_pass(webapp):
    webapp.remove_stub("npm")
    task = webapp.scenario_b()
    plan = webapp.plan(task)
    for gate_id in FRONTEND_GATES:
        assert status_of(plan, gate_id) == DecisionStatus.UNAVAILABLE, f"{gate_id}\n{explain(plan)}"
    assert not {g.id: g for g in plan.inventory}["frontend:unit"].available

    res = webapp.done(task)
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.verdict.exit_code == 2
