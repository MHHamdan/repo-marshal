"""Gate inventory (owner: agent "inventory"; roadmap M3, M14; architecture §3).

All gate definitions are read at BASE (git.read_at / git.list_files_at), never from the working
tree, so a change cannot redefine the gates that judge it.

v0.1 sources (precedence high -> low per (scope, kind)):
  1. policy.gates                        ([gates.<id>] in repomarshal.toml at BASE)
  2. package.json "scripts"              lint; typecheck|type-check|tsc;
                                         format:check|prettier:check;
                                         test|test:unit -> unit; test:integration; test:e2e|e2e ->
                                         runtime; test:a11y|a11y. argv = [pm, "run", <script>], pm
                                         from lockfile (pnpm-lock.yaml -> pnpm, yarn.lock -> yarn,
                                         bun.lock/bun.lockb -> bun, else npm). Ignore npm's
                                         placeholder test script ("no test specified").
     pyproject.toml / ruff.toml / mypy.ini / pytest.toml / pytest.ini / setup.cfg:
                                         [tool.ruff] or ruff.toml -> lint ["ruff","check","."];
                                         [tool.mypy] or mypy.ini -> typecheck ["mypy","."];
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
  3. native.native_gates()               always appended.
Scopes: the repository root and its immediate subdirectories that contain a recognised manifest.
Gate id "<scope-name>:<kind>" ("root" for the root scope). cwd = scope directory.

Availability (M3): argv[0] resolved with shutil.which(argv[0], path=path_env); "python" resolves
to sys.executable's basename alternatives ("python3") if "python" is missing. Unresolvable ->
available=False, unavailable_reason="<argv0> not found on PATH".

Definition change detection (M14): for every gate, compute definition_digest from its BASE
definition (the script body + pm for package.json; the relevant tool table / file bytes for
python configs; the [gates.<id>] table for policy gates) and compare with the same definition in
the WORKING TREE (git.read_worktree). If they differ (changed, removed, or the file was deleted),
definition_changed=True. Native gates never change.

M14 also covers files the gate's tool reads or executes, compared as "any change" via the change
set (added, modified, deleted or renamed; both path and old_path count):
  * the tool's definition file set within the gate scope (_is_definition_file below), e.g.
    conftest.py and pytest.toml / pytest.ini for pytest, ruff.toml for ruff, tsconfig*.json /
    .npmrc for package.json scripts;
  * repository files named by the BASE command: tokens of the package.json script body and its
    pre/post hooks (shlex), or the argv of a policy gate, resolved relative to the gate cwd.
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
import json
import os
import posixpath
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
    (GateKind.FORMAT, ("format:check", "prettier:check")),
    (GateKind.LINT, ("lint",)),
    (GateKind.TYPECHECK, ("typecheck", "type-check", "tsc")),
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
)
PYTEST = ("python", "-m", "pytest", "-q")

# Gate families: which definition file set (below) applies to a gate.
PYTEST_GATE = "pytest"
RUFF_GATE = "ruff"
MYPY_GATE = "mypy"
PYRIGHT_GATE = "pyright"
SCRIPT_GATE = "script"
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


def _package_manager(tree: _Tree, scope: str) -> str:
    for name, pm in LOCKFILES:
        if tree.read(scope + name) is not None:
            return pm
    return "npm"


def _script_definition(script: str) -> Callable[[_Tree, str], Any]:
    """What `<pm> run <script>` executes: the script body, its pre/post hooks and the pm."""

    def definition(tree: _Tree, scope: str) -> Any:
        data = tree.parsed(scope + "package.json", "json")
        if data is None:
            return {"package.json": "missing"}
        scripts = data.get("scripts") if data is not _MALFORMED else None
        if not isinstance(scripts, dict):
            return {"package.json": "malformed or no scripts"}
        return {
            "pm": _package_manager(tree, scope),
            "script": scripts.get(script),
            "pre": scripts.get("pre" + script),
            "post": scripts.get("post" + script),
        }

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
    out = []
    for kind, names in SCRIPT_KINDS:
        for name in names:
            body = scripts.get(name)
            if isinstance(body, str) and body.strip() and NPM_PLACEHOLDER not in body:
                source = f"{scope}package.json#scripts.{name}"
                bodies = (body, scripts.get("pre" + name), scripts.get("post" + name))
                words = tuple(w for b in bodies if isinstance(b, str) for w in _shell_words(b))
                definition = _script_definition(name)
                argv = (pm, "run", name)
                out.append(_make(scope, kind, argv, source, definition, SCRIPT_GATE, words))
                break
    return out


def _python_gates(tree: _Tree, scope: str, files: tuple[str, ...]) -> list[_Candidate]:
    if not any(tree.read(scope + m) is not None for m in PYTHON_MANIFESTS):
        return []
    out = []
    ruff = _resolve(RUFF_CHAIN, tree, scope, at_base=True)
    if ruff:
        argv = ("ruff", "check", ".")
        definition = _chain_definition(RUFF_CHAIN)
        out.append(_make(scope, GateKind.LINT, argv, ruff.source, definition, RUFF_GATE))
    typecheckers = (
        (MYPY_CHAIN, ("mypy", "."), MYPY_GATE),
        (PYRIGHT_CHAIN, ("pyright",), PYRIGHT_GATE),
    )
    for chain, argv, family in typecheckers:
        found = _resolve(chain, tree, scope, at_base=True)
        if found:
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


def detect_scopes(files: tuple[str, ...]) -> tuple[str, ...]:
    """The root ("") and immediate subdirectories ("dir/") that hold a recognised manifest."""
    scopes = set()
    for f in files:
        parts = f.split("/")
        if parts[-1] in SCOPE_MANIFESTS and len(parts) <= 2:
            scopes.add("" if len(parts) == 1 else parts[0] + "/")
    return tuple(sorted(scopes))


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
    scopes = detect_scopes(files)
    changed_paths = _changed_paths(change)

    candidates = [_Candidate(g, _policy_gate_definition(g.id)) for g in policy.gates]
    taken = {(g.scope, g.kind) for g in policy.gates}
    for scope in scopes:
        for cand in _package_json_gates(base_tree, scope) + _python_gates(base_tree, scope, files):
            key = (cand.gate.scope, cand.gate.kind)
            if key not in taken:
                taken.add(key)
                candidates.append(cand)

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

    duplicates = sorted(i for i, n in Counter(g.id for g in gates).items() if n > 1)
    if duplicates:
        raise ValueError(f"gate inventory has duplicate gate ids: {', '.join(duplicates)}")
    gates.sort(key=lambda g: (g.scope, KIND_ORDER.index(g.kind), g.id))
    return Inventory(gates=tuple(gates), scopes=scopes)
