"""`repomarshal doctor` (owner: agent "hosts"; roadmap SHOULD #5; MISSION.md §29).

    run(args) -> int

Report actionable facts, read-only: git availability and repository state (branch, BASE and how
it was resolved via anchor.effective_base, uncommitted changes), detected scopes and the gate
inventory with availability, policy status (default / valid at BASE / invalid -> errors), optional
providers (mcp extra importable, SkillSpector / Playwright / Reticle on PATH: report found or not,
never claim support), and Python version. --json prints a stable object. Exit 0 when the
repository can be planned, 3 when policy is invalid or not a git repo. Never executes gates.

Implementation notes (agent "hosts"):
- Read-only means: no gate runs, no file is written (not `.repomarshal/`, not the anchor state,
  not the index). Uncommitted changes come from `git --no-optional-locks status`, which does not
  refresh the index; the temporary-index snapshot used by `plan` is not taken, so not even loose
  objects are written.
- Exit codes: 0 plannable; 3 not a git repository, git missing, BASE unresolvable, policy invalid
  or the inventory inconsistent (duplicate gate ids); 4 internal error.
- --json object keys (stable): repomarshal_version, python, git, repository, policy, scopes, gates,
  providers, plannable, problems. Values that do not apply are null.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

from repomarshal import __version__, anchor, git, inventory, policy
from repomarshal.model import EXIT_INTERNAL_ERROR, GitError, NotAGitRepository, PolicyInvalid

EXIT_BLOCKED = 3

# Optional external providers: reported as found / not found on PATH only. RepoMarshal v0.2 does
# not integrate them; finding one is not a claim of support.
PROVIDERS = (("skillspector", "SkillSpector"), ("playwright", "Playwright"), ("reticle", "Reticle"))


def _git_info() -> dict[str, Any]:
    exe = shutil.which("git")
    info: dict[str, Any] = {"found": exe is not None, "path": exe, "version": None}
    if exe:
        try:
            out = subprocess.run(
                [exe, "--version"], capture_output=True, text=True, check=False, timeout=30
            ).stdout.strip()
            info["version"] = out.removeprefix("git version ").strip() or None
        except (OSError, subprocess.SubprocessError):
            info["version"] = None
    return info


def _providers() -> dict[str, Any]:
    try:
        mcp_found = importlib.util.find_spec("mcp") is not None
    except (ImportError, ValueError):
        mcp_found = False
    out: dict[str, Any] = {"mcp_extra": {"found": mcp_found, "module": "mcp"}}
    for exe, label in PROVIDERS:
        path = shutil.which(exe)
        out[exe] = {"name": label, "found": path is not None, "path": path}
    return out


def _uncommitted(root: Path) -> tuple[bool | None, int | None]:
    r = git._git(
        root,
        "--no-optional-locks",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=normal",
        check=False,
        text=False,
    )
    if r.returncode != 0:
        return None, None
    entries = r.stdout.split(b"\0")
    count = 0
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        count += 1
        if entry[:1] in (b"R", b"C"):  # renames and copies carry the old path as the next entry
            i += 1
    return count > 0, count


def _gate_fact(g: Any) -> dict[str, Any]:
    return {
        "id": g.id,
        "kind": g.kind.value,
        "scope": g.scope,
        "provider": g.provider,
        "argv": list(g.argv),
        "source": g.source,
        "available": g.available,
        "unavailable_reason": g.unavailable_reason,
        "definition_changed": g.definition_changed,
    }


def collect(repo: str | Path, path_env: str | None = None) -> dict[str, Any]:
    """Every fact `doctor` reports, as a JSON-ready dict. Never raises for expected problems."""
    if path_env is None:
        path_env = os.environ.get("PATH")
    problems: list[str] = []
    facts: dict[str, Any] = {
        "repomarshal_version": __version__,
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "git": _git_info(),
        "repository": {
            "path": str(Path(repo).resolve()),
            "is_git": False,
            "root": None,
            "branch": None,
            "head": None,
            "base": None,
            "base_resolution": None,
            "uncommitted": None,
            "changed_paths": None,
        },
        "policy": {"status": None, "source": None, "errors": []},
        "scopes": None,
        "gates": None,
        "providers": _providers(),
        "plannable": False,
        "problems": problems,
    }
    if not facts["git"]["found"]:
        problems.append("git executable not found on PATH")
        return facts

    rep = facts["repository"]
    try:
        root = git.repo_root(repo)
    except (NotAGitRepository, GitError) as e:
        problems.append(str(e))
        return facts
    rep.update(is_git=True, root=str(root))
    rep["branch"] = anchor._branch(root)
    try:
        rep["head"] = git.head_sha(root)
        base, how = anchor.effective_base(root, None)
    except (GitError, NotAGitRepository) as e:
        problems.append(f"cannot resolve BASE: {e}")
        return facts
    rep.update(base=base, base_resolution=how)
    rep["uncommitted"], rep["changed_paths"] = _uncommitted(root)

    pol_facts = facts["policy"]
    try:
        pol = policy.load_policy(root, base)
    except GitError as e:
        problems.append(f"cannot read {policy.POLICY_FILE} at BASE: {e}")
        return facts
    except PolicyInvalid as e:
        pol_facts.update(status="invalid", source=f"base:{base}:{policy.POLICY_FILE}")
        pol_facts["errors"] = list(e.errors)
        problems.append(f"{policy.POLICY_FILE} at BASE is invalid: plan and done are BLOCKED")
        return facts
    pol_facts.update(status="default" if pol.source == "default" else "valid", source=pol.source)
    if pol.source == "default" and (root / policy.POLICY_FILE).exists():
        problems.append(
            f"{policy.POLICY_FILE} exists in the working tree but not at BASE: it has no effect "
            "until committed on the base branch"
        )

    try:
        inv = inventory.build_inventory(root, base, pol, path_env=path_env)
    except (ValueError, GitError) as e:
        problems.append(f"gate inventory is inconsistent: {e}")
        return facts
    facts["scopes"] = list(inv.scopes)
    facts["gates"] = [_gate_fact(g) for g in inv.gates]
    for g in inv.gates:
        if not g.available:
            problems.append(f"gate {g.id} is unavailable ({g.unavailable_reason})")
    facts["plannable"] = True
    return facts


def _text(f: dict[str, Any]) -> str:
    out: list[str] = [f"repomarshal {f['repomarshal_version']}  (Python {f['python']['version']})"]
    g = f["git"]
    out.append(f"git: {(g['version'] or 'found') if g['found'] else 'NOT FOUND on PATH'}")
    r = f["repository"]
    if r["is_git"]:
        out.append(f"repository: {r['root']}")
        out.append(f"  branch: {r['branch']}  head: {r['head'] or '(no commits)'}")
        out.append(f"  BASE: {r['base'] or '(none: empty tree)'}  ({r['base_resolution']})")
        if r["uncommitted"] is not None:
            state = f"yes ({r['changed_paths']} paths)" if r["uncommitted"] else "no"
            out.append(f"  uncommitted changes: {state}")
    else:
        out.append(f"repository: {r['path']} is NOT a git repository")
    p = f["policy"]
    if p["status"]:
        out.append(f"policy: {p['status']} ({p['source']})")
        for err in p["errors"]:
            out.append(f"  - {err}")
    if f["scopes"] is not None:
        scopes = ", ".join(s or "(root)" for s in f["scopes"]) or "-"
        out.append(f"scopes: {scopes}")
    if f["gates"] is not None:
        out.append("gates:")
        for gate in f["gates"]:
            state = (
                "available" if gate["available"] else f"UNAVAILABLE: {gate['unavailable_reason']}"
            )
            out.append(f"  {gate['id']:<26} {gate['kind']:<14} {state}")
    out.append("optional providers (detection only; not a claim of support):")
    for key, info in f["providers"].items():
        name = info.get("name", key)
        if key == "mcp_extra":
            name = "mcp extra (repomarshal[mcp])"
        out.append(f"  {name:<28} {'found' if info['found'] else 'not found'}")
    if f["problems"]:
        out.append("problems:")
        out += [f"  - {pr}" for pr in f["problems"]]
    out.append(f"plannable: {'yes' if f['plannable'] else 'NO'}")
    return "\n".join(out)


def _exit_code(f: dict[str, Any]) -> int:
    return 0 if f["plannable"] else EXIT_BLOCKED


def run(args: argparse.Namespace) -> int:
    try:
        facts = collect(getattr(args, "repo", "."))
    except Exception:  # noqa: BLE001 - an internal error must never look like "plannable"
        traceback.print_exc()
        print("repomarshal doctor: internal error (exit 4)", file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    if getattr(args, "json", False):
        print(json.dumps(facts, indent=2, sort_keys=True))
    else:
        print(_text(facts))
    return _exit_code(facts)
