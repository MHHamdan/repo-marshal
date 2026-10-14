"""Unit tests for gate decisions (roadmap M6, M14 reason text; architecture §4.2-§4.3)."""

import dataclasses
from pathlib import Path

from repomarshal.decisions import assess_risk, decide_gates, domains_of
from repomarshal.model import (
    KIND_ORDER,
    ChangedFile,
    ChangeSet,
    Cost,
    DecisionStatus,
    FileStatus,
    Gate,
    GateDecision,
    GateKind,
    Inventory,
    Policy,
    Risk,
    Signal,
)
from repomarshal.native import native_gates
from repomarshal.signals import evaluate_signals, task_signals

REQ, SKIP = DecisionStatus.REQUIRED, DecisionStatus.SKIPPED
NA, UNAV = DecisionStatus.NOT_APPLICABLE, DecisionStatus.UNAVAILABLE
K = GateKind


def gate(id_: str, kind: GateKind, scope: str, argv: tuple[str, ...] = ("tool",), **kw) -> Gate:
    return Gate(
        id=id_,
        kind=kind,
        argv=argv,
        cwd=scope.rstrip("/"),
        scope=scope,
        cost=Cost.STANDARD,
        source=kw.pop("source", f"{scope}manifest#{kind}"),
        **kw,
    )


# Mirrors architecture §3.4 (without the unavailable root:unit; see its own test).
WEBAPP = Inventory(
    gates=(
        gate("frontend:lint", K.LINT, "frontend/", ("npm", "run", "lint")),
        gate("frontend:typecheck", K.TYPECHECK, "frontend/", ("npm", "run", "typecheck")),
        gate("frontend:unit", K.UNIT, "frontend/", ("npm", "run", "test")),
        gate("frontend:runtime", K.RUNTIME, "frontend/", ("npm", "run", "test:e2e")),
        gate("frontend:a11y", K.A11Y, "frontend/", ("npm", "run", "test:a11y")),
        gate("backend:lint", K.LINT, "backend/", ("ruff", "check", ".")),
        gate("backend:typecheck", K.TYPECHECK, "backend/", ("mypy", ".")),
        gate("backend:unit", K.UNIT, "backend/", ("python", "-m", "pytest", "-q")),
        gate("backend:integration", K.INTEGRATION, "backend/", ("python", "-m", "pytest")),
        *native_gates(),
    ),
    scopes=("backend/", "frontend/"),
)
ROOT_UNIT = gate(
    "root:unit",
    K.UNIT,
    "",
    ("make", "test"),
    available=False,
    unavailable_reason="make test: no Makefile at the repository root",
)


def change(*files: ChangedFile) -> ChangeSet:
    return ChangeSet("b" * 40, "b" * 40, "t" * 40, tuple(sorted(files, key=lambda f: f.path)), True)


def mod(path: str, *added: str) -> ChangedFile:
    return ChangedFile(path, FileStatus.MODIFIED, added=tuple(added))


def plan(ch: ChangeSet, inv: Inventory = WEBAPP, task: str | None = None):
    signals = evaluate_signals(Path("/nonexistent"), ch, Policy()) + task_signals(task)
    return signals, decide_gates(signals, inv, ch)


def by_gate(decisions) -> dict[str, GateDecision]:
    return {d.gate_id: d for d in decisions if d.gate_id}


def kind_level(decisions) -> dict[GateKind, GateDecision]:
    return {d.kind: d for d in decisions if d.gate_id is None}


def required(decisions) -> set[str]:
    return {d.gate_id for d in decisions if d.status is REQ}


SCENARIO_A = change(mod("docs/install.md", "Run `make install`."))
SCENARIO_B = change(
    mod("frontend/src/components/LoginForm.tsx", "<button>Sign in</button>"),
    mod("frontend/src/styles/login.css", ".login { color: red; }"),
)
SCENARIO_C = change(
    mod(
        "backend/app/auth/refresh.py",
        "    token = payload['refresh_token']",
        "    claims = jwt.decode(token, key)",
    )
)
SCENARIO_C_TASK = "Modify refresh-token handling"


# --------------------------------------------------------------------------- invariants


def _check_invariants(decisions, inv: Inventory) -> None:
    ids = [d.gate_id for d in decisions if d.gate_id]
    assert sorted(ids) == sorted(g.id for g in inv.gates)  # exactly one per gate
    assert all(d.reasons for d in decisions)  # every decision has a reason
    assert all(r.text for d in decisions for r in d.reasons)
    kinds_with_gates = {g.kind for g in inv.gates}
    for k in KIND_ORDER:
        levels = [d for d in decisions if d.kind is k and d.gate_id is None]
        assert len(levels) <= 1
        if k not in kinds_with_gates:
            assert len(levels) == 1
    order = [KIND_ORDER.index(d.kind) for d in decisions]
    assert order == sorted(order)
    for k in KIND_ORDER:
        gids = [d.gate_id for d in decisions if d.kind is k and d.gate_id]
        assert gids == sorted(gids)


def test_invariants_hold_for_all_scenarios():
    for ch in (SCENARIO_A, SCENARIO_B, SCENARIO_C, change()):
        for task in (None, SCENARIO_C_TASK, "trivial docs fix, skip tests"):
            _check_invariants(plan(ch, task=task)[1], WEBAPP)


# --------------------------------------------------------------------------- Scenario C


def test_scenario_c_matches_architecture_walkthrough():
    signals, decisions = plan(SCENARIO_C, task=SCENARIO_C_TASK)
    assert {s.id for s in signals} == {
        "code.changed",
        "security.auth_path",
        "security.sensitive_identifier",
        "task.security",
    }
    assert assess_risk(signals) is Risk.ELEVATED
    g, k = by_gate(decisions), kind_level(decisions)

    for gid in ("backend:lint", "backend:typecheck", "backend:unit", "backend:integration"):
        assert g[gid].status is REQ, gid
    assert g["native:secrets-diff"].status is REQ
    for gid in ("frontend:lint", "frontend:typecheck", "frontend:unit", "frontend:runtime",
                "frontend:a11y"):  # fmt: skip
        assert g[gid].status is SKIP, gid
    assert g["native:docs-links"].status is SKIP
    assert k[K.SECURITY_SCAN].status is UNAV
    assert k[K.RUNTIME].status is UNAV
    assert k[K.DEP_AUDIT].status is NA
    assert k[K.SKILL_AUDIT].status is NA
    assert required(decisions) == {
        "backend:lint",
        "backend:typecheck",
        "backend:unit",
        "backend:integration",
        "native:secrets-diff",
    }

    # reasons, as in the walkthrough table
    assert g["native:docs-links"].reasons[0].text == "no docs.changed signal"
    assert g["frontend:lint"].reasons[0].text == "no changed files under frontend/"
    assert any(r.text == "code.changed in backend/" for r in g["backend:lint"].reasons)
    assert "security.auth_path" in {r.signal for r in g["backend:integration"].reasons}
    secrets = {r.signal for r in g["native:secrets-diff"].reasons}
    assert {"security.auth_path", "security.sensitive_identifier"} <= secrets
    assert "security.auth_path" in {r.signal for r in k[K.SECURITY_SCAN].reasons}
    assert "security.auth_path" in {r.signal for r in k[K.RUNTIME].reasons}
    assert g["frontend:a11y"].reasons[0].text == "no frontend.ui signal"


def test_scenario_c_without_task_text_is_the_same():
    _, with_task = plan(SCENARIO_C, task=SCENARIO_C_TASK)
    _, without = plan(SCENARIO_C)
    status = lambda ds: [(d.kind, d.gate_id, d.status) for d in ds]  # noqa: E731
    assert status(with_task) == status(without)


def test_scenario_c_root_unit_unavailable():
    inv = Inventory(WEBAPP.gates + (ROOT_UNIT,), WEBAPP.scopes + ("",))
    _, decisions = plan(SCENARIO_C, inv)
    d = by_gate(decisions)["root:unit"]
    assert d.status is UNAV
    assert any("no Makefile" in r.text for r in d.reasons)
    _check_invariants(decisions, inv)


# --------------------------------------------------------------------------- Scenarios A and B


def test_scenario_a_only_docs_links_required():
    signals, decisions = plan(SCENARIO_A)
    assert required(decisions) == {"native:docs-links"}
    assert assess_risk(signals) is Risk.LOW
    assert by_gate(decisions)["native:docs-links"].reasons[0].signal == "docs.changed"
    # nothing needed is missing: no UNAVAILABLE anywhere
    assert not [d for d in decisions if d.status is UNAV]


def test_scenario_b_frontend_only():
    signals, decisions = plan(SCENARIO_B)
    assert required(decisions) == {
        "frontend:lint",
        "frontend:typecheck",
        "frontend:unit",
        "frontend:runtime",
        "frontend:a11y",
    }
    g, k = by_gate(decisions), kind_level(decisions)
    for gid in ("backend:lint", "backend:typecheck", "backend:unit", "backend:integration"):
        assert g[gid].status is SKIP, gid
    assert k[K.SECURITY_SCAN].status not in (REQ, UNAV)
    assert assess_risk(signals) is Risk.STANDARD
    assert "frontend.ui" in {r.signal for r in g["frontend:a11y"].reasons}


def test_rename_out_of_a_scope_requires_the_source_scope_gates():
    moved = ChangedFile(
        "frontend/src/routes.py", FileStatus.RENAMED, old_path="backend/app/api/routes.py"
    )
    _signals, decisions = plan(change(moved))
    req = required(decisions)
    assert {"backend:lint", "backend:typecheck", "backend:unit"} <= req
    assert {"frontend:lint", "frontend:typecheck", "frontend:unit"} <= req


def test_truncated_code_file_requires_secrets_gate():
    big = ChangedFile("backend/app/big.py", FileStatus.ADDED, added=("x = 1",), truncated=True)
    _signals, decisions = plan(change(big))
    assert by_gate(decisions)["native:secrets-diff"].status is REQ


# --------------------------------------------------------------------------- widening


def test_widening_signals_make_every_lint_typecheck_unit_gate_relevant():
    inv = Inventory(WEBAPP.gates + (gate("frontend:format", K.FORMAT, "frontend/"),), WEBAPP.scopes)
    for ch in (
        change(mod("Makefile")),  # build.config_changed
        change(mod("data/blob.bin")),  # coverage.unclassified
        change(mod("repomarshal.toml")),  # policy.self_modified
    ):
        signals, decisions = plan(ch, inv)
        g = by_gate(decisions)
        widening = {s.id for s in signals} & {
            "build.config_changed",
            "coverage.unclassified",
            "policy.self_modified",
        }
        assert widening, ch
        for gid in ("frontend:format", "frontend:lint", "frontend:typecheck", "frontend:unit",
                    "backend:lint", "backend:typecheck", "backend:unit"):  # fmt: skip
            assert g[gid].status is REQ, (ch.paths, gid)
            assert {r.signal for r in g[gid].reasons} & widening
        # widening does not reach expensive gates on its own
        assert g["frontend:a11y"].status is SKIP


def test_widening_with_code_change_keeps_scoped_reason():
    _, decisions = plan(change(mod("backend/app/api/routes.py"), mod("data/blob.bin")))
    g = by_gate(decisions)
    signals = {r.signal for r in g["backend:unit"].reasons}
    assert signals == {"code.changed", "coverage.unclassified"}
    assert {r.signal for r in g["frontend:unit"].reasons} == {"coverage.unclassified"}


def test_policy_self_modified_alone_requires_secrets_not_integration():
    # Field test (docs/evaluation.md): elevated risk alone does not demand integration tests,
    # which cannot verify a policy, CI or agent-config edit. It still widens and needs secrets.
    _, decisions = plan(change(mod("repomarshal.toml")))
    g = by_gate(decisions)
    assert g["backend:integration"].status is SKIP
    assert "code.changed under elevated risk" in g["backend:integration"].reasons[0].text
    assert g["native:secrets-diff"].status is REQ
    assert g["backend:unit"].status is REQ  # still widened


def test_ci_or_agent_config_alone_does_not_require_integration():
    for path in (".github/workflows/ci.yml", "AGENTS.md", ".github/scripts/release.sh"):
        signals, decisions = plan(change(mod(path)))
        assert assess_risk(signals) is Risk.ELEVATED, path
        g = by_gate(decisions)
        assert g["backend:integration"].status is SKIP, path
        assert g["native:secrets-diff"].status is REQ, path
        assert g["backend:lint"].status is REQ, path


def test_elevated_risk_with_code_change_requires_integration_in_the_code_scope():
    ch = change(mod(".github/workflows/ci.yml"), mod("backend/app/api/routes.py"))
    _, decisions = plan(ch)
    d = by_gate(decisions)["backend:integration"]
    assert d.status is REQ
    assert d.reasons[0].signal == "ci.changed"
    assert d.reasons[0].text == (
        "risk is elevated by ci.changed and code.changed; requires integration"
    )
    # code outside the integration gate's scope does not reach it
    ch = change(mod(".github/workflows/ci.yml"), mod("frontend/src/api.ts"))
    assert by_gate(plan(ch)[1])["backend:integration"].status is SKIP


def test_tamper_signal_requires_integration():
    ch = change(removed("frontend/src/a.test.ts", "it('works', () => {})"))
    signals, decisions = plan(ch)
    assert "tests.deleted" in {s.id for s in signals}
    d = by_gate(decisions)["backend:integration"]
    assert d.status is REQ and d.reasons[0].signal == "tests.deleted"


def test_lockfile_only_change_requires_unit_and_dep_audit_only():
    # v0.6 (docs/evaluation.md §10): a dependency bump alone does not demand integration, and a
    # lockfile-only change does not demand the secrets baseline.
    _, decisions = plan(change(mod("backend/uv.lock", 'version = "2.1.0"')))
    g, k = by_gate(decisions), kind_level(decisions)
    assert g["backend:integration"].status is SKIP
    assert k[K.DEP_AUDIT].status is UNAV
    assert g["frontend:unit"].status is REQ  # dependency.changed widens
    assert g["backend:unit"].status is REQ
    secrets = g["native:secrets-diff"]
    assert secrets.status is SKIP
    assert secrets.reasons[0].signal == "dependency.changed"
    assert secrets.reasons[0].text.startswith(
        "dependency.changed comes only from lockfiles (backend/uv.lock)"
    )
    assert "does not require secrets" in secrets.reasons[0].text


def test_manifest_dependency_change_still_requires_secrets_but_not_integration():
    # The manifest cannot be read at BASE here, so dependency.changed fires (fail closed).
    for manifest in ("frontend/package.json", "backend/pyproject.toml", "backend/requirements.txt"):
        signals, decisions = plan(change(mod(manifest, "requests==2.32.0"), mod("backend/uv.lock")))
        assert "dependency.changed" in {s.id for s in signals}, manifest
        g = by_gate(decisions)
        assert g["native:secrets-diff"].status is REQ, manifest
        assert g["native:secrets-diff"].reasons[0].signal == "dependency.changed"
        # (an unreadable package.json also fires tests.discovery_narrowed, which does demand it)
        integration = {r.signal for r in g["backend:integration"].reasons}
        assert "dependency.changed" not in integration, manifest
        assert kind_level(decisions)[K.DEP_AUDIT].status is UNAV


def test_lockfile_change_keeps_secrets_when_its_added_lines_are_not_clean():
    hidden = (
        ChangedFile("frontend/bun.lockb", FileStatus.MODIFIED, binary=True),
        ChangedFile("backend/uv.lock", FileStatus.MODIFIED, added=("x",), truncated=True),
        mod("frontend/package-lock.json", '"resolved": "https://ci:s3cr3t@registry.example/x.tgz"'),
        # a rename from a manifest is not lockfile-only
        ChangedFile("frontend/yarn.lock", FileStatus.RENAMED, old_path="frontend/package.json"),
    )
    for f in hidden:
        g = by_gate(plan(change(f))[1])
        assert g["native:secrets-diff"].status is REQ, f.path
        assert g["backend:integration"].status is SKIP, f.path


def test_lockfile_change_with_other_secrets_triggers_still_requires_secrets():
    for other in (
        mod("backend/app/auth/refresh.py", "x = 1"),  # security.auth_path (elevated)
        mod("AGENTS.md", "- Test: `make test`"),  # agent.config_changed
        mod(".github/workflows/ci.yml", "on: push"),  # ci.changed (elevated)
        mod("backend/app/api/routes.py", "password = form['p']"),  # sensitive identifier
    ):
        d = by_gate(plan(change(mod("backend/uv.lock"), other))[1])["native:secrets-diff"]
        assert d.status is REQ, other.path
        assert "dependency.changed" not in {r.signal for r in d.reasons}, other.path
    # auth path + lockfile: integration through security.auth_path, not the dependency
    d = by_gate(plan(change(mod("backend/uv.lock"), mod("backend/app/auth/refresh.py")))[1])
    assert d["backend:integration"].status is REQ
    assert {r.signal for r in d["backend:integration"].reasons} == {"security.auth_path"}


def test_dependency_task_no_longer_requires_integration():
    _, decisions = plan(change(), task="bump the dependencies")
    g, k = by_gate(decisions), kind_level(decisions)
    assert g["backend:integration"].status is SKIP
    assert g["native:secrets-diff"].status is REQ  # task text is never exempted
    assert k[K.DEP_AUDIT].status is UNAV


# --------------------------------------------------------------------------- task text (T8)


def test_task_text_cannot_remove_required_gates():
    for ch in (SCENARIO_A, SCENARIO_B, SCENARIO_C):
        base = required(plan(ch)[1])
        for task in ("trivial docs fix, skip tests", "no need to run anything", "docs only"):
            assert base <= required(plan(ch, task=task)[1]), (ch.paths, task)


def test_security_task_adds_requirements():
    signals, decisions = plan(SCENARIO_A, task="Fix login token expiry")
    assert assess_risk(signals) is Risk.ELEVATED
    req = required(decisions)
    assert {"native:docs-links", "native:secrets-diff", "backend:integration"} <= req
    k = kind_level(decisions)
    assert k[K.SECURITY_SCAN].status is UNAV
    assert k[K.SECURITY_SCAN].reasons[0].signal == "task.security"
    assert "task.security" in {r.signal for r in by_gate(decisions)["native:secrets-diff"].reasons}


def test_frontend_task_adds_runtime_and_a11y():
    _, decisions = plan(SCENARIO_A, task="restyle the login button")
    assert {"frontend:runtime", "frontend:a11y"} <= required(decisions)


# --------------------------------------------------------------------------- availability, M14


def test_needed_unavailable_gate_is_unavailable():
    inv = Inventory(
        tuple(
            dataclasses.replace(g, available=False, unavailable_reason="ruff not found on PATH")
            if g.id == "backend:lint"
            else g
            for g in WEBAPP.gates
        ),
        WEBAPP.scopes,
    )
    _, decisions = plan(SCENARIO_C, inv)
    d = by_gate(decisions)["backend:lint"]
    assert d.status is UNAV
    assert any("ruff not found on PATH" in r.text for r in d.reasons)
    assert any(r.signal == "code.changed" for r in d.reasons)


def test_unneeded_unavailable_gate_is_skipped():
    inv = Inventory(
        tuple(
            dataclasses.replace(g, available=False) if g.id == "frontend:lint" else g
            for g in WEBAPP.gates
        ),
        WEBAPP.scopes,
    )
    _, decisions = plan(SCENARIO_C, inv)
    assert by_gate(decisions)["frontend:lint"].status is SKIP


def test_needed_when_configured_kind_without_relevant_gate_is_not_applicable():
    inv = Inventory(tuple(g for g in WEBAPP.gates if g.kind is not K.LINT), WEBAPP.scopes)
    _, decisions = plan(SCENARIO_C, inv)
    d = kind_level(decisions)[K.LINT]
    assert d.status is NA
    assert d.reasons[0].signal == "code.changed"
    # and a non-WHEN_CONFIGURED kind is UNAVAILABLE
    inv = Inventory(tuple(g for g in WEBAPP.gates if g.kind is not K.UNIT), WEBAPP.scopes)
    assert kind_level(plan(SCENARIO_C, inv)[1])[K.UNIT].status is UNAV


def test_not_needed_kind_without_gates_is_not_applicable():
    _, decisions = plan(SCENARIO_A)
    k = kind_level(decisions)
    assert k[K.DEP_AUDIT].status is NA
    assert k[K.DEP_AUDIT].reasons[0].text == "no dependency.changed signal; none configured"


def test_m14_definition_changed_keeps_required_with_extra_reason():
    inv = Inventory(
        tuple(
            dataclasses.replace(
                g, definition_changed=True, source="backend/pyproject.toml#tool.pytest"
            )
            if g.id == "backend:unit"
            else g
            for g in WEBAPP.gates
        ),
        WEBAPP.scopes,
    )
    _, decisions = plan(SCENARIO_C, inv)
    d = by_gate(decisions)["backend:unit"]
    assert d.status is REQ
    assert d.reasons[-1].text == (
        "gate definition changed in this change (backend/pyproject.toml#tool.pytest); "
        "its result will be UNKNOWN"
    )
    # an unneeded gate with a changed definition stays SKIPPED, without the M14 reason
    inv2 = Inventory(
        tuple(dataclasses.replace(g, definition_changed=True) for g in WEBAPP.gates),
        WEBAPP.scopes,
    )
    d2 = by_gate(plan(SCENARIO_C, inv2)[1])["frontend:unit"]
    assert d2.status is SKIP and len(d2.reasons) == 1


# --------------------------------------------------------------------------- misc


def test_empty_change_and_empty_inventory():
    decisions = decide_gates((), Inventory((), ()), change())
    assert [d.kind for d in decisions] == list(KIND_ORDER)
    assert all(d.status is NA and d.gate_id is None for d in decisions)


def test_deterministic_and_order_independent():
    signals, first = plan(SCENARIO_C, task=SCENARIO_C_TASK)
    again = decide_gates(signals, WEBAPP, SCENARIO_C)
    shuffled = decide_gates(
        tuple(reversed(signals)), Inventory(tuple(reversed(WEBAPP.gates)), WEBAPP.scopes),
        SCENARIO_C,
    )  # fmt: skip
    assert first == again == shuffled


def test_assess_risk_and_domains():
    assert assess_risk(()) is Risk.LOW
    s = (
        Signal("docs.changed", "docs", Risk.LOW),
        Signal("task.security", "security", Risk.ELEVATED),
        Signal("code.changed", "code", Risk.STANDARD),
        Signal("security.auth_path", "security", Risk.ELEVATED),
    )
    assert assess_risk(s) is Risk.ELEVATED
    assert domains_of(s) == ("code", "docs", "security")
    assert domains_of(()) == ()


# --------------------------------------------------------------------------- tamper taint (v0.2)

TEST_KINDS = {K.UNIT, K.INTEGRATION, K.RUNTIME, K.A11Y}
WITH_ROOT = Inventory(gates=WEBAPP.gates + (ROOT_UNIT,), scopes=("", *WEBAPP.scopes))


def removed(path: str, *lines: str, status: FileStatus = FileStatus.MODIFIED) -> ChangedFile:
    return ChangedFile(path, status, removed=tuple(lines))


def tainted(decisions) -> dict[str, tuple[str, ...]]:
    return {d.gate_id: tuple(t.signal for t in d.taint) for d in decisions if d.taint}


def test_no_tamper_no_taint():
    for ch in (SCENARIO_A, SCENARIO_B, SCENARIO_C):
        assert tainted(plan(ch, WITH_ROOT)[1]) == {}


def test_deleted_test_taints_test_gates_of_its_scope_only():
    ch = change(
        removed(
            "backend/tests/test_refresh.py",
            "def test_rotation():",
            status=FileStatus.DELETED,
        )
    )
    _signals, decisions = plan(ch)
    g = by_gate(decisions)
    assert tainted(decisions) == {
        "backend:unit": ("tests.deleted",),
        "backend:integration": ("tests.deleted",),
    }
    (reason,) = g["backend:unit"].taint
    assert reason.text == (
        "tests.deleted: backend/tests/test_refresh.py deletes the file, removing test_rotation; "
        "this gate's result cannot vouch for the change"
    )
    # Still REQUIRED, so the gate runs and its exit code is recorded.
    assert g["backend:unit"].status is REQ
    assert g["backend:integration"].status is REQ
    # Widening makes the frontend unit gate required too, but its tests are untouched.
    assert g["frontend:unit"].status is REQ and g["frontend:unit"].taint == ()
    # Gates that do not run tests are never tainted.
    assert g["backend:lint"].taint == () and g["native:secrets-diff"].taint == ()


def test_skip_in_frontend_taints_frontend_test_runners():
    ch = change(mod("frontend/src/components/LoginForm.test.tsx", 'it.only("renders", () => {'))
    _signals, decisions = plan(ch)
    # A .test.tsx file is a test, not frontend.ui (field test), so runtime and a11y are not
    # needed and only the unit runner is tainted.
    assert tainted(decisions) == {"frontend:unit": ("tests.skip_added",)}
    g = by_gate(decisions)
    assert g["frontend:runtime"].status is SKIP and g["frontend:a11y"].status is SKIP
    assert {d.kind for d in decisions if d.taint} <= TEST_KINDS


def test_root_scope_gate_is_tainted_by_every_file_even_when_unavailable():
    ch = change(mod("frontend/src/a.test.ts", "xit('works', () => {})"))
    g = by_gate(plan(ch, WITH_ROOT)[1])
    assert g["root:unit"].status is UNAV
    assert [t.signal for t in g["root:unit"].taint] == ["tests.skip_added"]
    assert g["backend:unit"].taint == ()


def _narrowed(path: str) -> Signal:
    return Signal(
        "tests.discovery_narrowed",
        "tests",
        Risk.ELEVATED,
        (path,),
        f"test discovery narrowed; {path} adds -k to addopts",
    )


def test_scope_config_narrowing_taints_that_scope():
    s = _narrowed("backend/pyproject.toml")
    decisions = decide_gates((s,), WEBAPP, change())
    assert tainted(decisions) == {
        "backend:unit": ("tests.discovery_narrowed",),
        "backend:integration": ("tests.discovery_narrowed",),
    }
    reason = by_gate(decisions)["backend:unit"].taint[0].text
    assert reason.startswith("tests.discovery_narrowed: backend/pyproject.toml adds -k")


def test_repository_root_config_narrowing_taints_every_scope():
    decisions = decide_gates((_narrowed("pytest.ini"),), WITH_ROOT, change())
    t = tainted(decisions)
    assert set(t) == {"root:unit", "backend:unit", "frontend:unit", "backend:integration"}
    # SKIPPED gates (runtime/a11y are not needed here) are never tainted.
    g = by_gate(decisions)
    assert g["frontend:runtime"].status is SKIP and g["frontend:runtime"].taint == ()


def test_moved_test_taints_nothing():
    ch = change(
        removed("backend/tests/test_refresh.py", "def test_rotation():"),
        ChangedFile(
            "backend/tests/test_rotation.py", FileStatus.ADDED, added=("def test_rotation():",)
        ),
    )
    signals, decisions = plan(ch)
    assert "tests.deleted" not in {s.id for s in signals}
    assert tainted(decisions) == {}


def test_taint_is_deterministic_and_invariants_hold():
    ch = change(
        removed("backend/tests/test_refresh.py", "def test_rotation():"),
        mod("frontend/src/a.test.ts", "it.only('x', () => {})"),
    )
    a = plan(ch, WITH_ROOT)[1]
    assert a == plan(ch, WITH_ROOT)[1]
    _check_invariants(a, WITH_ROOT)
    root = by_gate(a)["root:unit"]
    assert [t.signal for t in root.taint] == ["tests.deleted", "tests.skip_added"]

