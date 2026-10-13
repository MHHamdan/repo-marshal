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

v0.2 test-tamper signals (roadmap SHOULD #2; threat T2). All three are elevated and widening, and
decisions.decide_gates taints the test-running gates whose scope covers their files. A "test
source" is a tests.changed path with a Python or JS/TS extension; its lines are what the
definition and marker patterns below are matched against.
- tests.deleted: a test source loses test definitions (removed lines minus the same definitions
  re-added in that file). A definition re-added in another changed test file is a move, not a
  deletion (matched by name, change-set wide, counted). A deleted test file, or a test file
  renamed to a path no runner collects, loses every definition it had at BASE; a rename between
  test paths is analysed like an edit. A deleted or renamed-away test file in a language whose
  definitions are not parsed (e.g. `_test.go`) always fires. Fails closed on hidden lines.
- tests.skip_added: added skip, disable or focus markers in a test source. Strings and comments
  are blanked first, so a test named `test_skip_expired` or a title "skips x" is not a marker. A
  marker line also removed elsewhere in the change set (same code once strings are blanked) is a
  move. Fails closed on hidden added lines.
- tests.discovery_narrowed: a pytest config (pyproject.toml, pytest.ini, .pytest.ini, tox.ini,
  setup.cfg), a jest config in package.json or jest.config.json, or package.json scripts are
  parsed at BASE and in the working tree and compared: a selection list (testpaths,
  python_files, ...) narrows when an effective BASE entry is gone (an absent key means the
  runner's default, and an absent testpaths means "everything"); an exclusion list narrows when
  it gains an entry; addopts and scripts narrow when they gain a narrowing option. JS config
  files (jest/vitest/vite config modules) cannot be parsed: any added or removed line inside one
  of the keys fires. Makefiles: an added pytest/jest command line with a narrowing option not on
  a removed line fires. A worktree file that cannot be read or parsed fires (fail closed).
- Notes ("<path> <what>", one per file) name tests and options, never values or other content.
"""

from __future__ import annotations

import configparser
import fnmatch
import json
import posixpath
import re
import shlex
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cache
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
        "coverage.unclassified",
        "policy.self_modified",
        "ci.changed",
        "agent.config_changed",
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
        "tox.ini", "noxfile.py", "pytest.ini", "mypy.ini", ".mypy.ini", "ruff.toml",
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

# ci.changed: CI configuration (an agent narrowing or disabling CI is a T4 threat).
CI_PREFIXES = (".github/workflows/", ".circleci/", ".buildkite/", ".gitlab/ci/")
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
)
JS_SKIP_MARKER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![\w$.])(?:it|test|describe)(?:\.\w+)*\.(?:skip\w*|todo)\b"),  # it.skip
    re.compile(r"(?<![\w$.])x(?:it|test|describe)\s*[(.]"),  # xit( xdescribe(
    # focus markers: everything else in the file is silently excluded
    re.compile(r"(?<![\w$.])(?:it|test|describe)(?:\.\w+)*\.only\b"),  # it.only
    re.compile(r"(?<![\w$.])f(?:it|describe)\s*[(.]"),  # fit( fdescribe(
)

# tests.discovery_narrowed: pytest config files -> (format, section holding the settings).
PYTEST_CONFIG_FILES: dict[str, tuple[str, str]] = {
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
JEST_SELECT_KEYS: tuple[str, ...] = ("testMatch", "testRegex")  # default: everything
JEST_EXCLUDE_KEYS: dict[str, tuple[str, ...]] = {"testPathIgnorePatterns": ("/node_modules/",)}
JEST_JSON_CONFIGS: frozenset[str] = frozenset({"jest.config.json"})
# JS config modules (not parsable without a JS engine): any change inside these keys fires.
JS_CONFIG_GLOBS: tuple[str, ...] = (
    "jest.config.*",
    "vitest.config.*",
    "vitest.workspace.*",
    "vite.config.*",
)
JS_CONFIG_KEYS: tuple[str, ...] = (
    "testMatch",
    "testPathIgnorePatterns",
    "testRegex",
    "include",
    "exclude",
)
MAKEFILE_NAMES: frozenset[str] = frozenset({"makefile", "gnumakefile"})
MAKEFILE_GLOBS: tuple[str, ...] = ("*.mk",)


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
    for key in keys:
        _drop(data, key.split("."))
    return data


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


def _code_only(line: str, lang: str) -> str:
    """The line with string literals blanked and comments removed."""
    s = _STRING_RE.sub('""', line)
    if lang == "py":
        return s.split("#", 1)[0]
    return _BLOCK_COMMENT_RE.sub("", s).split("//", 1)[0]


def defined_tests(lines: Iterable[str], lang: str) -> Counter[str]:
    """Test names defined on `lines` (Python defs/classes, or JS it/test/describe titles)."""
    out: Counter[str] = Counter()
    for line in lines:
        if lang == "py":
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


def skip_markers(line: str, lang: str) -> tuple[str, ...]:
    """Skip, disable or focus markers on one line, as short labels ("pytest.mark.skip")."""
    code = _code_only(line, lang)
    regexes = PY_SKIP_MARKER_RES if lang == "py" else JS_SKIP_MARKER_RES
    labels = []
    for rx in regexes:
        for m in rx.finditer(code):
            labels.append(re.sub(r"[\s@(]", "", m.group(0)).rstrip("."))
    return tuple(labels)


def _marker_key(line: str, lang: str) -> str:
    return " ".join(_code_only(line, lang).split())


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
    for f in change.files:
        new_lang = _test_lang(f.path) if f.status is not FileStatus.DELETED else None
        old_path = f.old_path or f.path
        old_lang = _test_lang(old_path)
        renamed_away = (
            f.status is FileStatus.RENAMED and _is_collected(old_path) and not _is_collected(f.path)
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


def _tests_skip_added(change: ChangeSet, _ctx: _Ctx) -> dict[str, str]:
    pool: Counter[str] = Counter()  # marker lines removed anywhere (a moved marker is not new)
    for f in change.files:
        lang = _test_lang(f.old_path or f.path) or _test_lang(f.path)
        if lang:
            pool.update(_marker_key(ln, lang) for ln in f.removed if skip_markers(ln, lang))
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
        for ln in f.added:
            found = skip_markers(ln, lang)
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


def command_narrowing(text: str) -> Counter[tuple[str, str]]:
    """Narrowing options given to pytest or jest/vitest in a shell command line."""
    out: Counter[tuple[str, str]] = Counter()
    for cmd in _COMMAND_SPLIT_RE.split(text):
        try:
            words = shlex.split(cmd)
        except ValueError:
            words = cmd.split()
        for i, w in enumerate(words):
            runner = posixpath.basename(w.lstrip("@-+"))
            if runner in PYTEST_RUNNERS:
                out.update(
                    _command_options(words[i + 1 :], PYTEST_VALUE_OPTIONS, PYTEST_FLAG_OPTIONS)
                )
                break
            if runner in JS_TEST_RUNNERS:
                out.update(_command_options(words[i + 1 :], JEST_VALUE_OPTIONS, JEST_FLAG_OPTIONS))
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


def _pytest_settings(name: str, raw: bytes) -> dict[str, Any]:
    """The pytest settings in a config file ({} when it has no pytest section). Raises on a parse
    error (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError, configparser.Error)."""
    fmt, section = PYTEST_CONFIG_FILES[name]
    text = raw.decode("utf-8")
    if fmt == "toml":
        tool = tomllib.loads(text).get("tool", {})
        pytest_table = tool.get("pytest", {}) if isinstance(tool, dict) else {}
        if not isinstance(pytest_table, dict):
            return {}
        merged = {k: v for k, v in pytest_table.items() if k != "ini_options"}
        ini = pytest_table.get("ini_options", {})
        if isinstance(ini, dict):
            merged.update(ini)
        return merged
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(text)
    return dict(parser[section]) if parser.has_section(section) else {}


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
    gained = Counter(
        _command_options(words_new, PYTEST_VALUE_OPTIONS, PYTEST_FLAG_OPTIONS)
    ) - Counter(_command_options(words_old, PYTEST_VALUE_OPTIONS, PYTEST_FLAG_OPTIONS))
    if gained:
        out.append(f"adds {_options_text(gained)} to addopts")
    return out


def jest_narrowing(old: Any, new: Any) -> list[str]:
    """What narrows between two jest config objects (empty: nothing)."""
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    out = []
    for key in JEST_SELECT_KEYS:
        if _selection_narrowed(_as_list(old.get(key)), _as_list(new.get(key)), None):
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
