"""Unit tests for the signal catalogue (roadmap M5)."""

import json
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import signals as sig
from repomarshal.model import (
    ChangedFile,
    ChangeSet,
    FileStatus,
    Policy,
    Risk,
    SignalRuleSpec,
)

M, A, D, R = FileStatus.MODIFIED, FileStatus.ADDED, FileStatus.DELETED, FileStatus.RENAMED
NOWHERE = Path("/nonexistent-repo")  # only build.config_changed reads content


def cs(*files: ChangedFile, base: str | None = None) -> ChangeSet:
    return ChangeSet(base, base, "t" * 40, tuple(sorted(files, key=lambda f: f.path)), True)


def f(path: str, status: FileStatus = M, added: tuple[str, ...] = (), **kw) -> ChangedFile:
    return ChangedFile(path, status, added=added, **kw)


DEFAULT_POLICY = Policy()


def ids(change: ChangeSet, policy: Policy = DEFAULT_POLICY, repo: Path = NOWHERE) -> dict:
    return {s.id: s.files for s in sig.evaluate_signals(repo, change, policy)}


# --------------------------------------------------------------------------- per rule


@pytest.mark.parametrize(
    "path",
    [
        "docs/install.md",
        "README.md",
        "guide.rst",
        "docs/diagram.png",
        "CHANGELOG.txt",
        "a/b.mdx",
        "LICENSE",
    ],
)
def test_docs_changed(path):
    assert "docs.changed" in ids(cs(f(path)))


@pytest.mark.parametrize(
    "path",
    [
        "requirements.txt",
        "notes.txt.bak",
        "src/app.py",
        "frontend/src/docs/messages.json",
        "docs/conf.py",
        "doc/data.yaml",
        "src/logo.png",
    ],
)
def test_not_docs(path):
    assert "docs.changed" not in ids(cs(f(path)))


@pytest.mark.parametrize(
    "path", ["docs/notes.txt", "doc/img/arch.svg", "docs/shot.jpg", "pkg/README", "x/CHANGELOG"]
)
def test_docs_formats(path):
    s = ids(cs(f(path)))
    assert set(s) == {"docs.changed"}


def test_non_doc_file_under_docs_goes_through_the_normal_rules():
    s = ids(cs(f("frontend/src/docs/messages.json"), f("docs/conf.py")))
    assert s["coverage.unclassified"] == ("frontend/src/docs/messages.json",)
    assert s["code.changed"] == ("docs/conf.py",)
    assert "docs.changed" not in s


def test_mdx_is_code_and_frontend():
    s = ids(cs(f("frontend/src/pages/help.mdx")))
    assert {"code.changed", "frontend.ui", "docs.changed"} <= set(s)
    assert "security.auth_path" in ids(cs(f("app/auth/login.mdx")))  # not exempt as docs


@pytest.mark.parametrize(
    "path", ["backend/app/api/routes.py", "web/main.ts", "x/y.tsx", "cmd/main.go", "lib/a.rs"]
)
def test_code_changed(path):
    assert "code.changed" in ids(cs(f(path)))


@pytest.mark.parametrize(
    "path,expected",
    [
        ("frontend/src/styles/login.css", True),
        ("frontend/src/components/LoginForm.tsx", True),
        ("frontend/src/components/util.ts", True),
        ("site/index.html", True),
        ("backend/app/api/routes.py", False),
        ("frontend/src/lib/fetch.ts", False),
    ],
)
def test_frontend_ui(path, expected):
    assert ("frontend.ui" in ids(cs(f(path)))) is expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("backend/tests/test_refresh.py", True),
        ("pkg/thing_test.py", True),
        ("frontend/src/components/LoginForm.test.tsx", True),
        ("web/a.spec.ts", True),
        ("web/__tests__/a.ts", True),
        ("server/handler_test.go", True),
        ("backend/app/testing_utils.py", False),
        ("backend/app/contest.py", False),
    ],
)
def test_tests_changed(path, expected):
    assert ("tests.changed" in ids(cs(f(path)))) is expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("backend/app/auth/refresh.py", True),  # directory segment
        ("app/session.py", True),  # exact stem
        ("svc/Tokens/store.go", True),  # case-insensitive segment
        ("lib/crypto.rs", True),
        ("frontend/src/components/LoginForm.tsx", False),  # "login" inside a stem: no
        ("frontend/src/styles/login.css", False),  # stylesheet: exempt
        ("app/authority.py", False),
        ("docs/security/threat-model.md", False),  # docs: exempt
        ("app/acl/rules.yaml", True),
    ],
)
def test_security_auth_path(path, expected):
    s = ids(cs(f(path)))
    assert ("security.auth_path" in s) is expected


def test_auth_path_matches_old_path_of_rename():
    s = ids(cs(f("app/misc/x.py", R, old_path="app/auth/x.py")))
    assert s["security.auth_path"] == ("app/auth/x.py", "app/misc/x.py")


def test_rename_lists_both_paths_in_every_signal():
    s = ids(cs(f("frontend/src/routes.py", R, old_path="backend/app/api/routes.py")))
    assert s["code.changed"] == ("backend/app/api/routes.py", "frontend/src/routes.py")
    assert "coverage.unclassified" not in s


def test_rename_unclassified_when_either_path_matches_no_rule():
    # The old path matched nothing: the new docs path must not make the move docs-only.
    s = ids(cs(f("docs/notes.md", R, old_path="backend/app/data.xyz")))
    assert s["coverage.unclassified"] == ("backend/app/data.xyz", "docs/notes.md")
    assert s["docs.changed"] == ("backend/app/data.xyz", "docs/notes.md")
    s = ids(cs(f("backend/app/data.xyz", R, old_path="docs/notes.md")))
    assert s["coverage.unclassified"] == ("backend/app/data.xyz", "docs/notes.md")


def test_rename_custom_rule_matches_old_path():
    policy = Policy(signals=(SignalRuleSpec("custom.pay", "security", Risk.ELEVATED, ("pay/**",)),))
    s = ids(cs(f("misc/a.py", R, old_path="pay/a.py")), policy)
    assert s["custom.pay"] == ("misc/a.py", "pay/a.py")


def test_auth_path_is_elevated():
    (s,) = [
        x
        for x in sig.evaluate_signals(NOWHERE, cs(f("a/auth/b.py")), Policy())
        if x.id == "security.auth_path"
    ]
    assert s.risk is Risk.ELEVATED and s.domain == "security"


def test_sensitive_identifier_added_code_lines_only():
    hit = f("backend/app/api/routes.py", added=("claims = jwt.decode(raw)",))
    assert "security.sensitive_identifier" in ids(cs(hit))
    removed_only = ChangedFile("backend/app/api/routes.py", M, removed=("token = 1",))
    assert "security.sensitive_identifier" not in ids(cs(removed_only))
    in_test = f("backend/tests/test_refresh.py", added=("token = make_token()",))
    assert "security.sensitive_identifier" not in ids(cs(in_test))
    in_docs = f("docs/x.md", added=("use a bearer token",))
    assert "security.sensitive_identifier" not in ids(cs(in_docs))
    (s,) = [
        x for x in sig.evaluate_signals(NOWHERE, cs(hit), Policy()) if x.id.endswith("identifier")
    ]
    assert s.risk is Risk.STANDARD


@pytest.mark.parametrize("flag", ["binary", "truncated"])
def test_sensitive_identifier_fails_closed_on_hidden_added_lines(flag):
    hidden = f("backend/app/big.py", A, added=("x = 1",), **{flag: True})
    (s,) = [
        x
        for x in sig.evaluate_signals(NOWHERE, cs(hidden), Policy())
        if x.id == "security.sensitive_identifier"
    ]
    assert s.files == ("backend/app/big.py",)
    assert f"backend/app/big.py: added lines not fully visible ({flag})" in s.detail
    # not for tests or non-code files
    assert "security.sensitive_identifier" not in ids(
        cs(f("backend/tests/test_big.py", A, **{flag: True}), f("img/a.png", A, **{flag: True}))
    )


def test_policy_self_modified():
    s = sig.evaluate_signals(NOWHERE, cs(f("repomarshal.toml")), Policy())
    by = {x.id: x for x in s}
    assert by["policy.self_modified"].risk is Risk.ELEVATED
    assert "policy.self_modified" not in ids(cs(f("sub/repomarshal.toml")))


@pytest.mark.parametrize(
    "path",
    ["Makefile", "backend/setup.cfg", "tox.ini", "frontend/tsconfig.json", "vite.config.ts",
     "frontend/tsconfig.build.json", "ruff.toml"],
)  # fmt: skip
def test_build_config_by_name_always_fires(path):
    assert "build.config_changed" in ids(cs(f(path)))


def test_unclassified_and_deleted_files():
    s = ids(cs(f("data/blob.bin", D), f("package-lock.json"), f("src/app.py", D)))
    assert s["coverage.unclassified"] == ("data/blob.bin", "package-lock.json")
    assert s["code.changed"] == ("src/app.py",)  # deleted files count


def test_one_signal_per_id_sorted_files_and_sorted_ids():
    out = sig.evaluate_signals(NOWHERE, cs(f("b/x.py"), f("a/y.py"), f("docs/z.md")), Policy())
    assert [s.id for s in out] == sorted(s.id for s in out)
    by = {s.id: s for s in out}
    assert by["code.changed"].files == ("a/y.py", "b/x.py")
    assert all(s.detail for s in out)


def test_custom_rules_are_add_only():
    policy = Policy(
        signals=(
            SignalRuleSpec("custom.payments", "security", Risk.ELEVATED, ("**/payments/**",)),
            SignalRuleSpec("custom.blob", "data", Risk.LOW, ("data/*.bin",)),
        )
    )
    s = ids(cs(f("backend/payments/charge.py"), f("data/x.bin"), f("payments/a.py")), policy)
    assert s["custom.payments"] == ("backend/payments/charge.py", "payments/a.py")
    assert s["code.changed"] == ("backend/payments/charge.py", "payments/a.py")  # built-in kept
    assert s["custom.blob"] == ("data/x.bin",)
    assert "coverage.unclassified" not in s  # a custom match classifies the file


def test_custom_rule_cannot_shadow_builtin_id():
    policy = Policy(signals=(SignalRuleSpec("code.changed", "x", Risk.LOW, ("**",)),))
    out = sig.evaluate_signals(NOWHERE, cs(f("data/a.bin")), policy)
    assert [s.id for s in out] == ["coverage.unclassified"]


@pytest.mark.parametrize(
    "path,pattern,expected",
    [
        ("a/payments/b.py", "**/payments/**", True),
        ("payments/b.py", "**/payments/**", True),
        ("a/b.py", "*.py", False),
        ("b.py", "*.py", True),
        ("a/b.py", "**/*.py", True),
        ("a/b.py", "a/?.py", True),
        ("a/b.py", "a/[ab].py", True),
        ("a/c.py", "a/[!ab].py", True),
    ],
)
def test_glob_match(path, pattern, expected):
    assert sig.glob_match(path, pattern) is expected


def test_evaluate_is_deterministic():
    change = cs(f("backend/app/auth/refresh.py", added=("x = refresh_token",)), f("docs/a.md"))
    a = sig.evaluate_signals(NOWHERE, change, Policy())
    b = sig.evaluate_signals(NOWHERE, change, Policy())
    assert a == b


def test_scenario_c_signals():
    change = cs(
        f(
            "backend/app/auth/refresh.py",
            added=("    token = payload['refresh_token']", "    claims = jwt.decode(token)"),
        )
    )
    assert set(ids(change)) == {
        "code.changed",
        "security.auth_path",
        "security.sensitive_identifier",
    }


def test_scenario_b_signals():
    change = cs(f("frontend/src/components/LoginForm.tsx"), f("frontend/src/styles/login.css"))
    s = ids(change)
    assert set(s) == {"code.changed", "frontend.ui"}
    assert s["frontend.ui"] == (
        "frontend/src/components/LoginForm.tsx",
        "frontend/src/styles/login.css",
    )


# --------------------------------------------------------------------------- build.config_changed


PKG = {
    "name": "web",
    "version": "1.0.0",
    "scripts": {"lint": "eslint .", "test": "vitest"},
    "dependencies": {"react": "18.2.0"},
    "devDependencies": {"vitest": "1.0.0"},
}

PYPROJECT = """\
[project]
name = "app"
dependencies = ["fastapi==0.110"]

[project.optional-dependencies]
dev = ["pytest==8.0"]

[tool.ruff]
line-length = 100
"""


@pytest.fixture
def repo(tmp_path):
    r = GitRepo(tmp_path / "r")
    r.write("frontend/package.json", json.dumps(PKG, indent=2))
    r.write("backend/pyproject.toml", PYPROJECT)
    base = r.commit("base")
    return r, base


def _build(r: GitRepo, base: str, *files: ChangedFile) -> bool:
    return "build.config_changed" in ids(cs(*files, base=base), repo=r.root)


def test_package_json_dependency_bump_is_not_build_config(repo):
    r, base = repo
    pkg = json.loads(json.dumps(PKG))
    pkg["dependencies"]["react"] = "18.3.1"
    pkg["devDependencies"]["vitest"] = "2.0.0"
    r.write("frontend/package.json", json.dumps(pkg, indent=4))  # reformatting is not config
    assert not _build(r, base, f("frontend/package.json"))


def test_package_json_script_edit_is_build_config(repo):
    r, base = repo
    pkg = json.loads(json.dumps(PKG))
    pkg["scripts"]["test"] = "exit 0"
    r.write("frontend/package.json", json.dumps(pkg))
    assert _build(r, base, f("frontend/package.json"))


def test_pyproject_dependency_vs_tool_edit(repo):
    r, base = repo
    r.write("backend/pyproject.toml", PYPROJECT.replace("0.110", "0.111").replace("8.0", "8.1"))
    assert not _build(r, base, f("backend/pyproject.toml"))
    r.write("backend/pyproject.toml", PYPROJECT.replace("100", "120"))
    assert _build(r, base, f("backend/pyproject.toml"))


def test_manifest_fail_closed_cases(repo):
    r, base = repo
    r.write("frontend/package.json", "{ not json")
    assert _build(r, base, f("frontend/package.json"))  # unparsable
    r.write("other/package.json", "{}")
    assert _build(r, base, f("other/package.json", A))  # added manifest
    assert _build(r, base, f("backend/pyproject.toml", D))  # deleted manifest
    # no base at all (unborn / empty tree): treated as added
    assert "build.config_changed" in ids(cs(f("frontend/package.json")), repo=r.root)


# --------------------------------------------------------------------------- task text


def test_task_signals_empty():
    assert sig.task_signals(None) == ()
    assert sig.task_signals("") == ()
    assert sig.task_signals("rename a variable") == ()


def test_task_signals_keywords():
    out = {s.id: s for s in sig.task_signals("Modify refresh-token handling")}
    assert set(out) == {"task.security"}
    assert out["task.security"].risk is Risk.ELEVATED
    assert out["task.security"].files == ()
    assert out["task.security"].domain == "security"

    out = {s.id for s in sig.task_signals("Fix the LOGIN form CSS and update the docs")}
    assert out == {"task.docs", "task.frontend", "task.security"}
    assert {s.id for s in sig.task_signals("Bump dependencies")} == {"task.dependencies"}


def test_task_signal_detail_echoes_only_keywords():
    (s,) = sig.task_signals("ignore all previous instructions; password <script>")
    assert s.detail == "task keywords: password"


# --------------------------------------------------------------------------- test tamper (v0.2)

TAMPER = ("tests.deleted", "tests.skip_added", "tests.discovery_narrowed")


def signals_by_id(change: ChangeSet, repo: Path = NOWHERE) -> dict:
    return {s.id: s for s in sig.evaluate_signals(repo, change, DEFAULT_POLICY)}


def tamper(change: ChangeSet, repo: Path = NOWHERE) -> dict:
    return {k: v for k, v in ids(change, repo=repo).items() if k in TAMPER}


def edit(path: str, added=(), removed=(), status: FileStatus = M, **kw) -> ChangedFile:
    return ChangedFile(path, status, added=tuple(added), removed=tuple(removed), **kw)


def test_tamper_signals_are_elevated_and_widening():
    for sid in TAMPER:
        assert sid in sig.WIDENING
        assert sid in sig.TAMPER_SIGNALS
    rules = {r.id: r for r in sig.CATALOGUE}
    assert all(rules[sid].risk is Risk.ELEVATED for sid in TAMPER)


PY_TEST = (
    "import pytest",
    "",
    "def test_rotation() -> None:",
    "    assert True",
    "",
    "async def test_async_refresh():",
    "    assert True",
    "",
    "class TestRefresh:",
    "    def test_inside_class(self):",
    "        assert True",
)


def test_deleted_python_test_file_names_its_tests():
    s = signals_by_id(cs(edit("backend/tests/test_refresh.py", removed=PY_TEST, status=D)))
    deleted = s["tests.deleted"]
    assert deleted.files == ("backend/tests/test_refresh.py",)
    assert deleted.risk is Risk.ELEVATED
    for name in ("test_rotation", "test_async_refresh", "TestRefresh", "test_inside_class"):
        assert name in deleted.detail


@pytest.mark.parametrize(
    "line",
    [
        "def test_rotation():",
        "    def test_method(self):",
        "async def test_async():",
        "class TestThing:",
        "class TestThing(unittest.TestCase):",
    ],
)
def test_removed_python_definition_is_a_deletion(line):
    assert "tests.deleted" in tamper(cs(edit("tests/test_x.py", removed=(line,))))


@pytest.mark.parametrize(
    "line",
    [
        'it("renders", () => {',
        "test('submits', async () => {",
        "describe(`LoginForm`, () => {",
        'test.each([[1], [2]])("adds %i", (n) => {',
        "  it('nested', () => {})",
    ],
)
def test_removed_js_definition_is_a_deletion(line):
    path = "frontend/src/components/LoginForm.test.tsx"
    assert "tests.deleted" in tamper(cs(edit(path, removed=(line,))))


def test_changed_signature_is_not_a_deletion():
    f = edit(
        "tests/test_x.py",
        removed=("def test_rotation():",),
        added=("def test_rotation() -> None:",),
    )
    assert tamper(cs(f)) == {}


def test_moving_a_test_between_files_is_not_a_deletion():
    src = edit("backend/tests/test_refresh.py", removed=("def test_rotation():", "    pass"))
    dst = edit(
        "backend/tests/test_rotation.py", added=("def test_rotation():", "    pass"), status=A
    )
    assert tamper(cs(src, dst)) == {}


def test_moving_a_test_into_a_deleted_files_place_is_not_a_deletion():
    src = edit("tests/test_a.py", removed=PY_TEST, status=D)
    dst = edit("tests/test_b.py", added=PY_TEST, status=A)
    assert tamper(cs(src, dst)) == {}


def test_partial_move_names_only_the_missing_test():
    src = edit("tests/test_a.py", removed=("def test_moved():", "def test_dropped():"))
    dst = edit("tests/test_b.py", added=("def test_moved():",))
    detail = signals_by_id(cs(src, dst))["tests.deleted"].detail
    assert "test_dropped" in detail
    assert "test_moved" not in detail


def test_a_move_counts_each_definition_once():
    # Two files each drop a test_x; only one is re-added elsewhere.
    a = edit("tests/test_a.py", removed=("def test_x():",))
    b = edit("tests/test_b.py", removed=("def test_x():",))
    c = edit("tests/test_c.py", added=("def test_x():",), status=A)
    assert tamper(cs(a, b, c)) == {"tests.deleted": ("tests/test_b.py",)}


def test_rename_between_test_paths_is_not_a_deletion():
    f = ChangedFile("tests/unit/test_b.py", R, old_path="tests/test_a.py")
    assert tamper(cs(f)) == {}


def test_turning_it_into_it_skip_is_a_skip_not_a_deletion():
    f = edit(
        "src/a.test.ts",
        removed=('it("works", () => {',),
        added=('it.skip("works", () => {',),
    )
    assert tamper(cs(f)) == {"tests.skip_added": ("src/a.test.ts",)}


def test_deleted_test_file_of_unparsed_language_fires():
    assert "tests.deleted" in tamper(
        cs(edit("pkg/x_test.go", removed=("func TestX(t *T) {}",), status=D))
    )


def test_deleted_helper_without_tests_is_not_a_deletion():
    helper = edit(
        "backend/tests/helpers.py", removed=("def make_token():", "    return 1"), status=D
    )
    assert tamper(cs(helper)) == {}


def test_hidden_test_lines_fail_closed():
    s = tamper(cs(edit("tests/test_x.py", removed=("x = 1",), truncated=True)))
    assert set(s) == {"tests.deleted", "tests.skip_added"}
    assert "tests.deleted" in tamper(cs(edit("tests/test_x.py", status=D, truncated=True)))
    assert "tests.deleted" in tamper(cs(edit("tests/test_x.py", binary=True)))


def test_rename_out_of_test_paths_is_a_deletion(tmp_path):
    r = GitRepo(tmp_path / "r")
    r.write("tests/test_a.py", "\n".join(PY_TEST) + "\n")
    base = r.commit("base")
    f = ChangedFile("archive/a.py", R, old_path="tests/test_a.py")
    s = signals_by_id(cs(f, base=base), repo=r.root)
    assert "test_rotation" in s["tests.deleted"].detail
    assert "(renamed from tests/test_a.py)" in s["tests.deleted"].detail
    assert set(s["tests.deleted"].files) == {"archive/a.py", "tests/test_a.py"}


SKIP_MARKERS_PY = [
    "@pytest.mark.skip",
    '@pytest.mark.skip(reason="flaky")',
    "@pytest.mark.skipif(sys.platform == 'win32', reason='x')",
    "@pytest.mark.xfail(strict=False)",
    "pytestmark = pytest.mark.skip",
    "    pytest.skip('not today')",
    "    pytest.xfail('known')",
    "@unittest.skip('later')",
    "@unittest.skipIf(True, 'x')",
    "@unittest.skipUnless(False, 'x')",
    "@skip('x')",
    "@mark.xfail",
    "    raise unittest.SkipTest('x')",
    "    self.skipTest('x')",
]


@pytest.mark.parametrize("line", SKIP_MARKERS_PY)
def test_python_skip_markers(line):
    assert "tests.skip_added" in tamper(cs(edit("backend/tests/test_x.py", added=(line,))))


SKIP_MARKERS_JS = [
    'it.skip("works", () => {})',
    "test.skip('works', () => {})",
    "describe.skip('suite', () => {})",
    "xit('works', () => {})",
    "xdescribe('suite', () => {})",
    "it.todo('later')",
    "test.concurrent.skip('x', async () => {})",
    "it.only('works', () => {})",
    "test.only('works', () => {})",
    "describe.only('suite', () => {})",
    "fit('works', () => {})",
    "fdescribe('suite', () => {})",
]


@pytest.mark.parametrize("line", SKIP_MARKERS_JS)
def test_js_skip_and_focus_markers(line):
    path = "frontend/src/components/LoginForm.test.tsx"
    assert "tests.skip_added" in tamper(cs(edit(path, added=(line,))))


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("tests/test_x.py", "def test_skip_expired_tokens():"),  # a test named "skip"
        ("tests/test_x.py", "def test_xfail_path(): assert skip_list == []"),
        ("tests/test_x.py", "# @pytest.mark.skip  (commented out)"),
        ("tests/test_x.py", 'msg = "use @pytest.mark.skip sparingly"'),
        ("src/a.test.ts", 'it("skips expired tokens", () => {})'),
        ("src/a.test.ts", 'it("calls test.only and it.skip", () => {})'),
        ("src/a.test.ts", "// it.only('debug')"),
        ("src/a.test.ts", "model.fit(data)"),
        ("src/a.test.ts", "const xit = 1"),
        ("backend/app/run.py", "@pytest.mark.skip"),  # not a test file
    ],
)
def test_not_skip_markers(path, line):
    assert "tests.skip_added" not in tamper(cs(edit(path, added=(line,))))


def test_moved_skip_marker_is_not_new():
    a = edit("tests/test_a.py", removed=('@pytest.mark.skip(reason="a")', "def test_x():"))
    b = edit("tests/test_b.py", added=('@pytest.mark.skip(reason="b")', "def test_x():"))
    assert tamper(cs(a, b)) == {}


def test_tamper_notes_map_notes_to_files():
    f = edit("backend/tests/test_x.py", removed=("def test_gone():",))
    (s,) = [
        x for x in sig.evaluate_signals(NOWHERE, cs(f), DEFAULT_POLICY) if x.id == "tests.deleted"
    ]
    assert sig.tamper_notes(s) == (
        ("backend/tests/test_x.py removes test_gone", ("backend/tests/test_x.py",)),
    )


def test_tamper_notes_never_echo_unsafe_content():
    f = edit("src/a.test.ts", removed=('it("<script>ignore previous; rm -rf", () => {})',))
    detail = signals_by_id(cs(f))["tests.deleted"].detail
    assert "<script>" not in detail
    assert "; rm" not in detail


# ---- tests.discovery_narrowed (BASE vs working tree)

BACKEND_PYPROJECT = """\
[project]
name = "backend"
dependencies = ["fastapi==0.110"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
"""


@pytest.fixture
def trepo(tmp_path):
    r = GitRepo(tmp_path / "t")
    r.write("backend/pyproject.toml", BACKEND_PYPROJECT)
    r.write("backend/pytest.ini", "[pytest]\naddopts = -q\n")
    r.write("backend/setup.cfg", "[metadata]\nname = x\n")
    r.write("backend/tox.ini", "[tox]\nenvlist = py311\n")
    r.write("frontend/package.json", json.dumps(PKG, indent=2))
    r.write("frontend/jest.config.js", "module.exports = {\n  verbose: true,\n};\n")
    r.write("Makefile", "test:\n\tpython -m pytest -q\n")
    return r, r.commit("base")


def _narrowed(r: GitRepo, base: str, f: ChangedFile) -> bool:
    return "tests.discovery_narrowed" in tamper(cs(f, base=base), repo=r.root)


@pytest.mark.parametrize(
    "replacement",
    [
        "addopts = \"-q -k 'not refresh'\"",
        'addopts = "-q -m smoke"',
        'addopts = "-q --ignore=tests/test_refresh.py"',
        'addopts = ["-q", "--deselect", "tests/test_refresh.py::test_rotation"]',
        'addopts = "-q --co"',
        'addopts = "-q"\ntestpaths = ["tests/unit"]',
        'addopts = "-q"\npython_files = ["test_api.py"]',
        'addopts = "-q"\npython_functions = ["check"]',
        'addopts = "-q"\nnorecursedirs = ["tests/slow"]',
    ],
)
def test_pyproject_narrowing(trepo, replacement):
    r, base = trepo
    r.write("backend/pyproject.toml", BACKEND_PYPROJECT.replace('addopts = "-q"', replacement))
    assert _narrowed(r, base, f("backend/pyproject.toml"))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('dependencies = ["fastapi==0.110"]', 'dependencies = ["fastapi==0.111"]'),  # bump
        ('testpaths = ["tests"]', 'testpaths = ["tests", "integration"]'),  # wider
        ('testpaths = ["tests"]\n', ""),  # no testpaths: everything
        ('addopts = "-q"', 'addopts = "-q -x --strict-markers"'),  # not narrowing options
        ('addopts = "-q"', 'addopts = "-q"\npython_files = ["test_*.py", "*_test.py", "t_*.py"]'),
        ('addopts = "-q"', 'addopts = "-q"\nnorecursedirs = [".*", "build"]'),  # subset of defaults
    ],
)
def test_pyproject_not_narrowing(trepo, old, new):
    r, base = trepo
    assert old in BACKEND_PYPROJECT
    r.write("backend/pyproject.toml", BACKEND_PYPROJECT.replace(old, new))
    assert not _narrowed(r, base, f("backend/pyproject.toml"))


def test_removing_a_narrowing_option_is_widening(tmp_path):
    r = GitRepo(tmp_path / "w")
    r.write("pytest.ini", "[pytest]\naddopts = -q -k 'not slow'\n")
    base = r.commit("base")
    r.write("pytest.ini", "[pytest]\naddopts = -q\n")
    assert not _narrowed(r, base, f("pytest.ini"))


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("backend/pytest.ini", "[pytest]\naddopts = -q -k 'not refresh'\n"),
        ("backend/pytest.ini", "[pytest]\naddopts = -q\ntestpaths = tests/unit\n"),
        ("backend/setup.cfg", "[metadata]\nname = x\n\n[tool:pytest]\naddopts = -m 'not slow'\n"),
        ("backend/tox.ini", "[tox]\nenvlist = py311\n\n[pytest]\naddopts = --deselect t.py::x\n"),
        ("backend/pytest.ini", "addopts = -k x\n"),  # unparsable: fail closed
    ],
)
def test_ini_narrowing(trepo, path, content):
    r, base = trepo
    r.write(path, content)
    assert _narrowed(r, base, f(path))


def test_added_pytest_config_with_testpaths_narrows(trepo):
    r, base = trepo
    r.write("backend/tests/pytest.ini", "[pytest]\ntestpaths = unit\n")
    assert _narrowed(r, base, f("backend/tests/pytest.ini", A))


def test_ini_without_pytest_section_is_not_narrowing(trepo):
    r, base = trepo
    r.write("backend/setup.cfg", "[metadata]\nname = y\n\n[flake8]\nignore = E501\n")
    assert not _narrowed(r, base, f("backend/setup.cfg"))


def _pkg(r: GitRepo, **changes) -> None:
    pkg = json.loads(json.dumps(PKG))
    for k, v in changes.items():
        if k == "scripts":
            pkg["scripts"].update(v)
        else:
            pkg[k] = v
    r.write("frontend/package.json", json.dumps(pkg, indent=2))


@pytest.mark.parametrize(
    "changes",
    [
        {"jest": {"testPathIgnorePatterns": ["/node_modules/", "LoginForm"]}},
        {"jest": {"testMatch": ["**/*.unit.test.ts"]}},
        {"jest": {"testRegex": "smoke\\.test\\.ts$"}},
        {"scripts": {"test": "vitest -t login"}},
        {"scripts": {"test": "jest --testPathIgnorePatterns LoginForm"}},
        {"scripts": {"test": "jest --passWithNoTests"}},
        {"scripts": {"test:py": "cd ../backend && python -m pytest -k 'not refresh'"}},
        {"scripts": {"test:py": "pytest --deselect=tests/test_a.py::test_x"}},
    ],
)
def test_package_json_narrowing(trepo, changes):
    r, base = trepo
    _pkg(r, **changes)
    assert _narrowed(r, base, f("frontend/package.json"))


@pytest.mark.parametrize(
    "changes",
    [
        {"devDependencies": {"vitest": "2.0.0"}},
        {"jest": {"verbose": True}},
        {"jest": {"testPathIgnorePatterns": ["/node_modules/"]}},  # the default
        {"scripts": {"test:py": "python -m pytest -q"}},  # -m belongs to python, not pytest
        {"scripts": {"lint": "eslint --max-warnings 0 ."}},
    ],
)
def test_package_json_not_narrowing(trepo, changes):
    r, base = trepo
    _pkg(r, **changes)
    assert not _narrowed(r, base, f("frontend/package.json"))


def test_widening_jest_test_match_is_not_narrowing(tmp_path):
    r = GitRepo(tmp_path / "j")
    r.write("package.json", json.dumps({"jest": {"testMatch": ["**/*.test.ts"]}}))
    base = r.commit("base")
    r.write("package.json", json.dumps({"jest": {"testMatch": ["**/*.test.ts", "**/*.spec.ts"]}}))
    assert not _narrowed(r, base, f("package.json"))
    r.write("package.json", json.dumps({"jest": {"testMatch": ["**/*.spec.ts"]}}))
    assert _narrowed(r, base, f("package.json"))


def test_js_config_key_change_narrows(trepo):
    r, base = trepo
    r.write(
        "frontend/jest.config.js",
        "module.exports = {\n  verbose: true,\n  testPathIgnorePatterns: [\n"
        '    "/node_modules/",\n    "LoginForm",\n  ],\n};\n',
    )
    added = ("  testPathIgnorePatterns: [", '    "/node_modules/",', '    "LoginForm",', "  ],")
    assert _narrowed(r, base, edit("frontend/jest.config.js", added=added))
    # an entry added to an existing multi-line array also counts
    assert _narrowed(r, base, edit("frontend/jest.config.js", added=('    "LoginForm",',)))


def test_js_config_unrelated_change_is_not_narrowing(trepo):
    r, base = trepo
    r.write("frontend/jest.config.js", "module.exports = {\n  verbose: false,\n};\n")
    f_ = edit(
        "frontend/jest.config.js", added=("  verbose: false,",), removed=("  verbose: true,",)
    )
    assert not _narrowed(r, base, f_)


def test_makefile_narrowing(trepo):
    r, base = trepo
    narrowing = edit(
        "Makefile",
        added=("\tpython -m pytest -q --ignore=tests/test_refresh.py",),
        removed=("\tpython -m pytest -q",),
    )
    assert _narrowed(r, base, narrowing)
    ordinary = edit(
        "Makefile", added=("\tpython -m pytest -q -x",), removed=("\tpython -m pytest -q",)
    )
    assert not _narrowed(r, base, ordinary)
    unchanged = edit("Makefile", added=("\t@pytest -k smoke",), removed=("\tpytest -k smoke",))
    assert not _narrowed(r, base, unchanged)


def test_command_narrowing_parses_runner_options():
    assert sig.command_narrowing("python -m pytest -q") == {}
    assert sig.command_narrowing("pytest -kfoo -m 'not slow'") == {
        ("-k", "foo"): 1,
        ("-m", "not slow"): 1,
    }
    assert sig.command_narrowing("npm ci && npx jest -t login") == {("-t", "login"): 1}
    assert sig.command_narrowing("eslint -m x") == {}


# --------------------------------------------------------------------------- ci / agent config


def _ids_for(path: str, status=FileStatus.MODIFIED) -> set[str]:
    change = ChangeSet(
        base=None,
        head=None,
        tree_digest="0" * 40,
        files=(ChangedFile(path, status, added=("x",)),),
        uncommitted=True,
    )
    return {s.id for s in sig.evaluate_signals(Path("."), change, Policy())}


def test_ci_changed_fires_for_ci_config():
    for p in (".github/workflows/ci.yml", ".gitlab-ci.yml", ".circleci/config.yml"):
        assert "ci.changed" in _ids_for(p), p
    assert "ci.changed" not in _ids_for("docs/ci.md")
    assert "ci.changed" in sig.WIDENING


def test_agent_config_changed_fires_for_agent_files():
    for p in (
        "AGENTS.md",
        "backend/CLAUDE.md",
        ".claude/settings.json",
        ".mcp.json",
        ".cursor/rules/x.mdc",
        ".github/copilot-instructions.md",
    ):
        assert "agent.config_changed" in _ids_for(p), p
    assert "agent.config_changed" not in _ids_for("docs/agents.md")


# ---- v0.2 fail-open review #7 / #8: evasions of the tamper signals

ALIASED_SKIPS_PY = [
    ("import pytest as pt", "@pt.mark.skip"),
    ("from pytest import mark as m", "@m.skip"),
    ("from pytest import mark as m", "@m.xfail(strict=False)"),
    ("import pytest", "@pytest .mark .skip"),
    ("import pytest", "@pytest.mark.ｓｋｉｐ"),  # fullwidth: NFKC makes it `skip`
    ("import pytest", "@ｐｙｔｅｓｔ.mark.skip"),
    ("import pytest", 'pytest.importorskip("no_such_module")'),
    ("from pytest import importorskip", 'importorskip("x")'),
    ("from pytest import skip as later", "    later('x')"),
    ("from pytest import *", "@mark.skipif(True, reason='x')"),
    ("import pytest", "m = pytest.mark", "s = m.skipif", "@s(True, reason='x')"),
    ("from pytest import (", "    mark as m,", ")", "@m.skip"),
    ("import unittest as ut", "@ut.skip('x')"),
    ("from unittest import skip as later", "@later('x')"),
    ("import unittest", "@unittest.expectedFailure"),
    ("import pytest", 'deco = getattr(pytest.mark, "skip")'),
    ("__test__ = False",),
    ("class TestX:", "    __test__ = 0"),
]


@pytest.mark.parametrize("lines", ALIASED_SKIPS_PY)
def test_aliased_and_obfuscated_python_skips(lines):
    assert "tests.skip_added" in tamper(cs(edit("backend/tests/test_x.py", added=lines)))


@pytest.mark.parametrize(
    "lines",
    [
        ("import pytest as pt", "@pt.mark.parametrize('x', [1, 2])"),
        ("from pytest import mark as m", "@m.slow"),
        ("from pytest import approx", "assert 0.1 + 0.2 == approx(0.3)"),
        ("obj = Thing()", "obj.skip()"),  # not derived from pytest/unittest
        ("def test_importorskip_name():",),
        ("__test__ = True",),
        ("if x == __test__:",),
        ("from unittest import mock", "@mock.patch('a.b')"),
        ("import pytest", "x = pytest . approx(1)"),
    ],
)
def test_aliases_without_skip_are_quiet(lines):
    assert "tests.skip_added" not in tamper(cs(edit("backend/tests/test_x.py", added=lines)))


def test_alias_from_base_content(tmp_path):
    r = GitRepo(tmp_path / "a")
    r.write("tests/test_a.py", "import pytest as pt\n\n\ndef test_x():\n    pass\n")
    base = r.commit("base")
    f_ = edit("tests/test_a.py", added=("@pt.mark.skip",))
    s = signals_by_id(cs(f_, base=base), repo=r.root)
    assert "tests/test_a.py adds pytest.mark.skip" in s["tests.skip_added"].detail


def test_skip_markers_resolves_given_aliases():
    names = sig.py_aliases(["import pytest as pt"])
    assert sig.skip_markers("@pt.mark.skip", "py", names) == ("pytest.mark.skip",)
    assert sig.skip_markers("@pt.mark.skip", "py") == ()  # no alias known
    assert sig.skip_markers("@pytest .mark .skip", "py") == ("pytest.mark.skip",)


def test_moved_aliased_marker_is_not_new():
    a = edit("tests/test_a.py", removed=("@pytest.mark.skip", "def test_x():"))
    b = edit("tests/test_b.py", added=("@pytest .mark .skip", "def test_x():"))
    assert tamper(cs(a, b)) == {}


@pytest.mark.parametrize(
    "lines",
    [
        ('const focus = "only";', 'test[focus]("smoke", () => {});'),
        ('test["only"]("smoke", () => {});',),
        ("const t = test;", 't.only("smoke", () => {});'),
        ('import { test as t } from "vitest";', 't.skip("smoke", () => {});'),
        ("const { only } = test;", 'only("smoke", () => {});'),
        ('it .skip("smoke", () => {});',),
    ],
)
def test_js_computed_and_aliased_markers(lines):
    path = "frontend/src/components/LoginForm.test.tsx"
    assert "tests.skip_added" in tamper(cs(edit(path, added=lines)))


@pytest.mark.parametrize(
    "lines",
    [
        ("const tests = [1, 2];", "expect(tests[0]).toBe(1);"),
        ("const t = test;", 't("smoke", () => {});'),
        ("const { each } = test;",),
        ('import { test, expect } from "vitest";',),
    ],
)
def test_js_aliases_without_markers_are_quiet(lines):
    path = "frontend/src/components/LoginForm.test.tsx"
    assert "tests.skip_added" not in tamper(cs(edit(path, added=lines)))


# tests.deleted: a "move" only counts when the destination is collected

COLLECT_BASE = {
    "backend/pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    "backend/tests/test_refresh.py": (
        "def test_keep():\n    pass\n\n\ndef test_moved():\n    pass\n"
    ),
}


@pytest.fixture
def crepo(tmp_path):
    r = GitRepo(tmp_path / "c")
    for path, text in COLLECT_BASE.items():
        r.write(path, text)
    return r, r.commit("base")


def _moved_to(r: GitRepo, base: str, dest: str) -> dict:
    src = edit("backend/tests/test_refresh.py", removed=("def test_moved():", "    pass"))
    dst = edit(dest, added=("def test_moved():", "    pass"), status=A)
    return tamper(cs(src, dst, base=base), repo=r.root)


@pytest.mark.parametrize(
    "dest",
    [
        "backend/attic/test_refresh.py",  # outside testpaths
        "backend/tests/build/test_refresh.py",  # default norecursedirs
        "backend/tests/.cache/test_refresh.py",
        "backend/tests/node_modules/test_refresh.py",
        "backend/tests/x.egg/test_refresh.py",
        "backend/tests/helpers.py",  # not python_files
        "backend/tests/conftest.py",
    ],
)
def test_move_to_an_uncollected_destination_is_a_deletion(crepo, dest):
    r, base = crepo
    assert "tests.deleted" in _moved_to(r, base, dest)


@pytest.mark.parametrize(
    "dest",
    [
        "backend/tests/test_moved.py",
        "backend/tests/unit/test_moved.py",
        "backend/tests/unit/moved_test.py",
        "backend/tests/integration/test_moved.py",
    ],
)
def test_move_to_a_collected_destination_is_not_a_deletion(crepo, dest):
    r, base = crepo
    assert _moved_to(r, base, dest) == {}


def test_rename_out_of_testpaths_is_a_deletion(crepo):
    r, base = crepo
    f_ = ChangedFile("backend/attic/test_refresh.py", R, old_path="backend/tests/test_refresh.py")
    s = signals_by_id(cs(f_, base=base), repo=r.root)
    assert "test_moved" in s["tests.deleted"].detail
    f_ = ChangedFile("backend/tests/unit/test_refresh.py", R, old_path=f_.old_path)
    assert "tests.deleted" not in tamper(cs(f_, base=base), repo=r.root)


@pytest.mark.parametrize(
    ("config_path", "config", "collected", "uncollected"),
    [
        (
            "backend/pytest.toml",  # pytest >= 9, beats pyproject.toml
            '[pytest]\ntestpaths = ["checks"]\n',
            "backend/checks/test_m.py",
            "backend/tests/test_m.py",
        ),
        (
            "backend/pytest.ini",
            "[pytest]\ntestpaths = tests/u*\npython_files = check_*.py\n",
            "backend/tests/unit/check_m.py",
            "backend/tests/unit/test_m.py",
        ),
        (
            "backend/.pytest.ini",
            "[pytest]\ntestpaths = tests\nnorecursedirs = legacy\n",
            "backend/tests/build/test_m.py",  # norecursedirs replaced: build is collected
            "backend/tests/legacy/test_m.py",
        ),
    ],
)
def test_collection_follows_the_base_config(tmp_path, config_path, config, collected, uncollected):
    r = GitRepo(tmp_path / "k")
    r.write("backend/tests/test_refresh.py", COLLECT_BASE["backend/tests/test_refresh.py"])
    r.write("backend/pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    r.write(config_path, config)
    base = r.commit("base")
    assert _moved_to(r, base, collected) == {}
    assert "tests.deleted" in _moved_to(r, base, uncollected)


def test_collection_without_any_config_uses_pytest_defaults(tmp_path):
    r = GitRepo(tmp_path / "n")
    r.write("README.md", "x\n")
    base = r.commit("base")
    src = edit("tests/test_a.py", removed=("def test_x():",))
    ok = edit("lib/test_b.py", added=("def test_x():",), status=A)
    assert tamper(cs(src, ok, base=base), repo=r.root) == {}
    hidden = edit("tests/dist/test_b.py", added=("def test_x():",), status=A)
    assert "tests.deleted" in tamper(cs(src, hidden, base=base), repo=r.root)


def test_move_to_an_uncollected_js_file_is_a_deletion():
    src = edit("src/a.test.ts", removed=('it("works", () => {})',))
    helper = edit("src/__fixtures__/helpers.ts", added=('it("works", () => {})',), status=A)
    assert "tests.deleted" not in tamper(cs(src, edit("src/b.test.ts", added=helper.added)))
    assert "tests.deleted" in tamper(cs(src, edit("tests/helpers.ts", added=helper.added)))


# tests.discovery_narrowed: jest keys, pytest.toml, scripts and Makefiles


@pytest.mark.parametrize(
    "changes",
    [
        {"jest": {"roots": ["<rootDir>/nothing"]}},
        {"jest": {"rootDir": "src/empty"}},
        {"jest": {"modulePathIgnorePatterns": ["components"]}},
        {"jest": {"testNamePattern": "smoke"}},
        {"jest": {"projects": ["<rootDir>/a"]}},
        {"scripts": {"test": "vitest run src/login"}},
        {"scripts": {"test": "vitest related src/a.ts"}},
        {"scripts": {"test": "VITEST_SEGFAULT_RETRY=1 vitest"}},
        {"scripts": {"test:unit": "npm test -- -t login"}},
        {"scripts": {"test:unit": "pnpm test src/login"}},
    ],
)
def test_package_json_v02_narrowing(trepo, changes):
    r, base = trepo
    _pkg(r, **changes)
    assert _narrowed(r, base, f("frontend/package.json"))


@pytest.mark.parametrize(
    "changes",
    [
        {"jest": {"roots": ["<rootDir>", "<rootDir>/extra"]}},  # wider
        {"jest": {"roots": ["<rootDir>"]}},  # the default
        {"jest": {"modulePathIgnorePatterns": []}},
        {"scripts": {"test": "vitest run"}},
        {"scripts": {"test": "vitest --reporter verbose"}},
        {"scripts": {"test:unit": "npm test"}},
        {"scripts": {"build": "npm run compile -- --mode production"}},
        {"scripts": {"setup": "pnpm install && pip install pytest pytest-cov"}},
    ],
)
def test_package_json_v02_not_narrowing(trepo, changes):
    r, base = trepo
    _pkg(r, **changes)
    assert not _narrowed(r, base, f("frontend/package.json"))


def test_jest_config_module_roots_change_narrows(trepo):
    r, base = trepo
    r.write("frontend/jest.config.js", "module.exports = {\n  roots: ['<rootDir>/x'],\n};\n")
    assert _narrowed(r, base, edit("frontend/jest.config.js", added=("  roots: ['<rootDir>/x'],",)))


@pytest.mark.parametrize("name", ["pytest.toml", ".pytest.toml"])
def test_pytest_toml_narrowing(trepo, name):
    r, base = trepo
    path = f"backend/{name}"
    r.write(path, '[pytest]\naddopts = ["-k", "not expired"]\n')
    assert _narrowed(r, base, f(path, A))
    r.write(path, '[pytest]\ntestpaths = ["tests/unit"]\n')
    assert _narrowed(r, base, f(path, A))
    r.write(path, '[pytest]\naddopts = ["-q"]\n')
    assert not _narrowed(r, base, f(path, A))
    r.write(path, "[pytest\n")
    assert _narrowed(r, base, f(path, A))  # unparsable: fail closed


def test_addopts_positional_path_narrows(trepo):
    r, base = trepo
    r.write("backend/pyproject.toml", BACKEND_PYPROJECT.replace('"-q"', '"-q tests/unit"'))
    assert _narrowed(r, base, f("backend/pyproject.toml"))


MAKE_BASE = "test:\n\tpython -m pytest -q\n\tnpm test\n"


@pytest.mark.parametrize(
    "after",
    [
        "test:\n\tPYTEST_ADDOPTS='-k smoke' python -m pytest -q\n\tnpm test\n",
        "export PYTEST_ADDOPTS = -k smoke\ntest:\n\tpython -m pytest -q\n\tnpm test\n",
        "test:\n\tpython -m pytest -q\n\tJEST_FILTER=x npm test\n",
        "test:\n\tpython -m pytest -q tests/test_smoke.py\n\tnpm test\n",
        "test:\n\tpython -m pytest -q \\\n\t\t-k smoke\n\tnpm test\n",  # continuation line
        "test:\n\tpython -m pytest -q\n\tnpm test -- -t smoke\n",
        "test:\n\tpython -m pytest -q\n\tnpm test -- --testNamePattern smoke\n",
        "test:\n\tpython -m pytest -q\n\tnpm run test -- src/login\n",
        "test:\n\tpython -m pytest -q\n\tyarn test -t smoke\n",
        "test:\n\tpython -m pytest -q\n\tnpx jest src/login\n",
    ],
)
def test_makefile_v02_narrowing(tmp_path, after):
    r = GitRepo(tmp_path / "m")
    r.write("Makefile", MAKE_BASE)
    base = r.commit("base")
    r.write("Makefile", after)
    before_lines, after_lines = MAKE_BASE.splitlines(), after.splitlines()
    f_ = edit(
        "Makefile",
        added=tuple(ln for ln in after_lines if ln not in before_lines),
        removed=tuple(ln for ln in before_lines if ln not in after_lines),
    )
    assert _narrowed(r, base, f_)


@pytest.mark.parametrize(
    "after",
    [
        MAKE_BASE + "\nlint:\n\truff check .\n",
        "test:\n\t@python -m pytest -q\n\tnpm test\n",  # same command
        "test:\n\tpython -m pytest -q -x --cov app\n\tnpm test\n",
        "test:\n\tpython -m pytest -q > out.txt 2>&1\n\tnpm test\n",
        "test:\n\tpython -m pytest -q -p no:cacheprovider\n\tnpm test\n",
        "test:\n\tpython -m pytest -q\n\tnpm test -- --ci\n",
        "test:\n\tpython -m pytest -q\n\tnpm ci && npm test\n",
        "install:\n\tpip install pytest pytest-cov\n" + MAKE_BASE,
    ],
)
def test_makefile_v02_not_narrowing(tmp_path, after):
    r = GitRepo(tmp_path / "m")
    r.write("Makefile", MAKE_BASE)
    base = r.commit("base")
    r.write("Makefile", after)
    before_lines, after_lines = MAKE_BASE.splitlines(), after.splitlines()
    f_ = edit(
        "Makefile",
        added=tuple(ln for ln in after_lines if ln not in before_lines),
        removed=tuple(ln for ln in before_lines if ln not in after_lines),
    )
    assert not _narrowed(r, base, f_)


def test_shell_script_narrowing(tmp_path):
    r = GitRepo(tmp_path / "s")
    r.write("scripts/test.sh", "#!/bin/sh\npytest -q\n")
    base = r.commit("base")
    r.write("scripts/test.sh", "#!/bin/sh\npytest -q tests/test_smoke.py\n")
    f_ = edit("scripts/test.sh", added=("pytest -q tests/test_smoke.py",), removed=("pytest -q",))
    assert _narrowed(r, base, f_)


def test_command_narrowing_v02():
    assert sig.command_narrowing("pytest -q tests/test_a.py") == {
        (sig.POSITIONAL, "tests/test_a.py"): 1
    }
    assert sig.command_narrowing("npm test -- -t smoke") == {("-t", "smoke"): 1}
    assert sig.command_narrowing("npm test -t smoke") == {}  # npm keeps it before `--`
    assert sig.command_narrowing("pnpm exec jest -t x") == {("-t", "x"): 1}
    assert sig.command_narrowing("vitest run") == {}
    assert sig.command_narrowing("pip install pytest") == {}
    (key,) = sig.command_narrowing("PYTEST_ADDOPTS='-k x' pytest")
    assert key[0] == "PYTEST_ADDOPTS="
