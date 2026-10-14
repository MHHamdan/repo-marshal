"""Unit tests for context selection (roadmap M8; architecture §4.4)."""

from __future__ import annotations

import os

import pytest

from helpers import GitRepo
from repomarshal.context import select_context
from repomarshal.model import ChangedFile, ChangeSet, FileStatus, Risk, Signal

M, A, D, R = FileStatus.MODIFIED, FileStatus.ADDED, FileStatus.DELETED, FileStatus.RENAMED

WEBAPP = {
    "AGENTS.md": "# Agents\n\n## Commands\n- Test: `make test`\n",
    "docs/install.md": "# Install\n\nRun the thing.\n",
    "docs/adr/0003-token-refresh.md": "# ADR 3: token refresh\n\nRotate refresh tokens.\n",
    "frontend/package.json": '{"scripts": {"lint": "eslint ."}}\n',
    "frontend/package-lock.json": "{}\n",
    "frontend/src/components/LoginForm.tsx": "export const LoginForm = () => null;\n",
    "frontend/src/components/LoginForm.test.tsx": "test('renders', () => {});\n",
    "frontend/src/styles/login.css": ".login { color: red; }\n",
    "backend/pyproject.toml": "[tool.ruff]\n[tool.mypy]\n[tool.pytest.ini_options]\n",
    "backend/app/__init__.py": "",
    "backend/app/auth/__init__.py": "# auth package\n",
    "backend/app/auth/refresh.py": "def refresh(token):\n    return token\n",
    "backend/app/api/routes.py": "ROUTES = []\n",
    "backend/tests/test_refresh.py": "def test_refresh():\n    assert True\n",
    "backend/tests/integration/test_auth_flow.py": "def test_flow():\n    assert True\n",
    ".agents/skills/ui-forms/SKILL.md": "---\nname: ui-forms\n---\n",
    ".agents/skills/auth-security/SKILL.md": "---\nname: auth-security\n---\n",
    ".agents/skills/docs-writing/SKILL.md": "---\nname: docs-writing\n---\n",
}

SCENARIO_C_SIGNALS = (
    Signal("code.changed", "code", Risk.STANDARD, ("backend/app/auth/refresh.py",)),
    Signal("security.auth_path", "security", Risk.ELEVATED, ("backend/app/auth/refresh.py",)),
)


def make_repo(tmp_path, files):
    repo = GitRepo(tmp_path / "repo")
    for path, content in files.items():
        repo.write(path, content)
    return repo, repo.commit("base")


def change_of(base, *files):
    return ChangeSet(
        base=base,
        head=base,
        tree_digest="0" * 40,
        files=tuple(sorted(files, key=lambda f: f.path)),
        uncommitted=True,
    )


def paths(plan):
    return [(i.priority, i.path) for i in plan.items]


@pytest.fixture
def webapp(tmp_path):
    return make_repo(tmp_path, WEBAPP)


def scenario_c(repo):
    repo.write(
        "backend/app/auth/refresh.py",
        "import jwt\n\ndef refresh(refresh_token):\n    return jwt.decode(refresh_token)\n",
    )


def test_scenario_c_generous_budget(webapp):
    repo, base = webapp
    scenario_c(repo)
    change = change_of(base, ChangedFile("backend/app/auth/refresh.py", M))
    plan = select_context(repo.root, change, SCENARIO_C_SIGNALS, "Modify refresh-token", 12_000)
    assert paths(plan) == [
        (1, "backend/app/auth/refresh.py"),
        (2, "backend/tests/test_refresh.py"),
        (3, "docs/adr/0003-token-refresh.md"),
        (4, "AGENTS.md"),
        (4, "backend/app/auth/__init__.py"),
    ]
    assert plan.excluded == ()
    assert plan.method == "deterministic-heuristic"
    assert plan.estimate == "bytes/4"
    assert plan.budget_tokens == 12_000
    size = (repo.root / "backend/app/auth/refresh.py").stat().st_size
    assert plan.items[0].est_tokens == size // 4
    assert plan.selected_tokens == sum(i.est_tokens for i in plan.items)
    assert plan.candidate_tokens == plan.selected_tokens
    adr = plan.items[2]
    assert "'refresh'" in adr.why and "'token'" in adr.why


def test_doc_matches_domain_keyword_alone(tmp_path):
    repo, base = make_repo(
        tmp_path,
        {"docs/session-handling.md": "x" * 40, "docs/install.md": "y", "lib/a.py": "a"},
    )
    repo.write("lib/a.py", "b")
    change = change_of(base, ChangedFile("lib/a.py", M))
    sec = (Signal("security.auth_path", "security", Risk.ELEVATED, ("lib/a.py",)),)
    plan = select_context(repo.root, change, sec, None, 1000)
    assert (3, "docs/session-handling.md") in paths(plan)
    assert all(p != "docs/install.md" for _, p in paths(plan))
    plan2 = select_context(repo.root, change, (), None, 1000)
    assert all(p != "docs/session-handling.md" for _, p in paths(plan2))


def test_tiny_budget_keeps_p1_p2_and_excludes_the_rest(webapp):
    repo, base = webapp
    scenario_c(repo)
    change = change_of(base, ChangedFile("backend/app/auth/refresh.py", M))
    plan = select_context(repo.root, change, SCENARIO_C_SIGNALS, None, 1)
    assert paths(plan) == [(1, "backend/app/auth/refresh.py"), (2, "backend/tests/test_refresh.py")]
    assert plan.selected_tokens > plan.budget_tokens  # correctness floor exceeds the budget
    ex = [(i.priority, i.path) for i in plan.excluded]
    assert ex == [
        (3, "docs/adr/0003-token-refresh.md"),
        (4, "AGENTS.md"),
        (4, "backend/app/auth/__init__.py"),
    ]
    for item in plan.excluded:
        assert "over budget" in item.why and item.est_tokens >= 0
    assert plan.candidate_tokens == plan.selected_tokens + sum(i.est_tokens for i in plan.excluded)


def test_budget_skips_large_item_but_keeps_smaller_later_ones(tmp_path):
    repo, base = make_repo(
        tmp_path,
        {"pkg/a.py": "a", "pkg/big.txt": "x" * 4000, "pkg/small.txt": "y" * 40},
    )
    repo.write("pkg/a.py", "aa")
    change = change_of(base, ChangedFile("pkg/a.py", M))
    plan = select_context(repo.root, change, (), None, 50)
    assert paths(plan) == [(1, "pkg/a.py"), (4, "pkg/small.txt")]
    assert [(i.path, i.est_tokens) for i in plan.excluded] == [("pkg/big.txt", 1000)]


def test_deterministic(webapp):
    repo, base = webapp
    scenario_c(repo)
    change = change_of(base, ChangedFile("backend/app/auth/refresh.py", M))
    a = select_context(repo.root, change, SCENARIO_C_SIGNALS, "t", 12_000)
    b = select_context(repo.root, change, SCENARIO_C_SIGNALS, "t", 12_000)
    assert a == b


def test_python_pairing_locations(tmp_path):
    repo, base = make_repo(
        tmp_path,
        {
            "src/pkg/x.py": "x",
            "src/pkg/test_x.py": "t",
            "tests/test_x.py": "t",
            "tests/unit/deep/x_test.py": "t",
            "other/test_x.py": "not paired: not same dir, not under tests/",
            "tests/test_y.py": "t",
        },
    )
    repo.write("src/pkg/x.py", "xx")
    change = change_of(base, ChangedFile("src/pkg/x.py", M))
    plan = select_context(repo.root, change, (), None, 0)
    p2 = [p for pr, p in paths(plan) if pr == 2]
    assert p2 == ["src/pkg/test_x.py", "tests/test_x.py", "tests/unit/deep/x_test.py"]


def test_js_pairing(tmp_path):
    repo, base = make_repo(
        tmp_path,
        {
            "web/src/Btn.tsx": "b",
            "web/src/Btn.test.tsx": "t",
            "web/src/Btn.spec.tsx": "t",
            "web/src/__tests__/Btn.test.tsx": "t",
            "web/src/Other.test.tsx": "t",
        },
    )
    repo.write("web/src/Btn.tsx", "bb")
    change = change_of(base, ChangedFile("web/src/Btn.tsx", M))
    plan = select_context(repo.root, change, (), None, 0)
    p2 = [p for pr, p in paths(plan) if pr == 2]
    assert p2 == ["web/src/Btn.spec.tsx", "web/src/Btn.test.tsx", "web/src/__tests__/Btn.test.tsx"]


def test_changed_test_pairs_back_to_source(webapp):
    repo, base = webapp
    repo.write("backend/tests/test_refresh.py", "def test_refresh():\n    assert 1\n")
    repo.write("frontend/src/components/LoginForm.test.tsx", "test('x', () => {});\n")
    change = change_of(
        base,
        ChangedFile("backend/tests/test_refresh.py", M),
        ChangedFile("frontend/src/components/LoginForm.test.tsx", M),
    )
    plan = select_context(repo.root, change, (), None, 0)
    p2 = [p for pr, p in paths(plan) if pr == 2]
    assert p2 == ["backend/app/auth/refresh.py", "frontend/src/components/LoginForm.tsx"]
    assert "changed test" in plan.items[2].why


def test_added_file_and_new_test_are_seen(tmp_path):
    repo, base = make_repo(tmp_path, {"README.md": "# r\n", "tests/test_new.py": "t"})
    repo.write("app/new.py", "n")
    repo.write("app/ignored_untracked.py", "not in the change set, so not a candidate")
    change = change_of(base, ChangedFile("app/new.py", A))
    plan = select_context(repo.root, change, (), None, 100)
    assert paths(plan) == [(1, "app/new.py"), (2, "tests/test_new.py"), (4, "README.md")]
    assert plan.items[0].why == "changed file (added)"


def test_deleted_file_counts_as_candidate_only(tmp_path):
    repo, base = make_repo(tmp_path, {"lib/gone.py": "g" * 400, "lib/keep.py": "k" * 40})
    repo.delete("lib/gone.py")
    change = change_of(base, ChangedFile("lib/gone.py", D))
    plan = select_context(repo.root, change, (), None, 1000)
    assert paths(plan) == [(4, "lib/keep.py")]
    assert plan.candidate_tokens == 100 + 10
    assert plan.selected_tokens == 10


def test_renamed_file(tmp_path):
    repo, base = make_repo(tmp_path, {"lib/old.py": "o"})
    (repo.root / "lib/old.py").rename(repo.root / "lib/new.py")
    change = change_of(base, ChangedFile("lib/new.py", R, old_path="lib/old.py"))
    plan = select_context(repo.root, change, (), None, 1000)
    assert paths(plan) == [(1, "lib/new.py")]
    assert plan.items[0].why == "changed file (renamed)"


def test_never_includes_git_or_repomarshal(tmp_path):
    repo, base = make_repo(tmp_path, {"a.py": "a", ".repomarshal/plan.json": "{}"})
    repo.write("a.py", "b")
    repo.write(".repomarshal/evidence.json", "{}")
    change = change_of(
        base,
        ChangedFile("a.py", M),
        ChangedFile(".repomarshal/evidence.json", A),
        ChangedFile(".git/config", M),
    )
    plan = select_context(repo.root, change, (), "repomarshal git config plan", 10_000)
    every = [i.path for i in plan.items + plan.excluded]
    assert every == ["a.py"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_symlink_escaping_repo_is_not_read(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("s" * 4000)
    repo, base = make_repo(tmp_path, {"lib/a.py": "a"})
    os.symlink(outside, repo.root / "lib/leak.txt")
    os.symlink(outside, repo.root / "lib/changed_link.txt")
    os.symlink(repo.root / ".git/config", repo.root / "lib/gitcfg")
    base = repo.commit("links")
    repo.write("lib/a.py", "b")
    change = change_of(base, ChangedFile("lib/a.py", M), ChangedFile("lib/changed_link.txt", M))
    plan = select_context(repo.root, change, (), None, 10_000)
    assert paths(plan) == [(1, "lib/a.py"), (1, "lib/changed_link.txt")]
    assert plan.items[1].est_tokens == 0
    assert plan.excluded == ()


def test_forecast_mode_matches_task_keywords(webapp):
    repo, base = webapp
    change = change_of(base)
    plan = select_context(repo.root, change, (), "Modify refresh-token handling", 12_000)
    assert paths(plan) == [
        (3, "docs/adr/0003-token-refresh.md"),
        (4, "AGENTS.md"),
        (4, "backend/app/auth/refresh.py"),
        (4, "backend/tests/test_refresh.py"),
    ]
    assert "task keyword 'refresh'" in plan.items[2].why


def test_forecast_mode_without_task_or_base(tmp_path):
    repo = GitRepo(tmp_path / "empty")
    plan = select_context(repo.root, change_of(None), (), None, 100)
    assert plan.items == () and plan.excluded == ()
    assert plan.selected_tokens == 0 and plan.candidate_tokens == 0


def test_negative_budget_is_clamped(webapp):
    repo, base = webapp
    change = change_of(base, ChangedFile("docs/install.md", M))
    plan = select_context(repo.root, change, (), None, -5)
    assert plan.budget_tokens == 0
    assert paths(plan) == [(1, "docs/install.md")]


def test_lockfile_and_generated_files_are_excluded_not_read(tmp_path):
    repo, base = make_repo(
        tmp_path,
        {
            "pnpm-lock.yaml": "a\n",
            "web/app.min.js": "a\n",
            "web/app.js.map": "a\n",
            "web/dist/bundle.js": "a\n",
            "web/src/app.ts": "a\n",
            "data/huge.json": "a\n",
        },
    )
    repo.write("pnpm-lock.yaml", "lock: 1\n" * 2000)
    for p in ("web/app.min.js", "web/app.js.map", "web/dist/bundle.js", "web/src/app.ts"):
        repo.write(p, "b\n")
    repo.write("data/huge.json", "x" * (200 * 1024 + 4))
    changed = ("pnpm-lock.yaml", "web/app.min.js", "web/app.js.map", "web/dist/bundle.js",
               "web/src/app.ts", "data/huge.json")  # fmt: skip
    change = change_of(base, *(ChangedFile(p, M) for p in changed))
    plan = select_context(repo.root, change, (), None, 12_000)
    assert [p for _, p in paths(plan)] == ["web/src/app.ts"]  # P1 keeps ordinary files
    why = {i.path: i.why for i in plan.excluded}
    assert set(why) == set(changed) - {"web/src/app.ts"}
    assert why["pnpm-lock.yaml"].endswith("excluded: lockfile (generated) (4000 est. tokens)")
    assert "minified bundle (generated)" in why["web/app.min.js"]
    assert "source map (generated)" in why["web/app.js.map"]
    assert "build output (generated)" in why["web/dist/bundle.js"]
    assert "file over 200 KB" in why["data/huge.json"]
    # the size estimate is honest: excluded files are candidates, not selected
    assert plan.selected_tokens == sum(i.est_tokens for i in plan.items)
    assert plan.candidate_tokens == plan.selected_tokens + sum(i.est_tokens for i in plan.excluded)
