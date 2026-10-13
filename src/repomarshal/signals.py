"""Signal catalogue and evaluation (owner: agent "planner"; roadmap M5; architecture §4.1; ADR-005).

The catalogue is data (a tuple of rules with stable ids). v0.1 signals:
  docs.changed, code.changed, frontend.ui, tests.changed, security.auth_path,
  security.sensitive_identifier, build.config_changed, policy.self_modified,
  coverage.unclassified
plus policy [[signals]] ("custom.*", path globs, add-only). One Signal per (id) aggregating all
matching files (files sorted). A file that matches no rule (built-in or custom) produces
coverage.unclassified. Deleted files count (their path still matters).

Matching notes (the exact pattern sets are the constants below):
- Path matching is case-insensitive and uses POSIX paths. For a rename, both the new and the old
  path are matched (moving a file out of `auth/` still touches auth code); the signal lists the
  new path.
- "Segment" means a directory component of the path (not the file name). "Stem" means the file
  name without its final extension, compared exactly (`LoginForm.tsx` has stem `loginform`, which
  is not `login`).
- security.auth_path ignores documentation, stylesheet and static-asset files (`login.css`,
  `docs/security/threat-model.md`): they cannot change auth behaviour, and every changed file
  still gets some signal, so this can never shrink a plan to nothing.
- build.config_changed compares BASE (git.read_at) with the working tree (git.read_worktree) for
  dependency-aware manifests and ignores the dependency sections. A manifest that is added,
  deleted, unreadable or unparsable on either side always fires (fail closed).
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
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
        # reserved for SHOULD signals, accepted if present:
        "ci.changed",
        "agent.config_changed",
        "tests.deleted",
    }
)

# --------------------------------------------------------------------------- pattern data

# docs.changed: documentation extensions anywhere, plain-text or extensionless files whose stem is
# a well-known doc name, and anything with a `docs`/`doc` directory segment.
DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".markdown", ".mdx", ".rst", ".adoc"})
DOC_TEXT_STEMS: frozenset[str] = frozenset(
    {"readme", "changelog", "changes", "history", "license", "notice", "authors", "contributing"}
)
DOC_SEGMENTS: frozenset[str] = frozenset({"docs", "doc"})

# code.changed: source-code extensions.
CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py", ".pyi",
        ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte",
        ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".groovy",
        ".rb", ".php", ".pl", ".pm", ".lua", ".ex", ".exs", ".erl", ".hs", ".clj", ".dart",
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm", ".cs", ".fs", ".swift",
        ".sh", ".bash", ".zsh", ".ps1",
    }
)  # fmt: skip

# frontend.ui: UI extensions, or a UI directory segment.
FRONTEND_EXTENSIONS: frozenset[str] = frozenset(
    {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".sass", ".less", ".html", ".htm"}
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

# policy.self_modified: the policy file at the repository root.
POLICY_PATH = "repomarshal.toml"


# --------------------------------------------------------------------------- catalogue


@dataclass(frozen=True)
class _Ctx:
    """Per-evaluation context passed to matchers that need repository content."""

    repo: Path
    base: str | None


Matcher = Callable[[ChangedFile, _Ctx], bool]


@dataclass(frozen=True)
class Rule:
    id: str
    domain: str
    risk: Risk
    description: str
    match: Matcher


def _parts(path: str) -> tuple[tuple[str, ...], str, str, str]:
    """(directory segments, file name, stem, extension), all lower-cased."""
    p = path.lower()
    segs = tuple(s for s in p.split("/") if s)
    name = segs[-1] if segs else ""
    stem, ext = posixpath.splitext(name)
    return segs[:-1], name, stem, ext


def _candidates(f: ChangedFile) -> tuple[str, ...]:
    return (f.path, f.old_path) if f.old_path and f.old_path != f.path else (f.path,)


def _any_path(pred: Callable[[str], bool]) -> Matcher:
    return lambda f, _ctx: any(pred(p) for p in _candidates(f))


def _is_doc(path: str) -> bool:
    dirs, _name, stem, ext = _parts(path)
    return (
        ext in DOC_EXTENSIONS
        or (ext in ("", ".txt") and stem in DOC_TEXT_STEMS)
        or any(d in DOC_SEGMENTS for d in dirs)
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


def _sensitive_identifier(f: ChangedFile, _ctx: _Ctx) -> bool:
    if f.binary or not _is_code(f.path) or _is_test(f.path):
        return False
    return any(SENSITIVE_IDENTIFIER_RE.search(line) for line in f.added)


def _is_build_config_name(path: str) -> bool:
    _dirs, name, _stem, _ext = _parts(path)
    return name in BUILD_CONFIG_NAMES or any(
        fnmatch.fnmatchcase(name, g) for g in BUILD_CONFIG_GLOBS
    )


def _build_config_changed(f: ChangedFile, ctx: _Ctx) -> bool:
    if not any(_is_build_config_name(p) for p in _candidates(f)):
        return False
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


CATALOGUE: tuple[Rule, ...] = (
    Rule(
        "docs.changed",
        "docs",
        Risk.LOW,
        "documentation file (" + " ".join(sorted(DOC_EXTENSIONS)) + ", docs/ segment)",
        _any_path(_is_doc),
    ),
    Rule(
        "code.changed",
        "code",
        Risk.STANDARD,
        "source-code file",
        _any_path(_is_code),
    ),
    Rule(
        "frontend.ui",
        "frontend",
        Risk.STANDARD,
        "UI file (markup, component or stylesheet)",
        _any_path(_is_frontend),
    ),
    Rule(
        "tests.changed",
        "tests",
        Risk.STANDARD,
        "test file",
        _any_path(_is_test),
    ),
    Rule(
        "security.auth_path",
        "security",
        Risk.ELEVATED,
        "path segment or file stem in {" + ", ".join(sorted(AUTH_WORDS)) + "}",
        _any_path(_is_auth_path),
    ),
    Rule(
        "security.sensitive_identifier",
        "security",
        Risk.STANDARD,
        "added non-test code line mentions a sensitive identifier",
        _sensitive_identifier,
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
        _any_path(lambda p: p == POLICY_PATH),
    ),
)

UNCLASSIFIED = Rule(
    "coverage.unclassified",
    "unknown",
    Risk.STANDARD,
    "file matched no signal rule; the plan is widened",
    lambda _f, _ctx: False,
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
        lambda f, _ctx: any(glob_match(p, g) for p in _candidates(f) for g in globs),
    )


# --------------------------------------------------------------------------- evaluation


def evaluate_signals(repo: Path, change: ChangeSet, policy: Policy) -> tuple[Signal, ...]:
    """Evaluate the catalogue plus policy rules over `change`. Sorted by id. Deterministic.

    build.config_changed needs BASE vs working-tree content of manifests (git.read_at with
    change.base, git.read_worktree); everything else uses paths, status and added lines.
    """
    ctx = _Ctx(Path(repo), change.base)
    builtin_ids = {r.id for r in CATALOGUE} | {UNCLASSIFIED.id}
    # Add-only: a policy rule can never replace a built-in rule, even with a colliding id.
    custom = tuple(
        _custom_rule(s)
        for s in policy.signals
        if s.id.startswith("custom.") and s.id not in builtin_ids
    )
    rules = CATALOGUE + custom
    hits: dict[str, tuple[Rule, set[str]]] = {}
    for f in change.files:
        matched = [r for r in rules if r.match(f, ctx)] or [UNCLASSIFIED]
        for r in matched:
            hits.setdefault(r.id, (r, set()))[1].add(f.path)
    return tuple(
        Signal(
            id=r.id, domain=r.domain, risk=r.risk, files=tuple(sorted(files)), detail=r.description
        )
        for _id, (r, files) in sorted(hits.items())
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
