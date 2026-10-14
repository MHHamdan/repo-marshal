"""Unit tests for repomarshal.workspace: when a root script covers a workspace package gate
(architecture §3.5). Evidence is explicit or absent; absent means both gates stay."""

from __future__ import annotations

import json
from typing import Any

import pytest

from repomarshal.workspace import Script, root_coverage, shell_words


class FakeTree:
    """In-memory tree with the read/parsed interface of inventory._Tree."""

    def __init__(self, files: dict[str, str]):
        self.files = files

    def read(self, path: str) -> bytes | None:
        text = self.files.get(path)
        return None if text is None else text.encode()

    def parsed(self, path: str, fmt: str) -> Any:
        assert fmt == "json"
        text = self.files.get(path)
        return None if text is None else json.loads(text)


VITEST_PROJECTS = """\
import { defineConfig } from "vitest/config";
// projects: ["not/this"]  (a comment must not count)
export default defineConfig({
  test: {
    projects: ["packages/*", "!packages/docs", "./vitest.compile.config.ts",
      { test: { name: "x" } }],
    watch: false,
  },
});
"""
PKG_VITEST = Script("test", "vitest run")


def cover(root_body: str, pkg: Script = PKG_VITEST, files: dict | None = None, unit=True):
    tree = FakeTree(files if files is not None else {})
    return root_coverage(tree, unit, Script("test", root_body), pkg, "packages/zod")


# --------------------------------------------------------------------------- recursive runners


@pytest.mark.parametrize(
    "body",
    [
        "pnpm -r test",
        "pnpm -r run test",
        "pnpm run -r test",
        "pnpm --recursive --if-present run test",
        "nub run -r test",
        "npm run test --workspaces",
        "npm test -ws",
        "npm --workspaces run test --if-present",
        "yarn workspaces run test",
        "yarn workspaces foreach -A run test",
        "yarn workspaces foreach --all --parallel run test",
        "turbo run test",
        "turbo test",
        "turbo run build test",
        "npx turbo run test",
        "pnpm turbo run test",
        "lerna run test --stream",
        "pnpm build && pnpm -r test",
    ],
)
def test_recursive_runner_is_evidence(body):
    pkg = Script("test", "jest --coverage")
    assert "runs 'test' in every workspace package" in cover(body, pkg, unit=False)


@pytest.mark.parametrize(
    "body",
    [
        "pnpm -r --filter zod test",  # filter
        "pnpm -F zod -r test",
        "pnpm -r test -- --coverage",  # extra arguments reach the package script
        "pnpm test",  # not recursive
        "pnpm -r exec vitest",  # not a script run
        "npm run test --workspace=zod",  # one workspace
        "npm run test -w zod",
        "yarn workspaces foreach run test",  # yarn 4 needs --all
        "yarn workspaces foreach -A --exclude zod run test",
        "turbo run test --filter=zod",
        "turbo run test --affected",
        "lerna run test --scope zod",
        "nx run-many -t test",  # nx targets can be redefined; not accepted
        "pnpm -r lint",  # a different script
        "pnpm -r test || true",  # could hide a failure
        "pnpm -r test; echo done",
        "pnpm -r test | tee out.log",
        "CI=1 pnpm -r test",  # an assignment could narrow the run
    ],
)
def test_no_recursive_evidence(body):
    assert cover(body, Script("test", "jest"), unit=False) is None


def test_lerna_respects_lerna_json_packages():
    files = {"lerna.json": json.dumps({"packages": ["apps/*"]})}
    assert cover("lerna run test", Script("test", "jest"), files, unit=False) is None
    files = {"lerna.json": json.dumps({"packages": ["packages/*"]})}
    assert cover("lerna run test", Script("test", "jest"), files, unit=False) is not None


# --------------------------------------------------------------------------- vitest / jest projects


def test_vitest_projects_in_root_config_cover_a_bare_vitest_package():
    files = {"vitest.config.ts": VITEST_PROJECTS}
    for root in ("vitest run", "nub exec --node vitest run", "pnpm vitest --run", "npx vitest"):
        evidence = cover(root, files=files)
        assert evidence == (
            "root vitest reads vitest.config.ts projects, which include packages/zod"
        ), root
    for pkg in ("pnpm vitest run", "nub exec --node vitest run", "vitest"):
        assert cover("vitest run", Script("test", pkg), files) is not None, pkg


def test_vitest_projects_exclusion_and_other_packages():
    files = {"vitest.config.ts": VITEST_PROJECTS}
    tree, root = FakeTree(files), Script("test", "vitest run")
    assert root_coverage(tree, True, root, PKG_VITEST, "packages/docs") is None
    assert root_coverage(tree, True, root, PKG_VITEST, "apps/web") is None


@pytest.mark.parametrize(
    ("root", "pkg"),
    [
        ("vitest run --project zod", "vitest run"),  # root narrowed to one project
        ("vitest run packages/mini", "vitest run"),  # positional filter
        ("vitest run", "vitest run --typecheck"),  # package adds options
        ("vitest run", "vitest run --config other.config.ts"),  # package uses another config
        ("vitest run", "vitest run && tsx check.ts"),  # package does more than vitest
        ("jest", "vitest run"),  # a different runner
        ("vitest run", "jest"),
        ("VITEST_MAX_THREADS=1 vitest run", "vitest run"),
    ],
)
def test_vitest_without_exact_evidence(root, pkg):
    assert cover(root, Script("test", pkg), {"vitest.config.ts": VITEST_PROJECTS}) is None


def test_package_hooks_void_runner_evidence():
    pkg = Script("test", "vitest run", hooks=True)  # pretest/posttest would not run at the root
    assert cover("vitest run", pkg, {"vitest.config.ts": VITEST_PROJECTS}) is None


def test_runner_evidence_is_unit_only():
    assert cover("vitest run", files={"vitest.config.ts": VITEST_PROJECTS}, unit=False) is None


@pytest.mark.parametrize(
    "config",
    [
        'export default { test: { include: ["src/**/*.test.ts"] } };',  # no projects
        "export default { test: { projects: [...pkgs] } };",  # spread could hide an exclusion
        "export default { test: { projects: dirs } };",  # a variable
        "export default { test: { projects: [`${root}/packages/*`] } };",  # template expression
        'export default { a: { projects: ["packages/*"] }, b: { projects: [] } };',  # ambiguous
    ],
)
def test_unusable_vitest_config_is_no_evidence(config):
    assert cover("vitest run", files={"vitest.config.ts": config}) is None


def test_vitest_config_resolution():
    projects = 'export default { test: { projects: ["packages/*"] } };'
    # vitest.config.* wins over vite.config.*
    files = {"vitest.config.mts": "export default {};", "vite.config.ts": projects}
    assert cover("vitest run", files=files) is None
    assert cover("vitest run", files={"vite.config.ts": projects}) is not None
    # --config names the file that is read
    assert cover("vitest run --config ci.vitest.ts", files={"ci.vitest.ts": projects}) is not None
    assert cover("vitest run -c missing.ts", files={"vitest.config.ts": projects}) is None
    for outside in ("../x.ts", "/etc/x.ts", "a/../../x.ts"):  # never read outside the repo
        assert cover(f"vitest run -c {outside}", files={outside: projects}) is None


def test_vitest_workspace_file_and_config_file_patterns():
    workspace = (
        'import { defineWorkspace } from "vitest/config";\n'
        'export default defineWorkspace(["packages/*/vitest.config.ts"]);\n'
    )
    files = {
        "vitest.workspace.ts": workspace,
        "packages/zod/vitest.config.ts": "export default {};",
    }
    assert cover("vitest run", files=files) is not None
    del files["packages/zod/vitest.config.ts"]  # the pattern names a config file it lacks
    assert cover("vitest run", files=files) is None
    assert cover("vitest run", files={"vitest.workspace.json": '["packages/*"]'}) is not None


def test_jest_projects():
    pkg = Script("test", "jest")
    root_pkg = json.dumps({"jest": {"projects": ["<rootDir>/packages/*"]}})
    assert cover("jest", pkg, {"package.json": root_pkg}) == (
        'root jest reads package.json "jest.projects", which include packages/zod'
    )
    assert cover("jest --ci", pkg, {"jest.config.json": '{"projects": ["packages/zod"]}'})
    js = "module.exports = { projects: ['<rootDir>/packages/*', {displayName: 'lint'}] };"
    assert cover("jest", pkg, {"jest.config.js": js}) is not None
    two = {"jest.config.js": js, "jest.config.json": '{"projects": ["packages/*"]}'}
    assert cover("jest", pkg, two) is None  # jest refuses several configs
    assert cover("jest --selectProjects zod", pkg, {"jest.config.js": js}) is None
    assert cover("jest", Script("test", "jest --coverage"), {"jest.config.js": js}) is None


def test_shell_words_split_operators():
    assert shell_words("a && b || c") == ["a", "&&", "b", "||", "c"]
