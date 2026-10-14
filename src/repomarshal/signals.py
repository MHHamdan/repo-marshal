"""Signal catalogue and evaluation (owner: agent "planner"; roadmap M5; architecture §4.1; ADR-005).

The catalogue is data (a tuple of rules with stable ids). v0.1 signals:
  docs.changed, code.changed, frontend.ui, tests.changed, security.auth_path,
  security.sensitive_identifier, build.config_changed, policy.self_modified,
  coverage.unclassified
v0.2 adds the test-tamper signals tests.deleted, tests.skip_added, tests.discovery_narrowed
plus policy [[signals]] ("custom.*", path globs, add-only). One Signal per (id) aggregating all
matching files (files sorted). A file that matches no rule (built-in or custom) produces
coverage.unclassified. Deleted files count (their path still matters).

Matching notes (the exact pattern sets are the constants below):
- Path matching is case-insensitive and uses POSIX paths. For a rename, every path rule (and
  coverage.unclassified) is evaluated against both the new and the old path: moving a file out of
  `auth/` or `backend/` still touches that code. A signal that fires for a renamed file lists both
  paths, so gate relevance (decisions) sees the source scope too. coverage.unclassified fires
  when either path matches no rule.
- docs.changed covers documentation file types only: doc extensions (.md .markdown .rst .adoc),
  .txt or extensionless files with a well-known doc stem (README, LICENSE, CHANGELOG, ...), and
  doc formats (those, any .txt, and images) under a `docs`/`doc` segment. Any other file under
  `docs/` (`docs/messages.json`, `docs/conf.py`) goes through the other rules. `.mdx` compiles to
  components: it is docs.changed, code.changed and frontend.ui.
- "Segment" means a directory component of the path (not the file name). "Stem" means the file
  name without its final extension, compared exactly (`LoginForm.tsx` has stem `loginform`, which
  is not `login`).
- security.sensitive_identifier fails closed: a non-test code file whose added lines are not
  fully visible (binary or truncated) fires it, so the secrets gate is required.
- security.auth_path ignores documentation, stylesheet and static-asset files (`login.css`,
  `docs/security/threat-model.md`): they cannot change auth behaviour, and every changed file
  still gets some signal, so this can never shrink a plan to nothing.
- build.config_changed compares BASE (git.read_at) with the working tree (git.read_worktree) for
  dependency-aware manifests and ignores the dependency sections. A manifest that is added,
  deleted, unreadable or unparsable on either side always fires (fail closed).
- dependency.changed fires for a lockfile (LOCKFILE_NAMES, LOCKFILE_GLOBS), or for a
  package.json / pyproject.toml / Cargo.toml whose dependency sections (the keys that
  build.config_changed ignores) differ between BASE and the working tree. An added or deleted
  manifest fires when it has a dependency section; an unreadable or unparsable one always fires
  (fail closed). It is widening: a dependency can change what any scope's lint, typecheck and
  unit gates see.
- frontend.ui never fires for a test file (`*.test.tsx`, `__tests__/x.jsx`): a test renders
  components but ships no UI. It stays tests.changed / code.changed.

v0.2 test-tamper signals (roadmap SHOULD #2; threat T2). All three are elevated and widening, and
decisions.decide_gates taints the test-running gates whose scope covers their files. A "test
source" is a tests.changed path with a Python or JS/TS extension; its lines are what the
definition and marker patterns below are matched against.
- tests.deleted: a test source loses test definitions (removed lines minus the same definitions
  re-added in that file). A definition re-added in another changed test file that the runner
  collects is a move, not a deletion (matched by name, change-set wide, counted). A deleted test
  file, or a test file renamed to a path no runner collects, loses every definition it had at
  BASE; a rename between collected paths is analysed like an edit. "Collected" for a .py file
  follows the BASE pytest config of the nearest ancestor directory that has one: inside its
  testpaths, under no norecursedirs directory (pytest's defaults: .*, build, dist, venv, ...)
  and matching python_files; other files are collected by name (COLLECTED_TEST_GLOBS). A deleted
  or renamed-away test file in a language whose definitions are not parsed (e.g. `_test.go`)
  always fires. Fails closed on hidden lines.
- tests.skip_added: added skip, disable or focus markers in a test source. Lines are
  NFKC-normalised, strings and comments blanked and whitespace around `.` dropped, so a test
  named `test_skip_expired` or a title "skips x" is not a marker but `@pytest .mark .ｓｋｉｐ` is.
  Local aliases are resolved from the file's BASE content plus its added lines (`import pytest
  as pt`, `from pytest import mark as m`, `skip = pytest.mark.skip`, `const t = test`): any skip
  attribute on a pytest/unittest-derived name is a marker, as are pytest.importorskip(,
  `__test__ = False` and a computed JS modifier (`test[name](...)`). A marker line also removed
  elsewhere in the change set (same code once normalised) is a move. Fails closed on hidden
  added lines.
- tests.discovery_narrowed: a pytest config (pytest.toml, .pytest.toml, pyproject.toml,
  pytest.ini, .pytest.ini, tox.ini, setup.cfg), a jest config in package.json or jest.config.json
  (testMatch, testRegex, roots, rootDir, projects, testPathIgnorePatterns,
  modulePathIgnorePatterns, ...), or package.json scripts are
  parsed at BASE and in the working tree and compared: a selection list (testpaths,
  python_files, ...) narrows when an effective BASE entry is gone (an absent key means the
  runner's default, and an absent testpaths means "everything"); an exclusion list narrows when
  it gains an entry; addopts and scripts narrow when they gain a narrowing option. JS config
  files (jest/vitest/vite config modules) cannot be parsed: any added or removed line inside one
  of the keys fires. Makefiles, justfiles and shell scripts: a command added or changed (diff
  lines, and BASE vs worktree with continuation lines joined) that narrows a runner fires: a
  narrowing option, a positional test path given to pytest/jest/vitest, a PYTEST_ADDOPTS= /
  JEST_* / VITEST_* variable, or narrowing arguments passed through a package-manager script
  (`npm test -- -t x`). Scripts and addopts are read the same way. A worktree file that cannot
  be read or parsed fires (fail closed).
- Notes ("<path> <what>", one per file) name tests and options, never values or other content.

v0.3 adds agent.skill_changed (elevated, widening, domain agent-config; ADR-002): an added,
modified or renamed file that belongs to an agent skill. A skill directory is
`.agents/skills/<n>/`, `.claude/skills/<n>/` or `skills/<n>/` (skills.SKILL_ROOTS), or any
directory holding a SKILL.md at BASE or in the change (skill_dir). Deleted files do not fire it.
It requires the skill_audit gate (providers: SkillSpector).
"""

from __future__ import annotations

import configparser
import fnmatch
import json
import posixpath
import re
import shlex
import tomllib
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from repomarshal import git
from repomarshal.model import (
    ChangedFile,
    ChangeSet,
    FileStatus,
    GitError,
    Policy,
    Risk,
    Signal,
    SignalRuleSpec,
)

# Signals that widen cheap/standard gates to every scope (architecture §4.2).
WIDENING: frozenset[str] = frozenset(
    {
        "build.config_changed",
        "dependency.changed",
        "coverage.unclassified",
        "policy.self_modified",
        "ci.changed",
        "agent.config_changed",
        "agent.skill_changed",
        "tests.deleted",
        "tests.skip_added",
        "tests.discovery_narrowed",
    }
)

# The v0.2 test-tamper signals; decisions taints test-running gates with them.
TAMPER_SIGNALS: tuple[str, ...] = ("tests.deleted", "tests.discovery_narrowed", "tests.skip_added")

# --------------------------------------------------------------------------- pattern data

# docs.changed: documentation extensions anywhere, plain-text or extensionless files whose stem is
# a well-known doc name, and documentation formats (incl. images) under a `docs`/`doc` segment.
DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".markdown", ".rst", ".adoc"})
# Compiled to components (MDX): docs, but also code and UI.
COMPILED_DOC_EXTENSIONS: frozenset[str] = frozenset({".mdx"})
DOC_TEXT_STEMS: frozenset[str] = frozenset(
    {
        "readme", "changelog", "changes", "history", "license", "licence", "copying", "notice",
        "authors", "contributing",
    }
)  # fmt: skip
DOC_SEGMENTS: frozenset[str] = frozenset({"docs", "doc"})
DOC_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp"})
# Accepted only under a DOC_SEGMENTS directory.
DOC_DIR_EXTENSIONS: frozenset[str] = (
    DOC_EXTENSIONS | COMPILED_DOC_EXTENSIONS | DOC_IMAGE_EXTENSIONS | frozenset({".txt"})
)

# code.changed: source-code extensions.
CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py", ".pyi",
        ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte",
        ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".groovy",
        ".rb", ".php", ".pl", ".pm", ".lua", ".ex", ".exs", ".erl", ".hs", ".clj", ".dart",
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm", ".cs", ".fs", ".swift",
        ".sh", ".bash", ".zsh", ".ps1",
        ".mdx",
    }
)  # fmt: skip

# frontend.ui: UI extensions, or a UI directory segment.
FRONTEND_EXTENSIONS: frozenset[str] = frozenset(
    {".tsx", ".jsx", ".vue", ".svelte", ".mdx", ".css", ".scss", ".sass", ".less", ".html", ".htm"}
)
FRONTEND_SEGMENTS: frozenset[str] = frozenset({"components"})

# tests.changed: test directory segments, or test file-name globs (matched on the file name).
TEST_SEGMENTS: frozenset[str] = frozenset({"tests", "test", "__tests__", "spec", "specs"})
TEST_NAME_GLOBS: tuple[str, ...] = (
    "test_*.py",
    "*_test.py",
    "*_test.go",
    "*.test.*",
    "*.spec.*",
    "conftest.py",
)

# security.auth_path: a directory segment or the exact file stem in this set.
AUTH_WORDS: frozenset[str] = frozenset(
    {
        "auth", "oauth", "login", "session", "sessions", "token", "tokens",
        "security", "crypto", "permissions", "acl",
    }
)  # fmt: skip
# ... except for files that cannot carry auth behaviour (docs, styles, static assets).
AUTH_EXEMPT_EXTENSIONS: frozenset[str] = DOC_EXTENSIONS | frozenset(
    {
        ".txt", ".css", ".scss", ".sass", ".less",
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".woff", ".woff2", ".ttf",
    }
)  # fmt: skip

# security.sensitive_identifier: added lines of non-test code files (CODE_EXTENSIONS).
SENSITIVE_IDENTIFIER_RE = re.compile(
    r"password|secret|token|jwt|bearer|private_key|hmac", re.IGNORECASE
)

# build.config_changed: manifests and tool configs (matched on the file name).
BUILD_CONFIG_NAMES: frozenset[str] = frozenset(
    {
        "package.json", "pyproject.toml", "setup.cfg", "setup.py", "makefile", "gnumakefile",
        "tox.ini", "noxfile.py", "pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml",
        "mypy.ini", ".mypy.ini", "ruff.toml",
        ".ruff.toml", ".flake8", ".pylintrc", ".eslintrc", ".prettierrc", ".babelrc",
        ".editorconfig", "go.mod", "cargo.toml", "dockerfile", ".npmrc", ".nvmrc",
        ".python-version",
    }
)  # fmt: skip
BUILD_CONFIG_GLOBS: tuple[str, ...] = (
    "tsconfig*.json",
    "jsconfig*.json",
    "*.config.js",
    "*.config.ts",
    "*.config.mjs",
    "*.config.cjs",
    "*.config.mts",
    "*.config.cts",
    ".eslintrc.*",
    ".prettierrc.*",
    ".babelrc.*",
    "babel.config.json",
    "*.mk",
)
# Dependency sections ignored when comparing BASE with the worktree. A dotted key path; "*" in a
# path component matches any key.
PACKAGE_JSON_DEPENDENCY_KEYS: tuple[str, ...] = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "peerDependenciesMeta",
    "optionalDependencies",
    "bundledDependencies",
    "bundleDependencies",
    "overrides",
    "resolutions",
)
TOML_DEPENDENCY_KEYS: dict[str, tuple[str, ...]] = {
    "pyproject.toml": (
        "project.dependencies",
        "project.optional-dependencies",
        "dependency-groups",
        "tool.poetry.dependencies",
        "tool.poetry.dev-dependencies",
        "tool.poetry.group.*.dependencies",
        "tool.uv.dev-dependencies",
    ),
    "cargo.toml": (
        "dependencies",
        "dev-dependencies",
        "build-dependencies",
        "target.*.dependencies",
        "target.*.dev-dependencies",
        "target.*.build-dependencies",
    ),
}

# dependency.changed: lockfiles (matched on the lower-cased file name).
LOCKFILE_NAMES: frozenset[str] = frozenset(
    {
        "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock",
        "bun.lockb", "poetry.lock", "uv.lock", "pipfile.lock", "pdm.lock", "cargo.lock", "go.sum",
        "gemfile.lock", "composer.lock",
    }
)  # fmt: skip
LOCKFILE_GLOBS: tuple[str, ...] = ("requirements*.txt",)

# ci.changed: CI configuration (an agent narrowing or disabling CI is a T4 threat). Scripts and
# composite actions under .github/ are run by workflows, so they are CI too.
CI_PREFIXES = (
    ".github/workflows/",
    ".github/scripts/",
    ".github/actions/",
    ".circleci/",
    ".buildkite/",
    ".gitlab/ci/",
)
CI_FILES = frozenset({".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", ".travis.yml"})

# agent.config_changed: agent instructions, settings and hooks (an agent removing its own stop
# hook or rewriting its instructions is a T4/T7 threat). Matched at any depth for the
# instruction files, at the root for the settings directories.
AGENT_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "GEMINI.md", ".mcp.json"})
AGENT_PREFIXES = (
    ".claude/",
    ".cursor/",
    ".codex/",
    ".gemini/",
    ".agents/",
    ".github/instructions/",
)
AGENT_ROOT_FILES = frozenset({".github/copilot-instructions.md", ".cursorrules"})


def _is_ci(path: str) -> bool:
    return path.startswith(CI_PREFIXES) or path in CI_FILES


def _is_agent_config(path: str) -> bool:
    return (
        path.rsplit("/", 1)[-1] in AGENT_FILES
        or path.startswith(AGENT_PREFIXES)
        or path in AGENT_ROOT_FILES
    )


# agent.skill_changed: a file of an agent skill (v0.3). The skill roots are those of skills.py;
# a directory with a SKILL.md (at BASE, or the changed file itself) is a skill directory too.
SKILL_ROOTS: tuple[str, ...] = (".agents/skills/", ".claude/skills/", "skills/")
SKILL_FILE = "skill.md"  # compared case-insensitively


def skill_dir(path: str, known: Iterable[str] = ()) -> str | None:
    """The skill directory (repo-relative, no trailing slash; "" for the root) that `path`
    belongs to, or None. `known` holds directories that contain a SKILL.md (base_skill_dirs);
    the deepest one containing `path` wins. Matching is case-insensitive; the returned directory
    keeps the case of `path`."""
    low = path.lower()
    for root in SKILL_ROOTS:
        if low.startswith(root):
            rest = path[len(root) :]
            if "/" in rest and rest.split("/", 1)[0]:
                return path[: len(root)] + rest.split("/", 1)[0]
    head, _, name = path.rpartition("/")
    if name.lower() == SKILL_FILE:
        return head
    best: str | None = None
    for d in known:
        if d == "" or low.startswith(d.lower() + "/"):
            if best is None or len(d) > len(best):
                best = d
    return None if best is None else path[: len(best)]


def symlinked_skill_entry(repo: Path, path: str) -> bool:
    """True for a symlink placed directly in a skill root (`.claude/skills/evil -> /elsewhere`).
    git records it as one path, so skill_dir (which needs `<root>/<name>/...`) misses it, yet a
    host follows it and loads the skill it points to (v0.3 fail-open review #5)."""
    low = path.lower()
    for root in SKILL_ROOTS:
        if low.startswith(root):
            rest = path[len(root) :]
            if rest and "/" not in rest:
                try:
                    return (Path(repo) / path).is_symlink()
                except OSError:
                    return True
    return False


@lru_cache(maxsize=32)
def _base_skill_dirs(repo: str, base: str | None) -> frozenset[str]:
    dirs = set()
    for f in git.list_files_at(Path(repo), base):
        head, _, name = f.rpartition("/")
        if name.lower() == SKILL_FILE:
            dirs.add(head)
    return frozenset(dirs)


def base_skill_dirs(repo: Path, base: str | None) -> frozenset[str]:
    """Directories that contain a SKILL.md at BASE ("" for the root). Empty when BASE cannot be
    listed (the fixed skill roots still apply)."""
    try:
        return _base_skill_dirs(str(Path(repo)), base)
    except (GitError, OSError):
        return frozenset()


def _skill_changed(f: ChangedFile, ctx: _Ctx) -> tuple[str, ...]:
    if f.status is FileStatus.DELETED:
        return ()
    known = base_skill_dirs(ctx.repo, ctx.base)
    return tuple(
        p
        for p in _candidates(f)
        if skill_dir(p, known) is not None or symlinked_skill_entry(ctx.repo, p)
    )


# policy.self_modified: the policy file at the repository root.
POLICY_PATH = "repomarshal.toml"

# ---- test-tamper signals (v0.2)

# Test sources (tests.changed paths) whose lines are parsed, by language.
PY_TEST_EXTENSIONS: frozenset[str] = frozenset({".py"})
JS_TEST_EXTENSIONS: frozenset[str] = frozenset(
    {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
)
# Files a runner collects by name (matched on the file name), plus JS/TS files under these
# segments (jest's default testMatch). Deleting one, or renaming it to a path that is not
# collected, removes its tests.
COLLECTED_TEST_GLOBS: tuple[str, ...] = (
    "test_*.py",
    "*_test.py",
    "*_test.go",
    "*.test.*",
    "*.spec.*",
)
COLLECTED_TEST_SEGMENTS: frozenset[str] = frozenset({"__tests__"})

# tests.deleted: test definitions. Group 1 is the test name used to recognise a move.
PY_TEST_DEFINITION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\("),  # def test_x / async def test_x
    re.compile(r"^\s*class\s+(Test\w*)"),  # class TestX
)
# it( test( describe(, with x/f prefixes and modifier chains (it.skip(, test.each(...)(...)), so
# that turning `it(` into `it.skip(` or `xit(` is a skip, not also a deletion. The name is the
# string title that follows, or the whole stripped line when there is none.
JS_TEST_CALL_RE = re.compile(r"(?<![\w$.])[xf]?(?:it|test|describe)(?:\.\w+)*\s*\(")
JS_TEST_TITLE_RE = re.compile(r"""\s*(['"`])((?:\\.|(?!\1).)*)\1""")

# tests.skip_added: markers matched on added lines with strings and comments blanked.
PY_SKIP_MARKER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpytest\.mark\.(?:skip|skipif|xfail)\b"),  # decorator or `pytestmark = ...`
    re.compile(r"^\s*@mark\.(?:skip|skipif|xfail)\b"),  # from pytest import mark
    re.compile(r"\bpytest\.(?:skip|xfail)\s*\("),  # imperative pytest.skip( / pytest.xfail(
    re.compile(r"\bunittest\.skip\w*\b"),  # @unittest.skip, skipIf, skipUnless
    re.compile(r"^\s*@skip(?:If|Unless)?\b"),  # from unittest import skip
    re.compile(r"\bSkipTest\b"),  # raise unittest.SkipTest
    re.compile(r"\.skipTest\s*\("),  # self.skipTest(
    re.compile(r"\bimportorskip\s*\("),  # pytest.importorskip("mod") skips the whole module
    re.compile(r"\b__test__\s*=(?!=)(?!\s*True\b)"),  # __test__ = False: not collected
)
# Alias-aware Python detection: a name bound (import, from-import, assignment) to one of these
# modules or their attributes is resolved to its dotted target; any skip attribute on the target
# is a marker (`import pytest as pt; @pt.mark.skip`, `from pytest import mark as m; @m.xfail`).
PY_SKIP_MODULES: frozenset[str] = frozenset({"pytest", "_pytest", "unittest"})
PY_SKIP_ATTRS: frozenset[str] = frozenset(
    {
        "skip", "skipif", "xfail", "importorskip", "skipIf", "skipUnless", "SkipTest",
        "skipTest", "expectedFailure",
    }
)  # fmt: skip
# Bound by `from pytest import *` (and the unittest equivalent).
PY_STAR_NAMES: tuple[str, ...] = (
    "mark", "skip", "xfail", "importorskip", "skipIf", "skipUnless", "SkipTest", "expectedFailure",
)  # fmt: skip
JS_TEST_FUNCTIONS: tuple[str, ...] = ("it", "test", "describe")


@cache
def _js_skip_marker_res(names: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """JS marker regexes for the test functions `names` (it/test/describe plus local aliases)."""
    fn = "(?:" + "|".join(re.escape(n) for n in names) + ")"
    return (
        re.compile(rf"(?<![\w$.]){fn}(?:\.\w+)*\.(?:skip\w*|todo)\b"),  # it.skip
        re.compile(r"(?<![\w$.])x(?:it|test|describe)\s*[(.]"),  # xit( xdescribe(
        # focus markers: everything else in the file is silently excluded
        re.compile(rf"(?<![\w$.]){fn}(?:\.\w+)*\.only\b"),  # it.only
        re.compile(r"(?<![\w$.])f(?:it|describe)\s*[(.]"),  # fit( fdescribe(
        # computed modifier: test[focus](...), test["only"](...) cannot be read statically
        re.compile(rf"(?<![\w$.]){fn}(?:\.\w+)*\s*\["),
    )


JS_SKIP_MARKER_RES: tuple[re.Pattern[str], ...] = _js_skip_marker_res(JS_TEST_FUNCTIONS)

# tests.discovery_narrowed: pytest config files -> (format, section holding the settings).
PYTEST_CONFIG_FILES: dict[str, tuple[str, str]] = {
    "pytest.toml": ("toml", "pytest"),  # pytest >= 9, [pytest] table
    ".pytest.toml": ("toml", "pytest"),
    "pyproject.toml": ("toml", "tool.pytest.ini_options"),  # also reads native [tool.pytest]
    "pytest.ini": ("ini", "pytest"),
    ".pytest.ini": ("ini", "pytest"),
    "tox.ini": ("ini", "pytest"),
    "setup.cfg": ("ini", "tool:pytest"),
}
# Selection lists and their defaults (None: the key absent selects everything). Narrowed when an
# effective BASE entry is missing from the effective worktree list.
PYTEST_SELECT_KEYS: dict[str, tuple[str, ...] | None] = {
    "testpaths": None,
    "python_files": ("test_*.py", "*_test.py"),
    "python_classes": ("Test",),
    "python_functions": ("test",),
}
# Exclusion lists and their defaults. Narrowed when the worktree list gains an entry.
PYTEST_EXCLUDE_KEYS: dict[str, tuple[str, ...]] = {
    "norecursedirs": (
        "*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}",
    ),
}  # fmt: skip
# Command-line options that narrow a run (addopts, scripts, Makefile recipes).
PYTEST_VALUE_OPTIONS: tuple[str, ...] = ("-k", "-m", "--ignore", "--ignore-glob", "--deselect")
PYTEST_FLAG_OPTIONS: tuple[str, ...] = (
    "--lf", "--last-failed", "--sw", "--stepwise", "--co", "--collect-only",
)  # fmt: skip
JEST_VALUE_OPTIONS: tuple[str, ...] = (
    "-t", "--testNamePattern", "--testPathPattern", "--testPathPatterns",
    "--testPathIgnorePatterns", "--changedSince",
)  # fmt: skip
JEST_FLAG_OPTIONS: tuple[str, ...] = (
    "-o", "--onlyChanged", "--lastCommit", "--findRelatedTests", "--passWithNoTests",
)  # fmt: skip
PYTEST_RUNNERS: frozenset[str] = frozenset({"pytest", "py.test"})
JS_TEST_RUNNERS: frozenset[str] = frozenset({"jest", "vitest"})
# jest config objects (package.json "jest", jest.config.json): same semantics as the pytest lists.
JEST_SELECT_KEYS: dict[str, tuple[str, ...] | None] = {
    "testMatch": None,  # default: everything
    "testRegex": None,
    "roots": ("<rootDir>",),
    "rootDir": None,
    "projects": None,
}
JEST_EXCLUDE_KEYS: dict[str, tuple[str, ...]] = {
    "testPathIgnorePatterns": ("/node_modules/",),
    "modulePathIgnorePatterns": (),
    "testNamePattern": (),
    "testPathPattern": (),
}
JEST_JSON_CONFIGS: frozenset[str] = frozenset({"jest.config.json"})
# JS config modules (not parsable without a JS engine): any change inside these keys fires.
JS_CONFIG_GLOBS: tuple[str, ...] = (
    "jest.config.*",
    "vitest.config.*",
    "vitest.workspace.*",
    "vite.config.*",
)
JS_CONFIG_KEYS: tuple[str, ...] = (
    # jest
    "testMatch",
    "testPathIgnorePatterns",
    "testRegex",
    "roots",
    "rootDir",
    "modulePathIgnorePatterns",
    "testNamePattern",
    "testPathPattern",
    "projects",
    # vitest (test.include/exclude/dir/root/testNamePattern/workspace)
    "include",
    "exclude",
    "dir",
    "root",
    "workspace",
)
# Files holding shell commands (Makefile recipes, scripts): an added or changed command that
# narrows a test runner fires.
MAKEFILE_NAMES: frozenset[str] = frozenset({"makefile", "gnumakefile", "justfile"})
MAKEFILE_GLOBS: tuple[str, ...] = ("*.mk", "*.sh", "*.bash", "*.zsh")
# Environment variables that inject options into a runner (`PYTEST_ADDOPTS='-k x' pytest`).
RUNNER_ENV_RE = re.compile(r"(?<![\w$])(PYTEST_ADDOPTS|JEST_\w+|VITEST_\w*)\s*[:?+]?=")
# Package managers whose `<pm> [run] <script> [--] args` passes args to the script's runner.
PACKAGE_MANAGERS: frozenset[str] = frozenset({"npm", "pnpm", "yarn", "bun"})
# Runner options that take a value (so the value is not read as a positional test path).
PYTEST_ARG_OPTIONS: frozenset[str] = frozenset(
    {
        "-c", "-p", "-o", "-n", "-W", "-r", "--tb", "--rootdir", "--confcutdir", "--basetemp",
        "--maxfail", "--durations", "--durations-min", "--junitxml", "--junit-xml", "--cov",
        "--cov-report", "--cov-fail-under", "--cov-config", "--log-level", "--log-file",
        "--log-cli-level", "--timeout", "--dist", "--numprocesses", "--import-mode",
        "--capture", "--color", "--override-ini", "--config-file", "--inifile",
        "--cache-dir", "--deselect", "--ignore", "--ignore-glob", "-k", "-m",
    }
)  # fmt: skip
JS_ARG_OPTIONS: frozenset[str] = frozenset(
    {
        "-c", "--config", "-w", "--maxWorkers", "--reporters", "--reporter", "--testTimeout",
        "--env", "--environment", "--shard", "--outputFile", "--coverageDirectory",
        "--coverageReporters", "--coverageProvider", "--pool", "--mode", "--project",
        "--selectProjects", "--rootDir", "--root", "--dir", "--workspace", "-t",
        "--testNamePattern", "--testPathPattern", "--testPathPatterns",
        "--testPathIgnorePatterns", "--changedSince", "--seed", "--retry",
    }
)  # fmt: skip
# vitest subcommands: `related` narrows to files, the others are not test paths.
VITEST_SUBCOMMANDS: frozenset[str] = frozenset({"run", "watch", "dev", "bench", "typecheck"})
_SHELL_REDIRECT_RE = re.compile(r"^\d*[<>]")


# --------------------------------------------------------------------------- catalogue


@dataclass(frozen=True)
class _Ctx:
    """Per-evaluation context passed to matchers that need repository content."""

    repo: Path
    base: str | None
    # Tamper findings for the whole change set: signal id -> {changed path: note}.
    tamper: dict[str, dict[str, str]] = field(default_factory=dict)


PathPredicate = Callable[[str], bool]
# A content-aware matcher returns the candidate paths (see _candidates) it classifies; empty means
# no match.
Matcher = Callable[[ChangedFile, _Ctx], tuple[str, ...]]


@dataclass(frozen=True)
class Rule:
    id: str
    domain: str
    risk: Risk
    description: str
    match: Matcher
    # Optional per-file note appended to the signal detail (e.g. why it fired fail-closed).
    note: Callable[[ChangedFile, _Ctx], str | None] | None = None


def _parts(path: str) -> tuple[tuple[str, ...], str, str, str]:
    """(directory segments, file name, stem, extension), all lower-cased."""
    p = path.lower()
    segs = tuple(s for s in p.split("/") if s)
    name = segs[-1] if segs else ""
    stem, ext = posixpath.splitext(name)
    return segs[:-1], name, stem, ext


def _candidates(f: ChangedFile) -> tuple[str, ...]:
    return (f.path, f.old_path) if f.old_path and f.old_path != f.path else (f.path,)


def _each_path(pred: PathPredicate) -> Matcher:
    """A path rule: evaluated on every candidate path (new and old path of a rename)."""
    return lambda f, _ctx: tuple(p for p in _candidates(f) if pred(p))


def _is_doc(path: str) -> bool:
    dirs, _name, stem, ext = _parts(path)
    return (
        ext in DOC_EXTENSIONS
        or ext in COMPILED_DOC_EXTENSIONS
        or (ext in ("", ".txt") and stem in DOC_TEXT_STEMS)
        or (ext in DOC_DIR_EXTENSIONS and any(d in DOC_SEGMENTS for d in dirs))
    )


def _is_code(path: str) -> bool:
    return _parts(path)[3] in CODE_EXTENSIONS


def _is_frontend(path: str) -> bool:
    if _is_test(path):
        return False  # a test renders components but ships no UI
    dirs, _name, _stem, ext = _parts(path)
    return ext in FRONTEND_EXTENSIONS or any(d in FRONTEND_SEGMENTS for d in dirs)


def _is_test(path: str) -> bool:
    dirs, name, _stem, _ext = _parts(path)
    return any(d in TEST_SEGMENTS for d in dirs) or any(
        fnmatch.fnmatchcase(name, g) for g in TEST_NAME_GLOBS
    )


def _is_auth_path(path: str) -> bool:
    dirs, _name, stem, ext = _parts(path)
    if ext in AUTH_EXEMPT_EXTENSIONS:
        return False
    return stem in AUTH_WORDS or any(d in AUTH_WORDS for d in dirs)


def _hidden_lines(f: ChangedFile) -> str | None:
    """Why the added lines of `f` are not fully visible, or None when they are."""
    if f.status == FileStatus.DELETED:
        return None  # nothing was added
    if f.binary:
        return "binary"
    if f.truncated:
        return "truncated"
    return None


def _sensitive_identifier(f: ChangedFile, _ctx: _Ctx) -> tuple[str, ...]:
    if not _is_code(f.path) or _is_test(f.path):
        return ()
    # Fail closed: lines we cannot see may hold a secret.
    if _hidden_lines(f) or any(SENSITIVE_IDENTIFIER_RE.search(line) for line in f.added):
        return (f.path,)
    return ()


def _sensitive_note(f: ChangedFile, _ctx: _Ctx) -> str | None:
    why = _hidden_lines(f)
    return f"{f.path}: added lines not fully visible ({why})" if why else None


def _is_build_config_name(path: str) -> bool:
    _dirs, name, _stem, _ext = _parts(path)
    return name in BUILD_CONFIG_NAMES or any(
        fnmatch.fnmatchcase(name, g) for g in BUILD_CONFIG_GLOBS
    )


def _build_config_changed(f: ChangedFile, ctx: _Ctx) -> tuple[str, ...]:
    named = tuple(p for p in _candidates(f) if _is_build_config_name(p))
    return named if named and _build_config_edit(f, ctx) else ()


def _build_config_edit(f: ChangedFile, ctx: _Ctx) -> bool:
    name = _parts(f.path)[1]
    if name != "package.json" and name not in TOML_DEPENDENCY_KEYS:
        return True  # no dependency sections to discount: any edit is a config edit
    if f.status != FileStatus.MODIFIED and f.status != FileStatus.RENAMED:
        return True  # added or deleted manifest
    try:
        before = git.read_at(ctx.repo, ctx.base, f.old_path or f.path)
        after = git.read_worktree(ctx.repo, f.path)
    except (GitError, OSError):
        return True
    if before is None or after is None:
        return True
    try:
        return _strip_dependencies(name, before) != _strip_dependencies(name, after)
    except (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return True  # unparsable on either side: fail closed


def _strip_dependencies(name: str, raw: bytes) -> Any:
    if name == "package.json":
        data = json.loads(raw.decode("utf-8"))
        keys: tuple[str, ...] = PACKAGE_JSON_DEPENDENCY_KEYS
    else:
        data = tomllib.loads(raw.decode("utf-8"))
        keys = TOML_DEPENDENCY_KEYS[name]
    if not isinstance(data, dict):
        return data
    python = _poetry_python(name, data)
    for key in keys:
        _drop(data, key.split("."))
    if python is not _NO_PYTHON:
        # The interpreter constraint is project configuration, like requires-python: keep it
        # in the build-config comparison (v0.7).
        data["poetry-python-constraint"] = python
    return data


_NO_PYTHON = object()


def _poetry_python(name: str, data: dict[str, Any]) -> Any:
    """The `python` entry of [tool.poetry.dependencies]: Poetry's interpreter constraint (the
    equivalent of requires-python), which sits among the dependencies but is not one."""
    if name != "pyproject.toml":
        return _NO_PYTHON
    deps = data.get("tool", {}).get("poetry", {}).get("dependencies")
    if isinstance(deps, dict) and "python" in deps:
        return deps["python"]
    return _NO_PYTHON


def is_lockfile(path: str) -> bool:
    """True for a dependency lockfile (by file name)."""
    name = _parts(path)[1]
    return name in LOCKFILE_NAMES or any(fnmatch.fnmatchcase(name, g) for g in LOCKFILE_GLOBS)


def is_pure_lockfile(path: str) -> bool:
    """True for a generated lockfile (LOCKFILE_NAMES). requirements*.txt is not one: it is
    hand-edited and names what gets installed, like a manifest (decisions: secrets)."""
    return _parts(path)[1] in LOCKFILE_NAMES


def _dependency_changed(f: ChangedFile, ctx: _Ctx) -> tuple[str, ...]:
    locks = tuple(p for p in _candidates(f) if is_lockfile(p))
    if locks:
        return locks
    manifests = tuple(
        p
        for p in _candidates(f)
        if (n := _parts(p)[1]) == "package.json" or n in TOML_DEPENDENCY_KEYS
    )
    return manifests if manifests and _dependency_edit(f, ctx) else ()


def _dependency_edit(f: ChangedFile, ctx: _Ctx) -> bool:
    """True when the dependency sections of a manifest differ between BASE and the worktree."""
    name = _parts(f.path)[1]
    old_name = _parts(f.old_path or f.path)[1]
    try:
        before = (
            None
            if f.status == FileStatus.ADDED
            else git.read_at(ctx.repo, ctx.base, f.old_path or f.path)
        )
        after = None if f.status == FileStatus.DELETED else git.read_worktree(ctx.repo, f.path)
    except (GitError, OSError):
        return True
    if before is None and f.status != FileStatus.ADDED:
        return True  # unreadable at BASE
    if after is None and f.status != FileStatus.DELETED:
        return True  # unreadable in the worktree
    try:
        old = _dependencies_of(old_name, before) if before is not None else {}
        new = _dependencies_of(name, after) if after is not None else {}
    except (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return True  # unparsable on either side: fail closed
    return old != new


def _dependencies_of(name: str, raw: bytes) -> dict[str, Any]:
    """The dependency sections of a manifest: {concrete dotted key: value}."""
    if name == "package.json":
        data = json.loads(raw.decode("utf-8"))
        keys: tuple[str, ...] = PACKAGE_JSON_DEPENDENCY_KEYS
    elif name in TOML_DEPENDENCY_KEYS:
        data = tomllib.loads(raw.decode("utf-8"))
        keys = TOML_DEPENDENCY_KEYS[name]
    else:
        return {}
    out: dict[str, Any] = {}
    for key in keys:
        _pick(data, key.split("."), "", out)
    deps = out.get("tool.poetry.dependencies")
    if name == "pyproject.toml" and isinstance(deps, dict) and "python" in deps:
        # Poetry's interpreter floor is not a dependency (v0.7: rich "drop 3.8" bumped only
        # `python` and raised dependency.changed). It still counts as a build-config edit.
        out["tool.poetry.dependencies"] = {k: v for k, v in deps.items() if k != "python"}
    return out


def _pick(node: Any, path: list[str], prefix: str, out: dict[str, Any]) -> None:
    if not isinstance(node, dict) or not path:
        return
    head, rest = path[0], path[1:]
    targets = list(node) if head == "*" else [head] if head in node else []
    for k in targets:
        dotted = f"{prefix}{k}"
        if rest:
            _pick(node[k], rest, dotted + ".", out)
        else:
            out[dotted] = node[k]


def _drop(node: Any, path: list[str]) -> None:
    if not isinstance(node, dict) or not path:
        return
    head, rest = path[0], path[1:]
    targets = list(node) if head == "*" else [head] if head in node else []
    for k in targets:
        if rest:
            _drop(node[k], rest)
        else:
            del node[k]


# --------------------------------------------------------------------------- test tamper


_STRING_RE = re.compile(r"""'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\\n])*`""")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?(?:\*/|$)")
_SAFE_RE = re.compile(r"[^\w .:/-]")
MAX_NAMES_SHOWN = 5


def _test_lang(path: str) -> str | None:
    """ "py" or "js" for a test source (a tests.changed path with a parsed extension)."""
    if not _is_test(path):
        return None
    ext = _parts(path)[3]
    return "py" if ext in PY_TEST_EXTENSIONS else "js" if ext in JS_TEST_EXTENSIONS else None


def _is_collected(path: str) -> bool:
    dirs, name, _stem, ext = _parts(path)
    if any(fnmatch.fnmatchcase(name, g) for g in COLLECTED_TEST_GLOBS):
        return True
    return ext in JS_TEST_EXTENSIONS and any(d in COLLECTED_TEST_SEGMENTS for d in dirs)


def _safe(text: str, limit: int = 60) -> str:
    """Echo-safe rendering of a name taken from file content (T10)."""
    out = _SAFE_RE.sub("?", text.strip())
    return out if len(out) <= limit else out[: limit - 3] + "..."


def _shown(names: Iterable[str]) -> str:
    items = list(names)
    text = ", ".join(items[:MAX_NAMES_SHOWN])
    if len(items) > MAX_NAMES_SHOWN:
        text += f" (+{len(items) - MAX_NAMES_SHOWN} more)"
    return text


_DOT_SPACE_RE = re.compile(r"\s*\.\s*")


def _code_only(line: str, lang: str) -> str:
    """The line NFKC-normalised (as Python does for identifiers, so fullwidth `ｓｋｉｐ` is
    `skip`), with string literals blanked, comments removed and whitespace around `.` dropped
    (`pytest .mark .skip` is `pytest.mark.skip`)."""
    s = _STRING_RE.sub('""', unicodedata.normalize("NFKC", line))
    s = s.split("#", 1)[0] if lang == "py" else _BLOCK_COMMENT_RE.sub("", s).split("//", 1)[0]
    return _DOT_SPACE_RE.sub(".", s)


# ---- name aliases (resolved per file from its BASE content plus its added lines)

_PY_IMPORT_RE = re.compile(r"^\s*import\s+(.+)$")
_PY_FROM_RE = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+(.+)$")
_PY_FROM_PAREN_RE = re.compile(r"from\s+([\w.]+)\s+import\s*\(([^)]*)\)", re.S)
_PY_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=]*)?=(?!=)\s*([A-Za-z_][\w.]*)")
_DOTTED_RE = re.compile(r"(?<![\w.])([^\W\d]\w*(?:\.\w+)*)")
_GETATTR_RE = re.compile(r"\bgetattr\s*\(\s*([^\W\d][\w.]*)")
_JS_ALIAS_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:const|let|var)\s+([\w$]+)\s*=\s*(?:it|test|describe)\b"),  # const t = test
    re.compile(r"\b(?:it|test|describe)\s+as\s+([\w$]+)"),  # import { test as t }
)
# const { only, skip } = test
_JS_DESTRUCTURE_RE = re.compile(
    r"\b(?:const|let|var)\s*\{([^}]*)\}\s*=\s*(?:it|test|describe)\b(?!\s*\()"
)


def _resolve_alias(dotted: str, aliases: dict[str, str]) -> str | None:
    head, _, rest = dotted.partition(".")
    target = aliases.get(head)
    if target is None:
        return None
    return target + ("." + rest if rest else "")


def _from_import(module: str, names: str, aliases: dict[str, str]) -> None:
    if module.split(".", 1)[0] not in PY_SKIP_MODULES:
        return
    for part in names.replace("(", " ").replace(")", " ").split(","):
        words = part.split()
        if not words:
            continue
        if words[0] == "*":
            aliases.update({n: f"{module}.{n}" for n in PY_STAR_NAMES})
        else:
            aliases[words[-1] if len(words) == 3 and words[1] == "as" else words[0]] = (
                f"{module}.{words[0]}"
            )


def py_aliases(code_lines: Iterable[str]) -> dict[str, str]:
    """Names bound to pytest/_pytest/unittest or their attributes -> dotted target. `code_lines`
    are _code_only lines; the module names themselves are always included."""
    aliases = {m: m for m in PY_SKIP_MODULES}
    stmts = [s for ln in code_lines for s in ln.split(";")]
    for m in _PY_FROM_PAREN_RE.finditer("\n".join(stmts)):
        _from_import(m.group(1), m.group(2), aliases)
    for s in stmts:
        m = _PY_IMPORT_RE.match(s)
        if m:
            for part in m.group(1).split(","):
                words = part.split()
                if len(words) == 3 and words[1] == "as":
                    if words[0].split(".", 1)[0] in PY_SKIP_MODULES:
                        aliases[words[2]] = words[0]
            continue
        m = _PY_FROM_RE.match(s)
        if m:
            _from_import(m.group(1), m.group(2), aliases)
    for _ in range(5):  # assignment chains: m = pytest.mark; s = m.skip
        changed = False
        for s in stmts:
            m = _PY_ASSIGN_RE.match(s)
            if not m:
                continue
            target = _resolve_alias(m.group(2), aliases)
            if target and target.split(".", 1)[0] in PY_SKIP_MODULES:
                if aliases.get(m.group(1)) != target:
                    aliases[m.group(1)] = target
                    changed = True
        if not changed:
            break
    return aliases


def js_aliases(code_lines: Iterable[str]) -> dict[str, str]:
    """Names bound to it/test/describe -> "test"; a destructured skip/only/todo name maps to
    "test.<name>"."""
    aliases = {n: n for n in JS_TEST_FUNCTIONS}
    for ln in code_lines:
        for rx in _JS_ALIAS_RES:
            for m in rx.finditer(ln):
                aliases[m.group(1)] = "test"
        for m in _JS_DESTRUCTURE_RE.finditer(ln):
            for part in m.group(1).split(","):
                key, _, local = part.partition(":")
                name = (local or key).strip()
                if name:
                    aliases[name] = "test." + key.strip()
    return aliases


def defined_tests(lines: Iterable[str], lang: str) -> Counter[str]:
    """Test names defined on `lines` (Python defs/classes, or JS it/test/describe titles)."""
    out: Counter[str] = Counter()
    for line in lines:
        if lang == "py":
            line = unicodedata.normalize("NFKC", line)  # Python reads identifiers as NFKC
            for rx in PY_TEST_DEFINITION_RES:
                m = rx.match(line)
                if m:
                    out[m.group(1)] += 1
            continue
        stripped = line.strip()
        if stripped.startswith(("//", "/*", "*")):
            continue
        for m in JS_TEST_CALL_RE.finditer(line):
            title = JS_TEST_TITLE_RE.match(line, m.end())
            out[title.group(2) if title else stripped] += 1
    return out


def skip_markers(line: str, lang: str, aliases: dict[str, str] | None = None) -> tuple[str, ...]:
    """Skip, disable or focus markers on one line, as short labels ("pytest.mark.skip").

    `aliases` (py_aliases / js_aliases of the file) resolves local names: with
    `import pytest as pt`, `@pt.mark.skip` is labelled "pytest.mark.skip"."""
    code = _code_only(line, lang)
    labels = []
    if lang == "py":
        regexes = PY_SKIP_MARKER_RES
        names = aliases if aliases is not None else {m: m for m in PY_SKIP_MODULES}
        for m in _DOTTED_RE.finditer(code):
            full = _resolve_alias(m.group(1), names)
            segs = full.split(".") if full else []
            if segs and segs[0] in PY_SKIP_MODULES and PY_SKIP_ATTRS.intersection(segs[1:]):
                labels.append(full)
        for m in _GETATTR_RE.finditer(code):  # getattr(pytest.mark, "skip")
            full = _resolve_alias(m.group(1), names)
            if full and full.split(".", 1)[0] in PY_SKIP_MODULES:
                labels.append(f"getattr {full}")
    else:
        names = aliases if aliases is not None else {n: n for n in JS_TEST_FUNCTIONS}
        regexes = _js_skip_marker_res(tuple(sorted(n for n, t in names.items() if "." not in t)))
        for m in _DOTTED_RE.finditer(code):  # destructured: const { only } = test; only(...)
            target = names.get(m.group(1).split(".", 1)[0], "")
            if target.split(".")[1:2] and target.split(".")[1].startswith(("skip", "only", "todo")):
                labels.append(target)
    for rx in regexes:
        for m in rx.finditer(code):
            label = re.sub(r"[\s@(=]", "", m.group(0)).rstrip(".")
            if label.endswith("["):
                label = label[:-1] + " computed modifier"
            if not any(other.endswith("." + label) for other in labels):
                labels.append(label)
    return tuple(dict.fromkeys(_safe(lb, 40) for lb in labels))


def _marker_key(line: str, lang: str) -> str:
    return " ".join(_code_only(line, lang).split())


# ---- pytest collection (BASE config of the scope a path belongs to)

# pytest's config files in its precedence order within one directory; the first four apply even
# without a pytest section.
PYTEST_CONFIG_ORDER: tuple[str, ...] = (
    "pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini",
    "setup.cfg",
)  # fmt: skip
PYTEST_DEDICATED_CONFIGS: frozenset[str] = frozenset(PYTEST_CONFIG_ORDER[:4])


class _Collection:
    """Whether pytest (or jest, by name) collects a path, from the BASE pytest config of the
    nearest ancestor directory that has one (testpaths, norecursedirs, python_files)."""

    def __init__(self, ctx: _Ctx):
        self.ctx = ctx
        self._configs: dict[str, tuple[str, dict[str, Any]]] = {}

    def _config(self, directory: str) -> tuple[str, dict[str, Any]]:
        """(config directory "a/b/" or "", settings) for `directory` ("" is the root)."""
        if directory in self._configs:
            return self._configs[directory]
        found: tuple[str, dict[str, Any]] | None = None
        prefix = directory + "/" if directory else ""
        for name in PYTEST_CONFIG_ORDER:
            try:
                raw = _read_base(self.ctx, prefix + name)
            except (GitError, OSError):
                raw = None
            if raw is None:
                continue
            try:
                section = _pytest_section(name, raw)
            except (ValueError, UnicodeDecodeError, configparser.Error):
                section = {} if name in PYTEST_DEDICATED_CONFIGS else None
            if section is not None or name in PYTEST_DEDICATED_CONFIGS:
                found = (prefix, section or {})
                break
        if found is None:
            found = self._config(posixpath.dirname(directory)) if directory else ("", {})
        self._configs[directory] = found
        return found

    def collected(self, path: str) -> bool:
        if _parts(path)[3] != ".py":
            return _is_collected(path)
        cfg_dir, settings = self._config(posixpath.dirname(path))
        segs = path[len(cfg_dir) :].lower().split("/")
        dirs, name = segs[:-1], segs[-1]
        testpaths = _as_list(settings.get("testpaths"), words=True)
        if testpaths:
            for t in testpaths:
                norm = posixpath.normpath(t.lower())
                tsegs = [] if norm == "." else norm.split("/")
                if len(tsegs) > len(segs) or not all(
                    fnmatch.fnmatchcase(s, p) for s, p in zip(segs, tsegs, strict=False)
                ):
                    continue
                if len(tsegs) == len(segs):
                    return True  # named explicitly: collected whatever its name
                dirs = dirs[len(tsegs) :]
                break
            else:
                return False  # outside every testpath
        norecurse = _as_list(settings.get("norecursedirs"), words=True)
        if norecurse is None:
            norecurse = list(PYTEST_EXCLUDE_KEYS["norecursedirs"])
        if any(fnmatch.fnmatchcase(d, p.lower()) for d in dirs for p in norecurse):
            return False
        files = _as_list(settings.get("python_files"), words=True)
        if files is None:
            files = list(PYTEST_SELECT_KEYS["python_files"] or ())
        return any(fnmatch.fnmatchcase(name, g.lower()) for g in files)


def _rename_note(f: ChangedFile) -> str:
    if f.old_path and f.old_path != f.path:
        return f"{f.path} (renamed from {f.old_path})"
    return f.path


def _read_base(ctx: _Ctx, path: str) -> bytes | None:
    """BASE content; raises GitError/OSError to the caller."""
    return git.read_at(ctx.repo, ctx.base, path)


def _tests_deleted(change: ChangeSet, ctx: _Ctx) -> dict[str, str]:
    removed: dict[str, Counter[str]] = {}
    pool: Counter[str] = Counter()  # definitions added (net) anywhere in the change set
    notes: dict[str, str] = {}
    coll = _Collection(ctx)
    for f in change.files:
        new_lang = _test_lang(f.path) if f.status is not FileStatus.DELETED else None
        old_path = f.old_path or f.path
        old_lang = _test_lang(old_path)
        renamed_away = (
            f.status is FileStatus.RENAMED
            and coll.collected(old_path)
            and not coll.collected(f.path)
        )
        gone = f.status is FileStatus.DELETED or renamed_away
        if gone and _is_collected(old_path) and old_lang is None:
            notes[f.path] = f"{_rename_note(f)} removes a test file"  # definitions not parsed
            continue
        lang = old_lang or new_lang
        if lang is None:
            continue
        if renamed_away:
            try:
                raw = _read_base(ctx, old_path)
            except (GitError, OSError):
                raw = None
            if raw is None:
                notes[f.path] = f"{_rename_note(f)} moves a test file out of test paths"
                continue
            lines = raw.decode("utf-8", "replace").splitlines()
            removed[f.path] = defined_tests(lines, lang)
            continue
        if _hidden_lines(f) or (f.status is FileStatus.DELETED and (f.binary or f.truncated)):
            why = "binary" if f.binary else "truncated"
            notes[f.path] = f"{_rename_note(f)} has test lines that are not fully visible ({why})"
            continue
        gained = defined_tests(f.added, lang) if new_lang else Counter()
        lost = defined_tests(f.removed, lang)
        removed[f.path] = lost - gained
        if new_lang and coll.collected(f.path):
            # only a destination the runner collects makes a move (helpers.py, build/ or a
            # path outside testpaths would hide the test)
            pool.update(gained - lost)
    for path in sorted(removed):
        missing = []
        for name in sorted(removed[path].elements()):
            if pool[name] > 0:
                pool[name] -= 1  # moved to another changed test file
            else:
                missing.append(_safe(name))
        if missing:
            f = next(c for c in change.files if c.path == path)
            verb = "deletes the file, removing" if f.status is FileStatus.DELETED else "removes"
            notes[path] = f"{_rename_note(f)} {verb} {_shown(missing)}"
    return notes


def _file_aliases(f: ChangedFile, ctx: _Ctx, lang: str) -> dict[str, str]:
    """Local names of pytest/unittest (Python) or it/test/describe (JS) in `f`: from its BASE
    content plus its added lines (a superset: fail closed)."""
    try:
        raw = _read_base(ctx, f.old_path or f.path)
    except (GitError, OSError):
        raw = None
    lines = [_code_only(ln, lang) for ln in [*_decode_lines(raw), *f.added]]
    return py_aliases(lines) if lang == "py" else js_aliases(lines)


def _tests_skip_added(change: ChangeSet, ctx: _Ctx) -> dict[str, str]:
    pool: Counter[str] = Counter()  # marker lines removed anywhere (a moved marker is not new)
    aliases: dict[str, dict[str, str]] = {}
    for f in change.files:
        lang = _test_lang(f.old_path or f.path) or _test_lang(f.path)
        if lang:
            aliases[f.path] = names = _file_aliases(f, ctx, lang)
            pool.update(_marker_key(ln, lang) for ln in f.removed if skip_markers(ln, lang, names))
    notes: dict[str, str] = {}
    for f in change.files:
        lang = _test_lang(f.path)
        if lang is None or f.status is FileStatus.DELETED:
            continue
        hidden = _hidden_lines(f)
        if hidden:
            notes[f.path] = (
                f"{_rename_note(f)} has added lines that are not fully visible ({hidden})"
            )
            continue
        labels: list[str] = []
        names = aliases.get(f.path) or _file_aliases(f, ctx, lang)
        for ln in f.added:
            found = skip_markers(ln, lang, names)
            if not found:
                continue
            key = _marker_key(ln, lang)
            if pool[key] > 0:
                pool[key] -= 1
                continue
            labels.extend(found)
        if labels:
            notes[f.path] = f"{_rename_note(f)} adds {_shown(dict.fromkeys(labels))}"
    return notes


# ---- tests.discovery_narrowed


def _command_options(
    words: list[str], value_opts: tuple[str, ...], flag_opts: tuple[str, ...]
) -> list[tuple[str, str]]:
    """Narrowing (option, value) pairs in a runner's argument words."""
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(words):
        w = words[i]
        i += 1
        for opt in value_opts:
            if w == opt:
                out.append((opt, words[i] if i < len(words) else ""))
                i += 1
                break
            if w.startswith(opt + "="):
                out.append((opt, w[len(opt) + 1 :]))
                break
            if len(opt) == 2 and w.startswith(opt) and not w.startswith("--"):
                out.append((opt, w[2:]))
                break
        else:
            for opt in flag_opts:
                if w == opt or w.startswith(opt + "="):
                    out.append((opt, ""))
                    break
    return out


_COMMAND_SPLIT_RE = re.compile(r"&&|\|\||[;|\n]")
# Label for a positional argument (a test file, directory or jest path pattern).
POSITIONAL = "test-path"
# Words before a runner name that mean it is being installed, not run (`pip install pytest`).
_INSTALL_WORDS = frozenset({"install", "add", "i", "uninstall", "remove", "rm"})
# Package-manager commands that are not scripts (`yarn add x`, `pnpm install`).
_PM_COMMANDS = frozenset(
    {
        "install", "i", "ci", "add", "remove", "rm", "uninstall", "exec", "dlx", "x", "why",
        "audit", "outdated", "update", "upgrade", "up", "link", "publish", "pack", "init",
        "create", "config", "cache", "list", "ls", "info", "view", "set", "version",
    }
)  # fmt: skip


def _positionals(words: list[str], arg_opts: frozenset[str], js: bool) -> list[tuple[str, str]]:
    """Positional arguments (test paths or path patterns) among a runner's argument words."""
    out: list[tuple[str, str]] = []
    skip_next = False
    for idx, w in enumerate(words):
        if skip_next:
            skip_next = False
            continue
        if _SHELL_REDIRECT_RE.match(w):
            skip_next = re.fullmatch(r"\d*(?:>>?|<)&?", w) is not None  # `> out.txt`
            continue
        if w.startswith("-"):
            skip_next = "=" not in w and w in arg_opts
            continue
        if js and idx == 0 and w in VITEST_SUBCOMMANDS:
            continue
        out.append((POSITIONAL, w))
    return out


def _runner_args(words: list[str], kind: str) -> list[tuple[str, str]]:
    """Narrowing options and positional test paths in a runner's argument words."""
    if kind == "pytest":
        found = _command_options(words, PYTEST_VALUE_OPTIONS, PYTEST_FLAG_OPTIONS)
        return found + _positionals(words, PYTEST_ARG_OPTIONS, js=False)
    found = _command_options(words, JEST_VALUE_OPTIONS, JEST_FLAG_OPTIONS)
    return found + _positionals(words, JS_ARG_OPTIONS, js=True)


def _passthrough_args(pm: str, rest: list[str]) -> list[tuple[str, str]] | None:
    """Narrowing arguments passed through `<pm> [run] <script> [--] ...` to the script's runner
    (which may be jest, vitest or pytest: both option sets count). None: not a script run."""
    if not rest:
        return None
    if rest[0] in ("run", "run-script") and len(rest) > 1:
        script, args = rest[1], rest[2:]
    elif rest[0] in ("test", "t", "tst"):
        script, args = "test", rest[1:]
    elif pm != "npm" and not rest[0].startswith("-") and rest[0] not in _PM_COMMANDS:
        script, args = rest[0], rest[1:]
    else:
        return None
    if "--" in args:
        args = args[args.index("--") + 1 :]
    elif pm == "npm":
        return []  # npm keeps options before `--` for itself
    value_opts = JEST_VALUE_OPTIONS + PYTEST_VALUE_OPTIONS
    flag_opts = JEST_FLAG_OPTIONS + PYTEST_FLAG_OPTIONS
    out = _command_options(args, value_opts, flag_opts)
    if any(k in script.lower() for k in ("test", "spec", "e2e")):
        out += _positionals(args, JS_ARG_OPTIONS | PYTEST_ARG_OPTIONS, js=True)
    return out


def command_narrowing(text: str) -> Counter[tuple[str, str]]:
    """Narrowing given to pytest or jest/vitest in shell command lines: runner options
    (-k, -t, --ignore, ...), positional test paths, runner environment variables
    (PYTEST_ADDOPTS=, JEST_*, VITEST_*) and arguments passed through a package manager script
    (`npm test -- -t x`)."""
    out: Counter[tuple[str, str]] = Counter()
    for cmd in _COMMAND_SPLIT_RE.split(text):
        for m in RUNNER_ENV_RE.finditer(cmd):
            out[(m.group(1) + "=", " ".join(cmd.split()))] += 1
        try:
            words = shlex.split(cmd)
        except ValueError:
            words = cmd.split()
        for i, w in enumerate(words):
            if w in _INSTALL_WORDS:
                break  # installing the runner, not running it
            runner = posixpath.basename(w.lstrip("@-+"))
            if runner in PYTEST_RUNNERS:
                out.update(_runner_args(words[i + 1 :], "pytest"))
                break
            if runner in JS_TEST_RUNNERS:
                out.update(_runner_args(words[i + 1 :], "js"))
                break
            passed = (
                _passthrough_args(runner, words[i + 1 :]) if runner in PACKAGE_MANAGERS else None
            )
            if passed is not None:
                out.update(passed)
                break
    return out


def _as_list(value: Any, *, words: bool = False) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value)
    if words:
        try:
            return shlex.split(text)
        except ValueError:  # unbalanced quotes: fall back to whitespace words
            pass
    return text.split()


def _pytest_section(name: str, raw: bytes) -> dict[str, Any] | None:
    """The pytest settings in a config file (None when it has no pytest section). Raises on a
    parse error (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError, configparser.Error)."""
    fmt, section = PYTEST_CONFIG_FILES[name]
    text = raw.decode("utf-8")
    if fmt == "toml":
        data = tomllib.loads(text)
        if section == "pytest":  # pytest.toml / .pytest.toml: a [pytest] table
            table = data.get("pytest")
            return table if isinstance(table, dict) else None
        tool = data.get("tool", {})
        pytest_table = tool.get("pytest") if isinstance(tool, dict) else None
        if not isinstance(pytest_table, dict):
            return None
        merged = {k: v for k, v in pytest_table.items() if k != "ini_options"}
        ini = pytest_table.get("ini_options", {})
        if isinstance(ini, dict):
            merged.update(ini)
        return merged
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(text)
    return dict(parser[section]) if parser.has_section(section) else None


def _pytest_settings(name: str, raw: bytes) -> dict[str, Any]:
    """The pytest settings in a config file ({} when it has no pytest section); raises like
    _pytest_section."""
    return _pytest_section(name, raw) or {}


def _selection_narrowed(old: list[str] | None, new: list[str] | None, default) -> bool:
    old_eff = old if old is not None else default
    new_eff = new if new is not None else default
    if new_eff is None:
        return False  # selects everything
    return old_eff is None or bool(set(old_eff) - set(new_eff))


def _exclusion_narrowed(old: list[str] | None, new: list[str] | None, default) -> bool:
    old_eff = old if old is not None else list(default)
    new_eff = new if new is not None else list(default)
    return bool(set(new_eff) - set(old_eff))


def _options_text(pairs: Counter[tuple[str, str]]) -> str:
    return " ".join(sorted({opt for opt, _ in pairs}))


def pytest_narrowing(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What narrows between two pytest settings dicts (empty: nothing)."""
    out = []
    for key, default in PYTEST_SELECT_KEYS.items():
        if _selection_narrowed(_as_list(old.get(key)), _as_list(new.get(key)), default):
            out.append(f"narrows {key}")
    for key, default in PYTEST_EXCLUDE_KEYS.items():
        if _exclusion_narrowed(_as_list(old.get(key)), _as_list(new.get(key)), default):
            out.append(f"extends {key}")
    words_old = _as_list(old.get("addopts"), words=True) or []
    words_new = _as_list(new.get("addopts"), words=True) or []
    # a positional path in addopts replaces testpaths
    gained = Counter(_runner_args(words_new, "pytest")) - Counter(_runner_args(words_old, "pytest"))
    if gained:
        out.append(f"adds {_options_text(gained)} to addopts")
    return out


def jest_narrowing(old: Any, new: Any) -> list[str]:
    """What narrows between two jest config objects (empty: nothing)."""
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    out = []
    for key, select_default in JEST_SELECT_KEYS.items():
        if _selection_narrowed(_as_list(old.get(key)), _as_list(new.get(key)), select_default):
            out.append(f"narrows jest {key}")
    for key, default in JEST_EXCLUDE_KEYS.items():
        if _exclusion_narrowed(_as_list(old.get(key)), _as_list(new.get(key)), default):
            out.append(f"extends jest {key}")
    return out


def _scripts_narrowing(old: Any, new: Any) -> list[str]:
    old_scripts = old.get("scripts") if isinstance(old, dict) else None
    new_scripts = new.get("scripts") if isinstance(new, dict) else None
    old_scripts = old_scripts if isinstance(old_scripts, dict) else {}
    new_scripts = new_scripts if isinstance(new_scripts, dict) else {}
    out = []
    for name, body in sorted(new_scripts.items()):
        before = old_scripts.get(name)
        gained = command_narrowing(str(body)) - command_narrowing(
            str(before) if before is not None else ""
        )
        if gained:
            out.append(f"adds {_options_text(gained)} to script {_safe(str(name), 40)}")
    return out


def _key_regions(lines: list[str]) -> set[str]:
    """Stripped lines inside a JS_CONFIG_KEYS entry (from the key to its balanced bracket)."""
    key_re = re.compile(r"""(?<![\w$])['"]?(?:""" + "|".join(JS_CONFIG_KEYS) + r""")['"]?\s*:""")
    out: set[str] = set()
    i = 0
    while i < len(lines):
        m = key_re.search(lines[i])
        if not m:
            i += 1
            continue
        depth, j = 0, i
        while j < len(lines) and j < i + 200:
            text = lines[j][m.end() :] if j == i else lines[j]
            depth += sum(text.count(c) for c in "[({") - sum(text.count(c) for c in "])}")
            out.add(lines[j].strip())
            j += 1
            if depth <= 0:
                break
        i = j
    out.discard("")
    return out


def _decode_lines(raw: bytes | None) -> list[str]:
    return raw.decode("utf-8", "replace").splitlines() if raw is not None else []


def _logical_lines(raw: bytes | None) -> str:
    """Shell/Makefile text with backslash continuations joined (one command per line)."""
    return re.sub(r"\\\r?\n", " ", raw.decode("utf-8", "replace")) if raw is not None else ""


def _script_content_narrowing(f: ChangedFile, ctx: _Ctx) -> Counter[tuple[str, str]]:
    """Narrowing in the working-tree commands of a Makefile or script that BASE lacks (so a
    command split over continuation lines is read whole). Unreadable sides count as empty;
    the diff lines are compared as well."""
    try:
        after = git.read_worktree(ctx.repo, f.path) if f.status is not FileStatus.DELETED else None
        before = _read_base(ctx, f.old_path or f.path) if f.status is not FileStatus.ADDED else None
    except (GitError, OSError):
        return Counter()
    old = Counter(" ".join(ln.split()) for ln in _logical_lines(before).splitlines())
    new = Counter(" ".join(ln.split()) for ln in _logical_lines(after).splitlines())
    return command_narrowing("\n".join((new - old).elements())) - command_narrowing(
        "\n".join((old - new).elements())
    )


def _discovery_file(f: ChangedFile, ctx: _Ctx) -> str | None:
    """Why `f` narrows test discovery, or None."""
    _dirs, name, _stem, _ext = _parts(f.path)
    old_path = f.old_path or f.path
    is_js_config = name not in JEST_JSON_CONFIGS and any(
        fnmatch.fnmatchcase(name, g) for g in JS_CONFIG_GLOBS
    )
    is_make = name in MAKEFILE_NAMES or any(fnmatch.fnmatchcase(name, g) for g in MAKEFILE_GLOBS)
    if is_js_config or is_make:
        if f.status is not FileStatus.DELETED and _hidden_lines(f):
            return f"changed lines are not fully visible ({_hidden_lines(f)})"
        added = Counter(ln.strip() for ln in f.added) - Counter(ln.strip() for ln in f.removed)
        lost = Counter(ln.strip() for ln in f.removed) - Counter(ln.strip() for ln in f.added)
        if is_make:
            gained = command_narrowing("\n".join(added.elements())) - command_narrowing(
                "\n".join(lost.elements())
            )
            gained |= _script_content_narrowing(f, ctx)
            return f"adds {_options_text(gained)} to a test command" if gained else None
        try:
            after = (
                git.read_worktree(ctx.repo, f.path) if f.status is not FileStatus.DELETED else None
            )
            before = _read_base(ctx, old_path) if f.status is not FileStatus.ADDED else None
        except (GitError, OSError):
            return "could not be read (fail closed)"
        hits = (set(added) & _key_regions(_decode_lines(after))) | (
            set(lost) & _key_regions(_decode_lines(before))
        )
        if not hits:
            return None
        keys = sorted({k for k in JS_CONFIG_KEYS for h in hits if re.search(rf"\b{k}\b", h)})
        return f"changes test selection ({', '.join(keys) or 'inside ' + '/'.join(JS_CONFIG_KEYS)})"

    if name not in PYTEST_CONFIG_FILES and name != "package.json" and name not in JEST_JSON_CONFIGS:
        return None
    try:
        before = _read_base(ctx, old_path) if f.status is not FileStatus.ADDED else None
        after = git.read_worktree(ctx.repo, f.path) if f.status is not FileStatus.DELETED else None
    except (GitError, OSError):
        return "could not be read (fail closed)"
    if after is None and f.status is not FileStatus.DELETED:
        return "could not be read (fail closed)"

    def parse(raw: bytes | None) -> Any:
        if raw is None:
            return {}
        if name in PYTEST_CONFIG_FILES:
            return _pytest_settings(name, raw)
        return json.loads(raw.decode("utf-8"))

    errors = (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError, configparser.Error)
    try:
        new = parse(after)
    except errors:
        return "could not be parsed (fail closed)"
    try:
        old = parse(before)
    except errors:
        old = {}  # BASE unparsable: compare with the runner defaults
    if name in PYTEST_CONFIG_FILES:
        found = pytest_narrowing(old, new)
    elif name in JEST_JSON_CONFIGS:
        found = jest_narrowing(old, new)
    else:
        found = jest_narrowing(
            old.get("jest") if isinstance(old, dict) else None,
            new.get("jest") if isinstance(new, dict) else None,
        ) + _scripts_narrowing(old, new)
    return ", ".join(found) if found else None


def _discovery_narrowed(change: ChangeSet, ctx: _Ctx) -> dict[str, str]:
    notes = {}
    for f in change.files:
        why = _discovery_file(f, ctx)
        if why:
            notes[f.path] = f"{_rename_note(f)} {why}"
    return notes


def tamper_findings(change: ChangeSet, ctx: _Ctx) -> dict[str, dict[str, str]]:
    """Per tamper signal, the changed paths it fires for and a note on each."""
    return {
        "tests.deleted": _tests_deleted(change, ctx),
        "tests.skip_added": _tests_skip_added(change, ctx),
        "tests.discovery_narrowed": _discovery_narrowed(change, ctx),
    }


def _tamper_rule(sid: str, description: str) -> Rule:
    return Rule(
        sid,
        "tests",
        Risk.ELEVATED,
        description,
        lambda f, ctx: (f.path,) if f.path in ctx.tamper.get(sid, {}) else (),
        lambda f, ctx: ctx.tamper.get(sid, {}).get(f.path),
    )


def tamper_notes(signal: Signal) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(note, paths it concerns) for a tamper signal, parsed from its detail. A signal file that
    no note concerns gets a generic note, so every file can still taint a gate."""
    parts = signal.detail.split("; ")[1:]
    out: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for part in parts:
        paths = tuple(
            p for p in signal.files if part.startswith(p + " ") or f"(renamed from {p})" in part
        )
        if paths:
            out.append((part, paths))
            seen.update(paths)
    for p in signal.files:
        if p not in seen:
            out.append((f"{p} is involved", (p,)))
    return tuple(out)


CATALOGUE: tuple[Rule, ...] = (
    Rule(
        "docs.changed",
        "docs",
        Risk.LOW,
        "documentation file ("
        + " ".join(sorted(DOC_EXTENSIONS | COMPILED_DOC_EXTENSIONS))
        + ", README-style names, doc formats under docs/)",
        _each_path(_is_doc),
    ),
    Rule(
        "code.changed",
        "code",
        Risk.STANDARD,
        "source-code file",
        _each_path(_is_code),
    ),
    Rule(
        "frontend.ui",
        "frontend",
        Risk.STANDARD,
        "UI file (markup, component or stylesheet)",
        _each_path(_is_frontend),
    ),
    Rule(
        "tests.changed",
        "tests",
        Risk.STANDARD,
        "test file",
        _each_path(_is_test),
    ),
    Rule(
        "security.auth_path",
        "security",
        Risk.ELEVATED,
        "path segment or file stem in {" + ", ".join(sorted(AUTH_WORDS)) + "}",
        _each_path(_is_auth_path),
    ),
    Rule(
        "security.sensitive_identifier",
        "security",
        Risk.STANDARD,
        "added non-test code line mentions a sensitive identifier, or its added lines are not "
        "fully visible",
        _sensitive_identifier,
        _sensitive_note,
    ),
    Rule(
        "build.config_changed",
        "build",
        Risk.STANDARD,
        "non-dependency edit to a manifest or tool config",
        _build_config_changed,
    ),
    Rule(
        "dependency.changed",
        "dependencies",
        Risk.STANDARD,
        "lockfile changed, or dependency sections of package.json / pyproject.toml / Cargo.toml "
        "differ between BASE and the working tree",
        _dependency_changed,
    ),
    Rule(
        "policy.self_modified",
        "policy",
        Risk.ELEVATED,
        f"{POLICY_PATH} changed; the change cannot alter its own plan (policy is read at BASE)",
        _each_path(lambda p: p == POLICY_PATH),
    ),
    Rule(
        "ci.changed",
        "ci",
        Risk.ELEVATED,
        "CI configuration changed; CI is where the authoritative verdict runs",
        _each_path(_is_ci),
    ),
    Rule(
        "agent.config_changed",
        "agent-config",
        Risk.ELEVATED,
        "agent instructions, settings, hooks or MCP config changed",
        _each_path(_is_agent_config),
    ),
    Rule(
        "agent.skill_changed",
        "agent-config",
        Risk.ELEVATED,
        "agent skill added or changed (SKILL.md, or a file under .agents/skills/<n>/, "
        ".claude/skills/<n>/, skills/<n>/ or a directory with a SKILL.md)",
        _skill_changed,
    ),
    _tamper_rule(
        "tests.deleted",
        "test file deleted or test definitions removed (def test_, class Test, it/test/describe), "
        "not counting tests moved to another changed test file",
    ),
    _tamper_rule(
        "tests.skip_added",
        "skip, disable or focus marker added in a test file (pytest/unittest skip and xfail, "
        "it.skip, xit, it.todo, it.only, fit, ...)",
    ),
    _tamper_rule(
        "tests.discovery_narrowed",
        "test discovery narrowed (pytest testpaths/python_*/norecursedirs/addopts, jest/vitest "
        "testMatch/testRegex/testPathIgnorePatterns/include/exclude, -k/-m/--ignore/--deselect "
        "in scripts or Makefiles)",
    ),
)

UNCLASSIFIED = Rule(
    "coverage.unclassified",
    "unknown",
    Risk.STANDARD,
    "file matched no signal rule; the plan is widened",
    lambda _f, _ctx: (),
)


# --------------------------------------------------------------------------- custom globs


@cache
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile an fnmatch-style glob where `*`/`?` stay within a segment and `**` spans
    segments (`**/` also matches zero directories)."""
    i, n, out = 0, len(pattern), []
    while i < n:
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 2 if pattern[i + 1 : i + 2] in ("!", "]") else i + 1)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1 : j].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z", re.DOTALL)


def glob_match(path: str, pattern: str) -> bool:
    """True when the repo-relative POSIX `path` matches the policy glob `pattern`."""
    return _glob_regex(pattern.removeprefix("./")).match(path) is not None


def _custom_rule(spec: SignalRuleSpec) -> Rule:
    globs = spec.paths
    return Rule(
        spec.id,
        spec.domain,
        spec.risk,
        "policy rule: " + ", ".join(globs),
        lambda f, _ctx: tuple(p for p in _candidates(f) if any(glob_match(p, g) for g in globs)),
    )


# --------------------------------------------------------------------------- evaluation


def evaluate_signals(repo: Path, change: ChangeSet, policy: Policy) -> tuple[Signal, ...]:
    """Evaluate the catalogue plus policy rules over `change`. Sorted by id. Deterministic.

    build.config_changed and tests.discovery_narrowed need BASE vs working-tree content of
    manifests and test configs (git.read_at with change.base, git.read_worktree), and
    tests.deleted reads BASE for a test file renamed out of test paths; everything else uses
    paths, status and added/removed lines.
    """
    ctx = _Ctx(Path(repo), change.base)
    ctx = _Ctx(ctx.repo, ctx.base, tamper_findings(change, ctx))
    builtin_ids = {r.id for r in CATALOGUE} | {UNCLASSIFIED.id}
    # Add-only: a policy rule can never replace a built-in rule, even with a colliding id.
    custom = tuple(
        _custom_rule(s)
        for s in policy.signals
        if s.id.startswith("custom.") and s.id not in builtin_ids
    )
    rules = CATALOGUE + custom
    hits: dict[str, tuple[Rule, set[str], list[str]]] = {}

    def hit(r: Rule, f: ChangedFile, paths: tuple[str, ...]) -> None:
        _rule, files, notes = hits.setdefault(r.id, (r, set(), []))
        files.update(paths)
        note = r.note(f, ctx) if r.note else None
        if note:
            notes.append(note)

    for f in change.files:
        candidates = _candidates(f)
        classified: set[str] = set()
        for r in rules:
            claimed = r.match(f, ctx)
            if claimed:
                classified.update(claimed)
                hit(r, f, candidates)  # a rename lists both paths
        if not classified.issuperset(candidates):
            hit(UNCLASSIFIED, f, candidates)
    return tuple(
        Signal(
            id=r.id,
            domain=r.domain,
            risk=r.risk,
            files=tuple(sorted(files)),
            detail="; ".join([r.description, *sorted(notes)]),
        )
        for _id, (r, files, notes) in sorted(hits.items())
    )


# --------------------------------------------------------------------------- task text

# Keyword map for untrusted task text. Words are matched whole, case-insensitively, after
# splitting on non-alphanumerics ("refresh-token" -> "refresh", "token"). Add-only.
TASK_KEYWORDS: dict[str, tuple[str, Risk, frozenset[str]]] = {
    "task.docs": (
        "docs",
        Risk.LOW,
        frozenset({"doc", "docs", "documentation", "readme", "changelog", "typo", "typos"}),
    ),
    "task.frontend": (
        "frontend",
        Risk.STANDARD,
        frozenset(
            {
                "ui", "ux", "frontend", "css", "style", "styles", "styling", "component",
                "components", "layout", "button", "form", "page", "react", "vue", "svelte",
                "accessibility", "a11y",
            }
        ),
    ),
    "task.security": (
        "security",
        Risk.ELEVATED,
        frozenset(
            {
                "auth", "authentication", "authorization", "authorisation", "oauth", "login",
                "logout", "session", "sessions", "token", "tokens", "jwt", "password",
                "passwords", "secret", "secrets", "credential", "credentials", "crypto",
                "encryption", "encrypt", "decrypt", "permission", "permissions", "acl",
                "security", "vulnerability", "cve", "csrf", "xss", "injection", "sso",
            }
        ),
    ),
    "task.dependencies": (
        "dependencies",
        Risk.STANDARD,
        frozenset(
            {
                "dependency", "dependencies", "deps", "upgrade", "bump", "lockfile",
                "package", "packages", "npm", "pip",
            }
        ),
    ),
    "task.tests": (
        "tests",
        Risk.STANDARD,
        frozenset({"test", "tests", "testing", "coverage", "flaky"}),
    ),
    "task.ci": (
        "ci",
        Risk.ELEVATED,
        frozenset({"ci", "pipeline", "workflow", "workflows"}),
    ),
    "task.data": (
        "data",
        Risk.ELEVATED,
        frozenset({"migration", "migrations", "database", "db", "schema", "sql"}),
    ),
}  # fmt: skip

_WORD_RE = re.compile(r"[a-z0-9]+")


def task_signals(task: str | None) -> tuple[Signal, ...]:
    """Add-only signals from untrusted task text: keyword map to "task.<domain>" signals
    (task.docs, task.frontend, task.security, task.dependencies, ...), risk per domain
    (task.security -> elevated). Never produces anything that removes a requirement.
    Empty/None task -> ().
    """
    if not task:
        return ()
    words = set(_WORD_RE.findall(task.lower()))
    out = []
    for sid, (domain, risk, keywords) in sorted(TASK_KEYWORDS.items()):
        found = sorted(words & keywords)
        if found:
            # Only catalogue keywords are echoed, never free task text (T10).
            out.append(Signal(sid, domain, risk, (), "task keywords: " + ", ".join(found)))
    return tuple(out)
