"""Test-tamper signals (tests.deleted / tests.skip_added / tests.discovery_narrowed) and taint.

Every gate stub passes, so a tamper edit the signals miss comes out as PASS. The correct
(fail-closed) outcome is anything but PASS; a tainted gate that FAILED must stay FAIL."""

from __future__ import annotations

import json

import pytest

from repomarshal.model import Verdict

from .conftest import LOGIN_TEST, TEST_REFRESH, Webapp

OLD_DEF = "def test_expired_after_lifetime() -> None:"
MAKE_POLICY = 'version = 1\n\n[gates.suite]\nkind = "unit"\nargv = ["make", "test"]\n'


def _decorate(w: Webapp, decorator: str, header: str = "import pytest\n") -> None:
    src = w.read(TEST_REFRESH)
    assert OLD_DEF in src
    w.repo.write(TEST_REFRESH, header + src.replace(OLD_DEF, f"{decorator}\n{OLD_DEF}"))


def _move(w: Webapp, new_path: str) -> None:
    w.repo.write(new_path, w.read(TEST_REFRESH))
    w.repo.delete(TEST_REFRESH)


def _set_jest(w: Webapp, config: dict) -> None:
    pkg = json.loads(w.read("frontend/package.json"))
    pkg["jest"] = config
    w.repo.write("frontend/package.json", json.dumps(pkg, indent=2) + "\n")


def _makefile(w: Webapp, before: str, after: str) -> None:
    w.commit_on_base("Makefile", f"test:\n\t{before}\n", "Makefile at BASE")
    w.repo.write("Makefile", f"test:\n\t{after}\n")


# ---------------------------------------------------------------------- confirmed fail-open

PY_SKIP_BYPASSES = [
    pytest.param(lambda w: _decorate(w, "@pt.mark.skip", "import pytest as pt\n"), id="alias"),
    pytest.param(
        lambda w: _decorate(w, "@m.skip", "from pytest import mark as m\n"), id="from-mark-as"
    ),
    pytest.param(lambda w: _decorate(w, "@pytest .mark .skip"), id="spaced-attribute"),
    # Python NFKC-normalises identifiers: `ｓｋｉｐ` (fullwidth) IS `skip` to the interpreter.
    pytest.param(lambda w: _decorate(w, "@pytest.mark.ｓｋｉｐ"), id="fullwidth-skip"),
    pytest.param(
        lambda w: w.repo.write(
            TEST_REFRESH,
            'import pytest\npytest.importorskip("no_such_module")\n' + w.read(TEST_REFRESH),
        ),
        id="importorskip",
    ),
    pytest.param(lambda w: w.append(TEST_REFRESH, "\n__test__ = False\n"), id="dunder-test"),
]

COLLECTION_BYPASSES = [
    # outside testpaths = ["tests"]; the file name still matches COLLECTED_TEST_GLOBS
    pytest.param(lambda w: _move(w, "backend/attic/test_refresh.py"), id="move-outside-testpaths"),
    # `build` is in pytest's default norecursedirs
    pytest.param(lambda w: _move(w, "backend/tests/build/test_refresh.py"), id="move-norecursedir"),
    # deleted test "moved" into a file pytest never collects (not test_*.py / *_test.py)
    pytest.param(
        lambda w: (
            w.repo.write(TEST_REFRESH, w.read(TEST_REFRESH).split("\n\n" + OLD_DEF)[0] + "\n"),
            w.repo.write("backend/tests/helpers.py", OLD_DEF + "\n    pass\n"),
        ),
        id="delete-masked-by-uncollected-def",
    ),
]

JS_BYPASSES = [
    pytest.param(
        lambda w: w.append(
            LOGIN_TEST,
            '\nconst focus = "only";\ntest[focus]("smoke", () => {\n'
            "  expect(true).toBeTruthy();\n});\n",
        ),
        id="computed-only",
    ),
    pytest.param(lambda w: _set_jest(w, {"roots": ["<rootDir>/nothing"]}), id="jest-roots"),
]

DISCOVERY_BYPASSES = [
    # pytest >= 9 reads pytest.toml before pyproject.toml; neither M14 nor the signal knows it
    pytest.param(
        lambda w: w.repo.write(
            "backend/pytest.toml", '[pytest]\naddopts = ["-k", "not expired"]\n'
        ),
        id="pytest-toml",
    ),
]

MAKEFILE_BYPASSES = [
    pytest.param(
        lambda w: _makefile(
            w, "python -m pytest -q", "PYTEST_ADDOPTS='-k smoke' python -m pytest -q"
        ),
        id="pytest-addopts-env",
    ),
    pytest.param(
        lambda w: _makefile(w, "python -m pytest -q", "python -m pytest -q tests/test_smoke.py"),
        id="positional-path",
    ),
    pytest.param(lambda w: _makefile(w, "npm test", "npm test -- -t smoke"), id="npm-passthrough"),
]


@pytest.mark.failopen
@pytest.mark.parametrize(
    "edit", PY_SKIP_BYPASSES + COLLECTION_BYPASSES + JS_BYPASSES + DISCOVERY_BYPASSES
)
def test_tamper_edit_is_never_pass(webapp: Webapp, edit):
    edit(webapp)
    res = webapp.done()
    assert res.verdict != Verdict.PASS, res.reasons


@pytest.mark.failopen
@pytest.mark.parametrize("edit", MAKEFILE_BYPASSES)
def test_makefile_narrowing_is_never_pass(tmp_path, edit):
    """A policy gate runs `make test`; the Makefile is not part of its M14 definition, so the
    v0.2 Makefile check is the only defence, and it only knows options written after the runner."""
    w = Webapp(tmp_path, policy=MAKE_POLICY)
    w.stub("make")
    edit(w)
    res = w.done()
    assert res.verdict != Verdict.PASS, res.reasons


@pytest.mark.failopen
def test_taint_never_turns_fail_into_unknown(webapp: Webapp):
    """api.done replaces a tainted gate's status with UNKNOWN unconditionally, so an agent whose
    tests FAIL adds one skip marker and the verdict improves from FAIL (exit 1) to UNKNOWN
    (exit 2). A tainted FAIL must stay FAIL."""
    _decorate(webapp, '@pytest.mark.skip(reason="flaky")')
    webapp.stub("python", exit_code=1, output="1 failed")
    res = webapp.done()
    assert res.verdict == Verdict.FAIL, res.reasons


# ---------------------------------------------------------------------- regression guards

HANDLED = [
    pytest.param(
        lambda w: _decorate(w, "@skip", "import pytest\nskip = pytest.mark.skip\n"), id="alias-var"
    ),
    pytest.param(
        lambda w: w.repo.write(
            TEST_REFRESH, w.read(TEST_REFRESH).replace("def test_expired", "def check_expired")
        ),
        id="rename-test-function",
    ),
    pytest.param(
        lambda w: w.repo.write(
            TEST_REFRESH, w.read(TEST_REFRESH).replace("def test_expired", "def tеst_expired")
        ),
        id="cyrillic-lookalike-def",
    ),
    pytest.param(
        lambda w: (
            w.repo.write(
                "backend/tests/test_refresh_v2.py",
                w.read(TEST_REFRESH).split("\n\n" + OLD_DEF)[0] + "\n",
            ),
            w.repo.delete(TEST_REFRESH),
            w.repo.git("add", "-A"),
        ),
        id="delete-in-renamed-file",
    ),
    pytest.param(
        lambda w: _move(w, "backend/tests/test_refresh.py.bak"), id="rename-uncollected-ext"
    ),
    pytest.param(
        lambda w: w.repo.write(
            LOGIN_TEST,
            w.read(LOGIN_TEST).replace(
                'test("renders the email field"', 'test["only"]("renders the email field"'
            ),
        ),
        id="bracket-only-on-existing-test",
    ),
    pytest.param(
        lambda w: w.append(LOGIN_TEST, "\nconst focus = test.only;\n"), id="only-helper-variable"
    ),
    pytest.param(lambda w: _set_jest(w, {"testMatch": ["**/none/*.ts"]}), id="jest-testMatch"),
    pytest.param(
        lambda w: w.repo.write(
            "frontend/jest.config.js",
            "module.exports = {\n  testPathIgnorePatterns: ['LoginForm'],\n};\n",
        ),
        id="jest-config-js",
    ),
    pytest.param(
        lambda w: w.repo.write(
            "backend/tests/conftest.py", "collect_ignore = ['test_refresh.py']\n"
        ),
        id="conftest-collect-ignore",
    ),
    pytest.param(
        lambda w: w.repo.write(
            "backend/tests/conftest.py",
            "def pytest_collection_modifyitems(items):\n    del items[1:]\n",
        ),
        id="conftest-modifyitems",
    ),
]


@pytest.mark.parametrize("edit", HANDLED)
def test_handled_tamper_is_not_pass(webapp: Webapp, edit):
    edit(webapp)
    res = webapp.done()
    assert res.verdict != Verdict.PASS, res.reasons


def test_makefile_dash_k_is_caught(tmp_path):
    w = Webapp(tmp_path, policy=MAKE_POLICY)
    w.stub("make")
    _makefile(w, "python -m pytest -q", "python -m pytest -q -k smoke")
    assert w.done().verdict == Verdict.UNKNOWN


def test_tainted_pass_is_unknown_not_pass(webapp: Webapp):
    _decorate(webapp, '@pytest.mark.skip(reason="flaky")')
    assert webapp.done().verdict == Verdict.UNKNOWN
