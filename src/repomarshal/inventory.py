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

from pathlib import Path

from repomarshal.model import Inventory, Policy


def build_inventory(
    repo: Path, base: str | None, policy: Policy, path_env: str | None = None
) -> Inventory:
    """Gate inventory at BASE with availability and definition-change flags (see module doc).

    Gates are sorted by (scope, kind order in model.KIND_ORDER, id). With base None (no commits),
    only native gates and policy gates exist.
    """
    raise NotImplementedError
