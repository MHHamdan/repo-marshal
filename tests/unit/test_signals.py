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
