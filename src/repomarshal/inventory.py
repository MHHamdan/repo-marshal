"""Gate inventory (owner: agent "inventory"; roadmap M3, M14; architecture §3).

All gate definitions are read at BASE (git.read_at / git.list_files_at), never from the working
tree, so a change cannot redefine the gates that judge it.

v0.1 sources (precedence high -> low per (scope, kind)):
  1. policy.gates                        ([gates.<id>] in repomarshal.toml at BASE)
  2. package.json "scripts"              lint|lint:check|test:lint;
                                         typecheck|type-check|tsc|test:types|test:typecheck;
                                         format:check|prettier:check|test:format;
                                         test|test:unit -> unit; test:integration; test:e2e|e2e ->
                                         runtime; test:a11y|a11y. argv = [pm, "run", <script>].
                                         Ignore npm's placeholder test script ("no test
                                         specified"). Gates must never mutate the tree: the first
                                         candidate (in that order) whose body and pre/post hooks
                                         carry no mutating flag (MUTATING_FLAGS: --write, --fix,
                                         -w, ...) wins; only when every candidate of a kind
                                         mutates is the first one used (the run will then most
                                         likely end UNKNOWN, tree modified, which is honest), and
                                         its source says "(mutating; no check variant)".
                                         pm: the "packageManager" field ("pnpm@9.1.0" -> pnpm) of
                                         the scope's package.json, else of the root package.json
                                         (the workspace root); else the lockfile in the scope,
                                         else at the root (pnpm-lock.yaml -> pnpm, yarn.lock ->
                                         yarn, bun.lock/bun.lockb -> bun, package-lock.json /
                                         npm-shrinkwrap.json -> npm); else npm.
     pyproject.toml / ruff.toml / mypy.ini / pytest.toml / pytest.ini / setup.cfg:
                                         [tool.ruff] or ruff.toml -> lint
                                         ["ruff","check","--no-fix","."] (never apply a
                                         configured `fix = true`);
                                         [tool.mypy] or mypy.ini -> typecheck ["mypy","."], or
                                         ["mypy"] when the config sets files, packages or
                                         modules (a path argument would override them);
                                         [tool.pyright] -> typecheck ["pyright"];
                                         pytest.toml, .pytest.toml, pytest.ini,
                                         .pytest.ini, [tool.pytest.ini_options],
                                         [tool.pytest], tox.ini [pytest],
                                         setup.cfg [tool:pytest], or
                                         tests/test_*.py -> unit
                                         ["python","-m","pytest","-q"];
                                         if tests/integration/ exists: unit adds
                                         "--ignore=tests/integration" and an
                                         integration gate ["python","-m","pytest","-q",
                                         "tests/integration"].
     Makefile (architecture §3.2)        targets format-check|fmt-check -> format, lint|check ->
                                         lint, typecheck, test -> unit; argv ["make", <target>]
                                         (same non-mutating preference as scripts). Within
                                         precedence 2 the order is package.json, python
                                         configs, Makefile.
  3. native.native_gates()               always appended.
Scopes: the repository root and its immediate subdirectories that contain a recognised manifest,
plus the workspace packages of the root: package.json "workspaces" (an array, or an object with
"packages") and pnpm-workspace.yaml (a simple `packages:` list, read line by line). Globs
("packages/*", "apps/**") expand at BASE to directories holding a package.json; "!" patterns
exclude. A subdirectory whose only manifests are pytest config files (pytest.ini, pytest.toml,
...) is not a scope when the root scope already runs pytest: it configures the root's suite
(e.g. tests/pytest.ini), and a scope of its own would require the suite twice.
Gate id "<scope-path>:<kind>" ("root" for the root scope, "packages/zod:unit" for a workspace
package). cwd = scope directory.

Availability (M3): argv[0] resolved with shutil.which(argv[0], path=path_env); "python" resolves
to sys.executable's basename alternatives ("python3") if "python" is missing. Unresolvable ->
available=False, unavailable_reason="<argv0> not found on PATH".

Definition change detection (M14): for every gate, compute definition_digest from its BASE
definition (the selected script, its body, hooks and pm for package.json; the relevant tool
table / file bytes for python configs; the Makefile bytes and selected target for make gates; the
[gates.<id>] table for policy gates) and compare with the same definition in the WORKING TREE
(git.read_worktree). If they differ (changed, removed, or the file was deleted),
definition_changed=True. Native gates never change. Non-policy definitions also include the
winning source of their (scope, kind) in that tree, so a source added or changed in the working
tree that would win over (or replace) the BASE source is a definition change too.

M14 also covers files the gate's tool reads or executes, compared as "any change" via the change
set (added, modified, deleted or renamed; both path and old_path count):
  * the tool's definition file set within the gate scope (_is_definition_file below), e.g.
    conftest.py and pytest.toml / pytest.ini for pytest, ruff.toml for ruff, tsconfig*.json /
    .npmrc for package.json scripts, Makefile / makefile / GNUmakefile and *.mk for make gates;
  * repository files named by the BASE command: tokens of the package.json script body and its
    pre/post hooks (shlex), or the argv of a policy gate, resolved relative to the gate cwd; a
    command that runs `make` also reads the Makefile in its cwd.
package.json, pyproject.toml and repomarshal.toml stay compared semantically (above), and paths
under .repomarshal/ or node_modules/ never count. The changed paths are appended to Gate.source
("...; definition changed: <paths>").

Gate ids are unique: a subdirectory scope named "root" (any case) is "root/:<kind>" so it cannot
collide with the repository root scope "root:<kind>"; build_inventory raises ValueError on any
duplicate id.
"""

from __future__ import annotations

import configparser
import dataclasses
import fnmatch
import json
import os
import posixpath
import re
import shlex
import shutil
import sys
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repomarshal import git
from repomarshal.model import (
    DEFAULT_COST,
    KIND_ORDER,
    ChangeSet,
    Gate,
    GateKind,
    Inventory,
    Policy,
    sha256_bytes,
    sha256_json,
)
from repomarshal.native import native_gates
from repomarshal.policy import POLICY_FILE

PYTHON_MANIFESTS = (
    "pyproject.toml",
    "setup.cfg",
    "pytest.toml",
    ".pytest.toml",
    "pytest.ini",
    ".pytest.ini",
    "tox.ini",
    "mypy.ini",
    ".mypy.ini",
    "ruff.toml",
    ".ruff.toml",
)
SCOPE_MANIFESTS = frozenset({"package.json", "Makefile", *PYTHON_MANIFESTS})

# package.json script names per kind, in preference order.
SCRIPT_KINDS: tuple[tuple[GateKind, tuple[str, ...]], ...] = (
    (GateKind.FORMAT, ("format:check", "prettier:check", "test:format")),
    (GateKind.LINT, ("lint", "lint:check", "test:lint")),
    (GateKind.TYPECHECK, ("typecheck", "type-check", "tsc", "test:types", "test:typecheck")),
    (GateKind.UNIT, ("test", "test:unit")),
    (GateKind.INTEGRATION, ("test:integration",)),
    (GateKind.RUNTIME, ("test:e2e", "e2e")),
    (GateKind.A11Y, ("test:a11y", "a11y")),
)
NPM_PLACEHOLDER = "no test specified"
LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
)
# A word of a command that makes it rewrite files (biome/eslint/prettier/ruff/black-style flags).
MUTATING_FLAGS = frozenset({"--write", "--fix", "-w", "--apply", "--apply-unsafe"})
MUTATING_PREFIXES = ("--write=", "--fix=")
_PM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Makefile targets per kind, in preference order (architecture §3.2).
MAKE_TARGETS: tuple[tuple[GateKind, tuple[str, ...]], ...] = (
    (GateKind.FORMAT, ("format-check", "fmt-check")),
    (GateKind.LINT, ("lint", "check")),
    (GateKind.TYPECHECK, ("typecheck",)),
    (GateKind.UNIT, ("test",)),
)
# GNU make's own lookup order.
MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
_MAKE_RULE = re.compile(r"^([A-Za-z0-9_./%-]+(?:[ \t]+[A-Za-z0-9_./%-]+)*)[ \t]*::?(?!=)(.*)$")
# A subdirectory holding only these is the root suite's configuration, not a scope of its own.
PYTEST_ONLY_MANIFESTS = frozenset({"pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"})
PYTEST = ("python", "-m", "pytest", "-q")

# Gate families: which definition file set (below) applies to a gate.
PYTEST_GATE = "pytest"
RUFF_GATE = "ruff"
MYPY_GATE = "mypy"
PYRIGHT_GATE = "pyright"
SCRIPT_GATE = "script"
MAKE_GATE = "make"
POLICY_GATE = "policy"

PYTEST_FILES = frozenset(
    {
        "conftest.py",
        "pytest.toml",
        ".pytest.toml",
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
    }
)
RUFF_FILES = frozenset({"ruff.toml", ".ruff.toml"})
MYPY_FILES = frozenset({"mypy.ini", ".mypy.ini", "setup.cfg"})
SCRIPT_CONFIG_PREFIXES = (
    "eslint.config.",
    ".eslintrc",
    ".babelrc",
    "jest.config.",
    "vitest.config.",
    "playwright.config.",
    "babel.config.",
    "vite.config.",
)
# Compared semantically by the digest; an edit to them alone (e.g. a dependency bump) is not a
# definition change even when a command names them.
SEMANTIC_FILES = frozenset({"package.json", "pyproject.toml", POLICY_FILE})
INTEGRATION_DIR = "tests/integration"

_MALFORMED = object()


# --------------------------------------------------------------------------- trees


class _Tree:
    """Read access to one side of the comparison: BASE (git) or the working tree."""

    def __init__(self, repo: Path, base: str | None, worktree: bool):
        self.repo = repo
        self.base = base
        self.worktree = worktree
        self._bytes: dict[str, bytes | None] = {}
        self._parsed: dict[tuple[str, str], Any] = {}

    def read(self, path: str) -> bytes | None:
        if path not in self._bytes:
            if self.worktree:
                self._bytes[path] = git.read_worktree(self.repo, path)
            else:
                self._bytes[path] = git.read_at(self.repo, self.base, path)
        return self._bytes[path]

    def parsed(self, path: str, fmt: str) -> Any:
        """Parsed content of `path` ("toml", "json" or "ini"): None if missing, _MALFORMED if it
        cannot be parsed."""
        key = (path, fmt)
        if key not in self._parsed:
            self._parsed[key] = _parse(self.read(path), fmt)
        return self._parsed[key]


def _parse(raw: bytes | None, fmt: str) -> Any:
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8")
        if fmt == "toml":
            return tomllib.loads(text)
        if fmt == "json":
            data = json.loads(text)
            return data if isinstance(data, dict) else _MALFORMED
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_string(text)
        return {name: dict(cp.items(name, raw=True)) for name in cp.sections()}
    except (UnicodeDecodeError, ValueError, configparser.Error):
        # tomllib.TOMLDecodeError and json.JSONDecodeError are ValueErrors.
        return _MALFORMED


# --------------------------------------------------------------------------- definitions
#
# A "lookup" finds one candidate source of a tool's configuration in a tree and returns its
# definition (JSON-ready) or None when absent. A chain of lookups mirrors the tool's own config
# precedence, so the same chain resolves the BASE and the working-tree definition.


@dataclass(frozen=True)
class _Found:
    source: str  # repo-relative, e.g. "backend/pyproject.toml#tool.ruff"
    definition: Any
    malformed: bool = False


Lookup = Callable[[_Tree, str], "_Found | None"]


def _file_lookup(name: str) -> Lookup:
    def lookup(tree: _Tree, scope: str) -> _Found | None:
        raw = tree.read(scope + name)
        if raw is None:
            return None
        return _Found(scope + name, {"file": scope + name, "sha256": sha256_bytes(raw)})

    return lookup


def _table_lookup(name: str, fmt: str, keys: tuple[str, ...], label: str) -> Lookup:
    def lookup(tree: _Tree, scope: str) -> _Found | None:
        path = scope + name
        data = tree.parsed(path, fmt)
        source = f"{path}#{label}"
        if data is None:
            return None
        if data is _MALFORMED:
            return _Found(source, {"file": path, "malformed": True}, malformed=True)
        node: Any = data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return _Found(source, {"file": path, "table": node})

    return lookup


RUFF_CHAIN = (
    _file_lookup(".ruff.toml"),
    _file_lookup("ruff.toml"),
    _table_lookup("pyproject.toml", "toml", ("tool", "ruff"), "tool.ruff"),
)
MYPY_CHAIN = (
    _file_lookup("mypy.ini"),
    _file_lookup(".mypy.ini"),
    _table_lookup("pyproject.toml", "toml", ("tool", "mypy"), "tool.mypy"),
    _table_lookup("setup.cfg", "ini", ("mypy",), "mypy"),
)
PYRIGHT_CHAIN = (
    _file_lookup("pyrightconfig.json"),
    _table_lookup("pyproject.toml", "toml", ("tool", "pyright"), "tool.pyright"),
)
# pytest's own precedence within a directory (pytest >= 9 reads pytest.toml / .pytest.toml
# first, even when empty).
PYTEST_CHAIN = (
    _file_lookup("pytest.toml"),
    _file_lookup(".pytest.toml"),
    _file_lookup("pytest.ini"),
    _file_lookup(".pytest.ini"),
    _table_lookup(
        "pyproject.toml", "toml", ("tool", "pytest", "ini_options"), "tool.pytest.ini_options"
    ),
    _table_lookup("pyproject.toml", "toml", ("tool", "pytest"), "tool.pytest"),
    _table_lookup("tox.ini", "ini", ("pytest",), "pytest"),
    _table_lookup("setup.cfg", "ini", ("tool:pytest",), "tool:pytest"),
)


def _resolve(chain: tuple[Lookup, ...], tree: _Tree, scope: str, at_base: bool) -> _Found | None:
    """First source in the chain. At BASE malformed sources are skipped (never a gate); in the
    working tree a malformed source is returned so that it counts as a definition change."""
    for lookup in chain:
        found = lookup(tree, scope)
        if found is None or (found.malformed and at_base):
            continue
        return found
    return None


def _chain_definition(chain: tuple[Lookup, ...]) -> Callable[[_Tree, str], Any]:
    def definition(tree: _Tree, scope: str) -> Any:
        found = _resolve(chain, tree, scope, at_base=False)
        return None if found is None else found.definition

    return definition


def _declared_package_manager(tree: _Tree, scope: str) -> str | None:
    """The tool named by package.json "packageManager" ("pnpm@9.1.0+sha..." -> "pnpm")."""
    data = tree.parsed(scope + "package.json", "json")
    if not isinstance(data, dict):
        return None
    field = data.get("packageManager")
    if not isinstance(field, str):
        return None
    name = field.strip().split("@", 1)[0]
    return name if _PM_NAME.fullmatch(name) else None


def _package_manager(tree: _Tree, scope: str) -> str:
    """packageManager (scope, then the workspace root) wins over lockfiles (scope, then root)."""
    places = (scope, "") if scope else ("",)
    for place in places:
        declared = _declared_package_manager(tree, place)
        if declared:
            return declared
    for place in places:
        for name, pm in LOCKFILES:
            if tree.read(place + name) is not None:
                return pm
    return "npm"


def _mutates(commands: tuple[Any, ...]) -> bool:
    """Whether any of the shell commands carries a flag that rewrites files."""
    for command in commands:
        if not isinstance(command, str):
            continue
        for word in _shell_words(command):
            if word in MUTATING_FLAGS or word.startswith(MUTATING_PREFIXES):
                return True
    return False


def _pick(names: tuple[str, ...], bodies: Callable[[str], tuple[Any, ...] | None]) -> str | None:
    """The first defined name whose commands do not mutate; else the first defined name (a gate
    whose only form mutates is still a gate, see the module doc). None if no name is defined."""
    defined = [n for n in names if bodies(n) is not None]
    for name in defined:
        if not _mutates(bodies(name) or ()):
            return name
    return defined[0] if defined else None


def _script_bodies(scripts: dict) -> Callable[[str], tuple[Any, ...] | None]:
    def bodies(name: str) -> tuple[Any, ...] | None:
        body = scripts.get(name)
        if not (isinstance(body, str) and body.strip() and NPM_PLACEHOLDER not in body):
            return None
        return (body, scripts.get("pre" + name), scripts.get("post" + name))

    return bodies


def _script_definition(kind: GateKind, names: tuple[str, ...]) -> Callable[[_Tree, str], Any]:
    """What `<pm> run <script>` executes: the script the kind selects in this tree, its body, its
    pre/post hooks and the pm. A different selection (e.g. a check variant added or a mutating
    flag dropped) is a definition change."""

    def definition(tree: _Tree, scope: str) -> Any:
        data = tree.parsed(scope + "package.json", "json")
        if data is None:
            return {"package.json": "missing"}
        scripts = data.get("scripts") if data is not _MALFORMED else None
        if not isinstance(scripts, dict):
            return {"package.json": "malformed or no scripts"}
        script = _pick(names, _script_bodies(scripts))
        if script is None:
            return {"kind": kind.value, "script": None}
        return {
            "pm": _package_manager(tree, scope),
            "name": script,
            "script": scripts.get(script),
            "pre": scripts.get("pre" + script),
            "post": scripts.get("post" + script),
        }

    return definition


def _makefile(tree: _Tree, scope: str) -> tuple[str, bytes] | None:
    """The makefile GNU make would read in the scope, with its BASE bytes."""
    for name in MAKEFILES:
        raw = tree.read(scope + name)
        if raw is not None:
            return scope + name, raw
    return None


def _make_rules(text: str) -> dict[str, list[str]]:
    """Explicit targets of a makefile and their recipe lines (tab-indented or after `;`)."""
    rules: dict[str, list[str]] = {}
    current: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("\t"):
            for recipe in current:
                recipe.append(line.strip())
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        current = []
        m = _MAKE_RULE.match(line)
        if m is None:
            continue
        inline = m.group(2).split(";", 1)
        for target in m.group(1).split():
            recipe = rules.setdefault(target, [])
            if len(inline) == 2 and inline[1].strip():
                recipe.append(inline[1].strip())
            current.append(recipe)
    return rules


def _make_definition(names: tuple[str, ...]) -> Callable[[_Tree, str], Any]:
    """The whole makefile (recipes may use any variable or prerequisite) and the target the kind
    selects in this tree."""

    def definition(tree: _Tree, scope: str) -> Any:
        found = _makefile(tree, scope)
        if found is None:
            return {"makefile": "missing"}
        path, raw = found
        try:
            rules = _make_rules(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return {"file": path, "malformed": True}
        target = _pick(names, lambda n: tuple(rules[n]) if n in rules else None)
        return {"file": path, "sha256": sha256_bytes(raw), "target": target}

    return definition


def _policy_gate_definition(gate_id: str) -> Callable[[_Tree, str], Any]:
    def definition(tree: _Tree, scope: str) -> Any:
        data = tree.parsed(POLICY_FILE, "toml")
        if data is None or data is _MALFORMED:
            return None
        gates = data.get("gates")
        return gates.get(gate_id) if isinstance(gates, dict) else None

    return definition


# --------------------------------------------------------------------------- candidates


@dataclass(frozen=True)
class _Candidate:
    gate: Gate
    definition: Callable[[_Tree, str], Any]
    family: str = POLICY_GATE
    # BASE command words that may name repository files the gate executes.
    words: tuple[str, ...] = ()


def _jsonable(obj: Any) -> Any:
    """TOML may hold dates and times; make every definition JSON-serialisable."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return f"{type(obj).__name__}:{obj.isoformat() if hasattr(obj, 'isoformat') else obj}"


def _digest(definition: Any) -> str:
    return sha256_json(_jsonable(definition))


def _scope_name(scope: str) -> str:
    """The id prefix: "root" for the repository root, "root/" for a subdirectory named root."""
    if not scope:
        return "root"
    name = scope[:-1]
    return name + "/" if name.lower() == "root" else name


def _make(
    scope: str,
    kind: GateKind,
    argv: tuple[str, ...],
    source: str,
    definition,
    family: str,
    words: tuple[str, ...] = (),
) -> _Candidate:
    gate = Gate(
        id=f"{_scope_name(scope)}:{kind.value}",
        kind=kind,
        argv=argv,
        cwd=scope[:-1],
        scope=scope,
        cost=DEFAULT_COST[kind],
        source=source,
    )
    return _Candidate(gate, definition, family, words)


def _package_json_gates(tree: _Tree, scope: str) -> list[_Candidate]:
    data = tree.parsed(scope + "package.json", "json")
    if data is None or data is _MALFORMED:
        return []
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return []
    pm = _package_manager(tree, scope)
    bodies_of = _script_bodies(scripts)
    out = []
    for kind, names in SCRIPT_KINDS:
        name = _pick(names, bodies_of)
        if name is None:
            continue
        bodies = bodies_of(name) or ()
        source = f"{scope}package.json#scripts.{name}"
        if _mutates(bodies):
            source += " (mutating; no check variant)"
        words = tuple(w for b in bodies if isinstance(b, str) for w in _shell_words(b))
        definition = _script_definition(kind, names)
        argv = (pm, "run", name)
        out.append(_make(scope, kind, argv, source, definition, SCRIPT_GATE, words))
    return out


def _makefile_gates(tree: _Tree, scope: str) -> list[_Candidate]:
    found = _makefile(tree, scope)
    if found is None:
        return []
    path, raw = found
    try:
        rules = _make_rules(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return []
    out = []
    for kind, names in MAKE_TARGETS:
        target = _pick(names, lambda n: tuple(rules[n]) if n in rules else None)
        if target is None:
            continue
        source = f"{path}#{target}"
        if _mutates(tuple(rules[target])):
            source += " (mutating; no check variant)"
        argv = ("make", target)
        out.append(_make(scope, kind, argv, source, _make_definition(names), MAKE_GATE, argv))
    return out


def _mypy_argv(tree: _Tree, found: _Found) -> tuple[str, ...]:
    """`mypy .` overrides configured files/packages/modules; pass no path when they are set."""
    definition = found.definition
    if isinstance(definition, dict) and "table" in definition:
        table = definition["table"]
    else:
        sections = tree.parsed(found.source, "ini")
        table = sections.get("mypy") if isinstance(sections, dict) else None
    if isinstance(table, dict) and any(table.get(k) for k in ("files", "packages", "modules")):
        return ("mypy",)
    return ("mypy", ".")


def _python_gates(tree: _Tree, scope: str, files: tuple[str, ...]) -> list[_Candidate]:
    if not any(tree.read(scope + m) is not None for m in PYTHON_MANIFESTS):
        return []
    out = []
    ruff = _resolve(RUFF_CHAIN, tree, scope, at_base=True)
    if ruff:
        # --no-fix: a configured `fix = true` would rewrite the tree (gates never mutate it).
        argv = ("ruff", "check", "--no-fix", ".")
        definition = _chain_definition(RUFF_CHAIN)
        out.append(_make(scope, GateKind.LINT, argv, ruff.source, definition, RUFF_GATE))
    typecheckers = (
        (MYPY_CHAIN, ("mypy", "."), MYPY_GATE),
        (PYRIGHT_CHAIN, ("pyright",), PYRIGHT_GATE),
    )
    for chain, argv, family in typecheckers:
        found = _resolve(chain, tree, scope, at_base=True)
        if found:
            if family == MYPY_GATE:
                argv = _mypy_argv(tree, found)
            definition = _chain_definition(chain)
            out.append(_make(scope, GateKind.TYPECHECK, argv, found.source, definition, family))
            break

    tests = scope + "tests/"
    integration = scope + INTEGRATION_DIR + "/"
    pytest_cfg = _resolve(PYTEST_CHAIN, tree, scope, at_base=True)
    has_unit_tests = any(
        f.startswith(tests)
        and not f.startswith(integration)
        and f.rsplit("/", 1)[-1].startswith("test_")
        and f.endswith(".py")
        for f in files
    )
    if pytest_cfg is None and not has_unit_tests:
        return out
    has_integration = any(f.startswith(integration) for f in files)
    unit_source = pytest_cfg.source if pytest_cfg else tests
    definition = _chain_definition(PYTEST_CHAIN)
    unit_argv = PYTEST + (f"--ignore={INTEGRATION_DIR}",) if has_integration else PYTEST
    out.append(_make(scope, GateKind.UNIT, unit_argv, unit_source, definition, PYTEST_GATE))
    if has_integration:
        argv = PYTEST + (INTEGRATION_DIR,)
        out.append(_make(scope, GateKind.INTEGRATION, argv, integration, definition, PYTEST_GATE))
    return out


def _scope_candidates(tree: _Tree, scope: str, files: tuple[str, ...]) -> list[_Candidate]:
    """The winning candidate per kind of one scope, in source precedence order."""
    out: list[_Candidate] = []
    seen: set[GateKind] = set()
    sources = (
        _package_json_gates(tree, scope)
        + _python_gates(tree, scope, files)
        + _makefile_gates(tree, scope)
    )
    for cand in sources:
        if cand.gate.kind not in seen:
            seen.add(cand.gate.kind)
            out.append(cand)
    return out


def _winner_source(tree: _Tree, scope: str, kind: GateKind, files: tuple[str, ...]) -> str | None:
    """The source that would define (scope, kind) if the inventory were read from `tree`."""
    key = ("winners", scope)
    if key not in tree._parsed:
        cands = _scope_candidates(tree, scope, files)
        tree._parsed[key] = {c.gate.kind: c.gate.source for c in cands}
    return tree._parsed[key].get(kind)


def _with_winner(cand: _Candidate, files: tuple[str, ...]) -> _Candidate:
    """M14: the definition also names the winning source of the gate's (scope, kind), so a source
    added or changed in the working tree that takes over the gate is a definition change."""
    own, kind = cand.definition, cand.gate.kind

    def definition(tree: _Tree, scope: str) -> Any:
        return {"definition": own(tree, scope), "source": _winner_source(tree, scope, kind, files)}

    return dataclasses.replace(cand, definition=definition)


def detect_scopes(files: tuple[str, ...], tree: _Tree | None = None) -> tuple[str, ...]:
    """The root ("") and immediate subdirectories ("dir/") that hold a recognised manifest; with
    the BASE tree also the workspace packages, and without pytest-only subdirectories of a root
    that runs pytest (see the module doc)."""
    manifests: dict[str, set[str]] = {}
    for f in files:
        parts = f.split("/")
        if parts[-1] in SCOPE_MANIFESTS and len(parts) <= 2:
            scope = "" if len(parts) == 1 else parts[0] + "/"
            manifests.setdefault(scope, set()).add(parts[-1])
    scopes = set(manifests)
    if tree is not None:
        if "" in scopes and any(
            c.gate.kind is GateKind.UNIT for c in _python_gates(tree, "", files)
        ):
            scopes -= {s for s, names in manifests.items() if s and names <= PYTEST_ONLY_MANIFESTS}
        scopes |= set(_workspace_scopes(tree, files))
    return tuple(sorted(scopes))


def _workspace_globs(tree: _Tree) -> list[str]:
    """Workspace package globs of the repository root (package.json, pnpm-workspace.yaml)."""
    globs: list[str] = []
    data = tree.parsed("package.json", "json")
    if isinstance(data, dict):
        ws = data.get("workspaces")
        if isinstance(ws, dict):
            ws = ws.get("packages")
        if isinstance(ws, list):
            globs += [g for g in ws if isinstance(g, str)]
    raw = tree.read("pnpm-workspace.yaml")
    if raw is not None:
        globs += _pnpm_workspace_packages(raw.decode("utf-8", errors="replace"))
    return globs


def _yaml_scalar(text: str) -> str:
    text = text.split(" #", 1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _pnpm_workspace_packages(text: str) -> list[str]:
    """The `packages:` list of pnpm-workspace.yaml: block items (`  - "pkgs/*"`) or a flow list
    on the same line (`packages: [a, b]`). No YAML dependency; anything else is ignored."""
    out: list[str] = []
    in_packages = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace() and not stripped.startswith("-"):
            key, _, rest = stripped.partition(":")
            in_packages = key.strip() == "packages"
            rest = rest.split(" #", 1)[0].strip()
            if in_packages and rest.startswith("[") and rest.endswith("]"):
                out += [_yaml_scalar(i) for i in rest[1:-1].split(",") if i.strip()]
                in_packages = False
            continue
        if in_packages and stripped.startswith("-"):
            item = _yaml_scalar(stripped[1:])
            if item:
                out.append(item)
    return out


def _glob_match(pattern: list[str], parts: list[str]) -> bool:
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return any(_glob_match(pattern[1:], parts[i:]) for i in range(len(parts) + 1))
    return (
        bool(parts)
        and fnmatch.fnmatchcase(parts[0], pattern[0])
        and _glob_match(pattern[1:], parts[1:])
    )


def _workspace_scopes(tree: _Tree, files: tuple[str, ...]) -> list[str]:
    """Directories (at BASE) holding a package.json that a workspace glob matches."""
    include, exclude = [], []
    for glob in _workspace_globs(tree):
        negated = glob.startswith("!")
        glob = glob[1:] if negated else glob
        while glob.startswith("./"):
            glob = glob[2:]
        glob = glob.strip("/")
        if glob.endswith("/package.json"):
            glob = glob[: -len("/package.json")]
        if not glob or glob.startswith("../") or glob == "..":
            continue
        (exclude if negated else include).append(glob.split("/"))
    if not include:
        return []
    out = []
    for f in files:
        directory, _, name = f.rpartition("/")
        if name != "package.json" or not directory or _never_counts(f):
            continue
        parts = directory.split("/")
        if any(_glob_match(p, parts) for p in include) and not any(
            _glob_match(p, parts) for p in exclude
        ):
            out.append(directory + "/")
    return out


# --------------------------------------------------------------------------- definition files


def _shell_words(command: str) -> list[str]:
    """Words of a shell command (operators split off); whitespace split if it does not lex."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()


def _never_counts(path: str) -> bool:
    parts = path.split("/")
    return parts[0] == ".repomarshal" or "node_modules" in parts


def _changed_paths(change: ChangeSet | None) -> frozenset[str]:
    """Every path the change set touches, renames counted at both ends."""
    if change is None:
        return frozenset()
    paths = set()
    for f in change.files:
        paths.add(f.path)
        if f.old_path:
            paths.add(f.old_path)
    return frozenset(p for p in paths if not _never_counts(p))


def _is_definition_file(family: str, rel: str) -> bool:
    """Whether `rel` (relative to the gate scope) is in the family's definition file set."""
    base = rel.rsplit("/", 1)[-1]
    top = "/" not in rel
    if family == PYTEST_GATE:
        # pytest picks its ini file from the ancestors of its path arguments: any depth.
        return base in PYTEST_FILES
    if family == RUFF_GATE:
        return base in RUFF_FILES
    if family == MYPY_GATE:
        return top and base in MYPY_FILES
    if family == PYRIGHT_GATE:
        return top and base == "pyrightconfig.json"
    if family == MAKE_GATE:
        return (top and base in MAKEFILES) or base.endswith(".mk")
    if family == SCRIPT_GATE:
        if base == ".npmrc":
            return top
        if base.startswith("tsconfig") and base.endswith(".json"):
            return True
        return base.startswith(SCRIPT_CONFIG_PREFIXES)
    return False


def _executed_file(cwd: str, word: str) -> str | None:
    """Repo-relative path that a command word names relative to cwd, if it stays in the repo."""
    if not word or word.startswith("/"):
        return None
    path = posixpath.normpath(posixpath.join(cwd, word))
    if path in (".", "..") or path.startswith("../"):
        return None
    if path.rsplit("/", 1)[-1] in SEMANTIC_FILES:
        return None
    return path


def _definition_files_changed(cand: _Candidate, changed: frozenset[str]) -> list[str]:
    """Changed paths that belong to the gate's definition beyond its digest (see module doc)."""
    if not changed:
        return []
    gate = cand.gate
    hits = set()
    for path in changed:
        if path.startswith(gate.scope):
            rel = path[len(gate.scope) :]
            if _is_definition_file(cand.family, rel):
                hits.add(path)
    if cand.family == SCRIPT_GATE and ".npmrc" in changed:
        hits.add(".npmrc")  # the repository root .npmrc applies to every package
    words = cand.words if cand.family != POLICY_GATE else gate.argv
    if cand.family != MAKE_GATE and "make" in words:
        # The command runs make, which reads the makefile of its cwd.
        for name in MAKEFILES:
            path = posixpath.join(gate.cwd, name) if gate.cwd else name
            if path in changed:
                hits.add(path)
    for word in words:
        candidates = [word]
        if "=" in word:
            candidates.append(word.split("=", 1)[1])
        for w in candidates:
            path = _executed_file(gate.cwd, w)
            if path is not None and path in changed:
                hits.add(path)
    return sorted(hits)


def _with_reason(gate: Gate, paths: list[str]) -> Gate:
    shown = ", ".join(paths[:5])
    if len(paths) > 5:
        shown += f" (+{len(paths) - 5} more)"
    return dataclasses.replace(
        gate, definition_changed=True, source=f"{gate.source}; definition changed: {shown}"
    )


# --------------------------------------------------------------------------- availability


def _availability(gate: Gate, repo: Path, path_env: str | None) -> Gate:
    if gate.provider != "command" or not gate.argv:
        return gate
    argv0 = gate.argv[0]
    if "/" in argv0:
        target = Path(repo) / gate.cwd / argv0
        if target.is_file() and os.access(target, os.X_OK):
            return gate
    else:
        names = [argv0]
        if argv0 == "python":
            names += ["python3", os.path.basename(sys.executable)]
        for name in names:
            if name and shutil.which(name, path=path_env):
                if name == argv0:
                    return gate
                return dataclasses.replace(gate, argv=(name, *gate.argv[1:]))
    return dataclasses.replace(
        gate, available=False, unavailable_reason=f"{argv0} not found on PATH"
    )


# --------------------------------------------------------------------------- inventory


def build_inventory(
    repo: Path,
    base: str | None,
    policy: Policy,
    path_env: str | None = None,
    change: ChangeSet | None = None,
) -> Inventory:
    """Gate inventory at BASE with availability and definition-change flags (see module doc).

    Gates are sorted by (scope, kind order in model.KIND_ORDER, id). With base None (no commits),
    only native gates and policy gates exist.
    """
    base_tree = _Tree(repo, base, worktree=False)
    work_tree = _Tree(repo, base, worktree=True)
    files = git.list_files_at(repo, base)
    scopes = detect_scopes(files, base_tree)
    changed_paths = _changed_paths(change)

    candidates = [_Candidate(g, _policy_gate_definition(g.id)) for g in policy.gates]
    taken = {(g.scope, g.kind) for g in policy.gates}
    for scope in scopes:
        for cand in _scope_candidates(base_tree, scope, files):
            key = (cand.gate.scope, cand.gate.kind)
            if key not in taken:
                taken.add(key)
                candidates.append(_with_winner(cand, files))

    gates: list[Gate] = []
    for cand in candidates:
        gate = cand.gate
        if gate.provider == "native":
            gates.append(gate)
            continue
        digest = gate.definition_digest or _digest(cand.definition(base_tree, gate.scope))
        changed = _digest(cand.definition(work_tree, gate.scope)) != digest
        gate = dataclasses.replace(gate, definition_digest=digest, definition_changed=changed)
        extra = _definition_files_changed(cand, changed_paths)
        if extra:
            gate = _with_reason(gate, extra)
        gates.append(_availability(gate, repo, path_env))
    gates.extend(native_gates())
    from repomarshal import providers  # v0.3: named adapters contribute gates read at BASE

    gates.extend(providers.discover(repo, base, change, path_env))
    # A `playwright test` script gate duplicates the Playwright provider in its scope. Drop it, but
    # keep it when its definition changed, so that edit still surfaces as UNKNOWN.
    dropped = providers.superseded(repo, base, gates)
    gates = [g for g in gates if g.id not in dropped or g.definition_changed]

    duplicates = sorted(i for i, n in Counter(g.id for g in gates).items() if n > 1)
    if duplicates:
        raise ValueError(f"gate inventory has duplicate gate ids: {', '.join(duplicates)}")
    gates.sort(key=lambda g: (g.scope, KIND_ORDER.index(g.kind), g.id))
    return Inventory(gates=tuple(gates), scopes=scopes)
