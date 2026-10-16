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
- policy.waivers (v0.9): how CI waivers are set up at BASE: waivers_permit_pass,
  allow_author_waivers, and the waiver_signers file (T40) with one entry per key line
  (principals, key type, hardware, touch_required). Setups that defeat signed waiver codes are
  listed in problems; they do not affect `plannable`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shlex
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
        "policy": {"status": None, "source": None, "errors": [], "waivers": None},
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
    pol_facts["waivers"] = _waiver_facts(root, base, pol, problems)
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


_KEY_TYPE = re.compile(r"(?:ssh-|ecdsa-|sk-)[A-Za-z0-9@._-]+")


def _signer_line(line: str) -> dict[str, Any] | None:
    """One allowed-signers line: `principals [options] keytype key [comment]`, or None when it is
    a comment, blank or unreadable."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    try:
        tokens = shlex.split(line, posix=True)
    except ValueError:
        return {"principals": [], "key_type": None, "hardware": False, "touch_required": False,
                "namespaces": None, "error": "unparsable line"}  # fmt: skip
    i = next((n for n, t in enumerate(tokens[1:], 1) if _KEY_TYPE.fullmatch(t)), None)
    if i is None:
        return {"principals": tokens[0].split(","), "key_type": None, "hardware": False,
                "touch_required": False, "namespaces": None, "error": "no key type"}  # fmt: skip
    options = ",".join(tokens[1:i]).split(",")
    names = [o.split("=", 1)[1] for o in options if o.startswith("namespaces=")]
    key_type = tokens[i]
    hardware = key_type.startswith("sk-")
    return {
        "principals": tokens[0].split(","),
        "key_type": key_type,
        "hardware": hardware,
        "touch_required": hardware and "no-touch-required" not in options,
        "namespaces": names[0] if names else None,
        "error": None,
    }


def _waiver_facts(root: Path, base: str | None, pol: Any, problems: list[str]) -> dict[str, Any]:
    """How waivers are set up at BASE, and the problems that make signed codes (T40) weak."""
    out: dict[str, Any] = {
        "waivers_permit_pass": pol.waivers_permit_pass,
        "allow_author_waivers": pol.allow_author_waivers,
        "signers_path": pol.waiver_signers or None,
        "signers_found": None,
        "signers": [],
    }
    if not pol.waiver_signers:
        if pol.allow_author_waivers:
            problems.append(
                "allow_author_waivers is set but waiver_signers is not: every author waiver is "
                "rejected (T40; docs/guides/waivers.md, 'Signed waiver codes')"
            )
        return out
    raw = git.read_at(root, base, pol.waiver_signers)
    out["signers_found"] = raw is not None
    if raw is None:
        problems.append(
            f"waiver_signers file {pol.waiver_signers} does not exist at BASE: every label waiver "
            "is rejected until it is committed on the base branch"
        )
        return out
    text = raw.removeprefix(b"\xef\xbb\xbf").decode("utf-8", "replace")
    out["signers"] = [e for e in map(_signer_line, text.splitlines()) if e is not None]
    if not out["signers"]:
        problems.append(
            f"waiver_signers file {pol.waiver_signers} lists no key: every label waiver is "
            "rejected until a key line is added"
        )
    for e in out["signers"]:
        who = ",".join(e["principals"]) or "?"
        if e["error"]:
            problems.append(f"waiver_signers line for {who}: {e['error']}; ssh-keygen will skip it")
        elif not e["hardware"]:
            problems.append(
                f"waiver signing key for {who} is {e['key_type']}, not a hardware (sk-) key: any "
                "process that can use it, an agent included, can sign waivers (T41)"
            )
        elif not e["touch_required"]:
            problems.append(
                f"waiver signing key for {who} has no-touch-required: signing needs no person (T41)"
            )
    return out


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
    w = p.get("waivers")
    if w:
        out.append(
            f"waivers: count as PASS {'yes' if w['waivers_permit_pass'] else 'no'}; "
            f"author waivers {'allowed' if w['allow_author_waivers'] else 'not allowed'}"
        )
        if w["signers_path"]:
            found = "found at BASE" if w["signers_found"] else "MISSING at BASE"
            out.append(f"  signed codes required: {w['signers_path']} ({found})")
            for e in w["signers"]:
                kind = e["key_type"] or "?"
                touch = "touch required" if e["touch_required"] else "NO touch"
                out.append(f"    {','.join(e['principals']) or '?':<20} {kind}  {touch}")
        else:
            out.append("  signed codes: not required (no waiver_signers)")
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
