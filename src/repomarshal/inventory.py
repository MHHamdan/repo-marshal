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
     pyproject.toml / ruff.toml / mypy.ini / pytest.ini / setup.cfg:
                                         [tool.ruff] or ruff.toml -> lint ["ruff","check","."];
                                         [tool.mypy] or mypy.ini -> typecheck ["mypy","."];
                                         [tool.pyright] -> typecheck ["pyright"];
                                         [tool.pytest.ini_options], pytest.ini, or
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
"""

from __future__ import annotations

import configparser
import dataclasses
import json
import os
import shutil
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repomarshal import git
from repomarshal.model import (
    DEFAULT_COST,
    KIND_ORDER,
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
    "pytest.ini",
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
PYTEST_CHAIN = (
    _file_lookup("pytest.ini"),
    _table_lookup(
        "pyproject.toml", "toml", ("tool", "pytest", "ini_options"), "tool.pytest.ini_options"
    ),
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
    return scope[:-1] if scope else "root"


def _make(scope: str, kind: GateKind, argv: tuple[str, ...], source: str, definition) -> _Candidate:
    gate = Gate(
        id=f"{_scope_name(scope)}:{kind.value}",
        kind=kind,
        argv=argv,
        cwd=scope[:-1],
        scope=scope,
        cost=DEFAULT_COST[kind],
        source=source,
    )
    return _Candidate(gate, definition)


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
                out.append(_make(scope, kind, (pm, "run", name), source, _script_definition(name)))
                break
    return out


def _python_gates(tree: _Tree, scope: str, files: tuple[str, ...]) -> list[_Candidate]:
    if not any(tree.read(scope + m) is not None for m in PYTHON_MANIFESTS):
        return []
    out = []
    ruff = _resolve(RUFF_CHAIN, tree, scope, at_base=True)
    if ruff:
        argv = ("ruff", "check", ".")
        out.append(_make(scope, GateKind.LINT, argv, ruff.source, _chain_definition(RUFF_CHAIN)))
    for chain, argv in ((MYPY_CHAIN, ("mypy", ".")), (PYRIGHT_CHAIN, ("pyright",))):
        found = _resolve(chain, tree, scope, at_base=True)
        if found:
            out.append(
                _make(scope, GateKind.TYPECHECK, argv, found.source, _chain_definition(chain))
            )
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
    out.append(_make(scope, GateKind.UNIT, unit_argv, unit_source, definition))
    if has_integration:
        argv = PYTEST + (INTEGRATION_DIR,)
        out.append(_make(scope, GateKind.INTEGRATION, argv, integration, definition))
    return out


def detect_scopes(files: tuple[str, ...]) -> tuple[str, ...]:
    """The root ("") and immediate subdirectories ("dir/") that hold a recognised manifest."""
    scopes = set()
    for f in files:
        parts = f.split("/")
        if parts[-1] in SCOPE_MANIFESTS and len(parts) <= 2:
            scopes.add("" if len(parts) == 1 else parts[0] + "/")
    return tuple(sorted(scopes))


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
    repo: Path, base: str | None, policy: Policy, path_env: str | None = None
) -> Inventory:
    """Gate inventory at BASE with availability and definition-change flags (see module doc).

    Gates are sorted by (scope, kind order in model.KIND_ORDER, id). With base None (no commits),
    only native gates and policy gates exist.
    """
    base_tree = _Tree(repo, base, worktree=False)
    work_tree = _Tree(repo, base, worktree=True)
    files = git.list_files_at(repo, base)
    scopes = detect_scopes(files)

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
        gates.append(_availability(gate, repo, path_env))
    gates.extend(native_gates())

    gates.sort(key=lambda g: (g.scope, KIND_ORDER.index(g.kind), g.id))
    return Inventory(gates=tuple(gates), scopes=scopes)
