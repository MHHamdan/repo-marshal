"""Zero-config gate inventory of the webapp fixture (architecture §3.4, without AGENTS.md
Commands, which v0.1 does not parse)."""

from __future__ import annotations

from repomarshal.model import DecisionStatus, GateKind

from .conftest import BACKEND_GATES, FRONTEND_GATES, NATIVE_GATES, decision


def test_zero_config_inventory(webapp):
    plan = webapp.plan()
    gates = {g.id: g for g in plan.inventory}
    assert set(gates) == {*FRONTEND_GATES, *BACKEND_GATES, *NATIVE_GATES}

    expected = {
        "frontend:lint": (GateKind.LINT, ("npm", "run", "lint")),
        "frontend:typecheck": (GateKind.TYPECHECK, ("npm", "run", "typecheck")),
        "frontend:unit": (GateKind.UNIT, ("npm", "run", "test")),
        "frontend:runtime": (GateKind.RUNTIME, ("npm", "run", "test:e2e")),
        "frontend:a11y": (GateKind.A11Y, ("npm", "run", "test:a11y")),
        "backend:lint": (GateKind.LINT, ("ruff", "check", "--no-fix", ".")),
        "backend:typecheck": (GateKind.TYPECHECK, ("mypy", ".")),
        "backend:unit": (
            GateKind.UNIT,
            ("python", "-m", "pytest", "-q", "--ignore=tests/integration"),
        ),
        "backend:integration": (
            GateKind.INTEGRATION,
            ("python", "-m", "pytest", "-q", "tests/integration"),
        ),
    }
    for gate_id, (kind, argv) in expected.items():
        g = gates[gate_id]
        assert g.kind == kind, gate_id
        assert tuple(g.argv) == argv, gate_id
        assert g.scope == gate_id.split(":")[0] + "/", gate_id
        assert g.available, f"{gate_id}: {g.unavailable_reason}"
        assert not g.definition_changed, gate_id
    for gate_id in NATIVE_GATES:
        assert gates[gate_id].provider == "native"
        assert gates[gate_id].scope == ""


def test_every_inventory_gate_gets_exactly_one_decision(webapp):
    plan = webapp.plan(webapp.scenario_c())
    for g in plan.inventory:
        decision(plan, g.id)
    for d in plan.gates:
        assert d.reasons, f"{d.gate_id or d.kind} has no reason"
        assert d.status in set(DecisionStatus)
