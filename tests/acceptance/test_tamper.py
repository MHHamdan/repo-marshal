"""Test-tamper signals (roadmap SHOULD #2; threat T2): an agent that deletes, skips or hides tests
gets UNKNOWN, never PASS, even when every gate stub passes. The tainted gates still run."""

from __future__ import annotations

import json

import pytest

from repomarshal.model import DecisionStatus, ResultStatus, Verdict

from .conftest import decision, explain, result, signal_ids

TEST_REFRESH = "backend/tests/test_refresh.py"
LOGIN_TEST = "frontend/src/components/LoginForm.test.tsx"


def _delete_test_file(w) -> None:
    w.repo.delete(TEST_REFRESH)


def _skip_backend_test(w) -> None:
    src = w.read(TEST_REFRESH)
    old = "def test_expired_after_lifetime() -> None:"
    assert old in src
    src = src.replace(old, '@pytest.mark.skip(reason="flaky")\n' + old)
    w.repo.write(TEST_REFRESH, "import pytest\n" + src)


def _focus_login_test(w) -> None:
    src = w.read(LOGIN_TEST)
    old = 'test("renders the email field"'
    assert old in src
    src = src.replace(old, 'it.only("renders the email field"')
    src += '\ntest("submits the form", () => {\n  expect(true).toBeTruthy();\n});\n'
    w.repo.write(LOGIN_TEST, src)


def _addopts_deselect(w) -> None:
    w.append("backend/pyproject.toml", 'addopts = "-k not refresh"\n')


def _jest_ignore(w) -> None:
    pkg = json.loads(w.read("frontend/package.json"))
    pkg["jest"] = {"testPathIgnorePatterns": ["/node_modules/", "LoginForm"]}
    w.repo.write("frontend/package.json", json.dumps(pkg, indent=2) + "\n")


# The addopts edit also changes backend:unit's own definition ([tool.pytest.ini_options]), so
# the runner already reports UNKNOWN for M14 and api.done keeps that reason instead of the taint
# text (it only rewrites results that are not UNKNOWN yet). The plan's taint still names it.
M14 = "gate definition changed"

CASES = [
    pytest.param(_delete_test_file, "tests.deleted", "backend:unit", None, id="delete-test-file"),
    pytest.param(_skip_backend_test, "tests.skip_added", "backend:unit", None, id="pytest-skip"),
    pytest.param(_focus_login_test, "tests.skip_added", "frontend:unit", None, id="it-only"),
    pytest.param(
        _addopts_deselect, "tests.discovery_narrowed", "backend:unit", M14, id="addopts-k"
    ),
    pytest.param(
        _jest_ignore, "tests.discovery_narrowed", "frontend:unit", None, id="jest-ignore-patterns"
    ),
]


@pytest.mark.parametrize(("edit", "signal", "gate_id", "masked_by"), CASES)
def test_tamper_is_unknown_with_passing_stubs(webapp, edit, signal, gate_id, masked_by):
    edit(webapp)
    plan = webapp.plan()
    assert signal in signal_ids(plan), explain(plan)
    d = decision(plan, gate_id)
    assert d.status == DecisionStatus.REQUIRED, explain(plan)  # still runs
    assert any(t.signal == signal and signal in t.text for t in d.taint), d.taint

    tool = "python" if gate_id.startswith("backend") else "npm"
    webapp.marker_stub(tool)
    res = webapp.done()
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.record is not None
    r = result(res.record, gate_id)
    assert r.status == ResultStatus.UNKNOWN
    assert r.exit_code == 0, "the evidence keeps the real exit code"
    assert webapp.ran(tool)
    assert (masked_by or signal) in r.reason, r.reason


def test_deleted_test_file_names_the_file(webapp):
    _delete_test_file(webapp)
    plan = webapp.plan()
    taint = decision(plan, "backend:unit").taint
    assert any(TEST_REFRESH in t.text and "test_expired_after_lifetime" in t.text for t in taint)
    # frontend tests are untouched: widened to required, but not tainted.
    front = decision(plan, "frontend:unit")
    assert front.status == DecisionStatus.REQUIRED
    assert front.taint == ()


def test_moved_test_is_not_a_deletion(webapp):
    src = webapp.read(TEST_REFRESH)
    keep, moved = src.split("\n\ndef test_expired_after_lifetime", 1)
    webapp.repo.write(TEST_REFRESH, keep + "\n")
    webapp.repo.write(
        "backend/tests/test_expiry.py",
        "from app.auth.refresh import LIFETIME_S, is_expired\n\n\n"
        "def test_expired_after_lifetime" + moved,
    )
    plan = webapp.plan()
    assert "tests.deleted" not in signal_ids(plan), explain(plan)
    assert webapp.done().verdict == Verdict.PASS


def test_ordinary_edit_still_passes(webapp):
    webapp.routine_backend_change()
    plan = webapp.plan()
    assert not {"tests.deleted", "tests.skip_added", "tests.discovery_narrowed"} & signal_ids(plan)
    res = webapp.done()
    assert res.verdict == Verdict.PASS, res.reasons


def test_adding_a_test_still_passes(webapp):
    webapp.append(
        TEST_REFRESH,
        "\n\ndef test_skip_window_is_not_expired() -> None:\n"
        "    assert not is_expired(0.0, now=0.0)\n",
    )
    webapp.append(
        LOGIN_TEST,
        '\ntest("skips nothing when the form is empty", () => {\n'
        "  expect(true).toBeTruthy();\n});\n",
    )
    plan = webapp.plan()
    assert "tests.changed" in signal_ids(plan)
    assert not {"tests.deleted", "tests.skip_added", "tests.discovery_narrowed"} & signal_ids(plan)
    res = webapp.done()
    assert res.verdict == Verdict.PASS, res.reasons
