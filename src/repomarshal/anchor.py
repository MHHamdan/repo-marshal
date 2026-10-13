"""BASE anchoring across invocations (owner: lead; fail-open review #2).

The automatic BASE comes from refs (origin/*, main, master) that the coding agent can move, for
example by fast-forwarding local `main` onto its own commits so they are never judged. The first
BASE seen for a branch is therefore remembered in `.repomarshal/state/base.json`. Later automatic
resolutions use merge-base(remembered, automatic): the older commit wins, so moving a ref can only
*widen* the diff. An explicit `--base` always wins and replaces the remembered value.

The state file is agent-writable like everything under `.repomarshal/`. Deleting or editing it
only restores plain automatic resolution. It never narrows the diff below what the refs give.
That is the same "may only add work" rule as for duration data (architecture §3.3). The
authoritative anchor remains a CI run with an explicit BASE.
"""

from __future__ import annotations

import json
from pathlib import Path

from repomarshal import git

STATE_FILE = ".repomarshal/state/base.json"


def _branch(repo: Path) -> str:
    r = git._git(repo, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    return r.stdout.strip() or "(detached)"


def _load(repo: Path) -> dict[str, str]:
    try:
        data = json.loads((repo / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _is_commit(repo: Path, sha: str) -> bool:
    return git._git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def effective_base(repo: Path, explicit: str | None) -> tuple[str | None, str]:
    """Return (base sha, how it was chosen). Never writes state."""
    if explicit is not None:
        return git.resolve_base(repo, explicit), "explicit"
    auto = git.resolve_base(repo, None)
    remembered = _load(repo).get(_branch(repo))
    if not remembered or not _is_commit(repo, remembered):
        return auto, "automatic"
    if auto is None:
        return remembered, "remembered"
    if remembered == auto:
        return auto, "automatic (matches remembered)"
    mb = git._git(repo, "merge-base", remembered, auto, check=False)
    if mb.returncode == 0 and mb.stdout.strip():
        older = mb.stdout.strip()
        how = "automatic" if older == auto else "remembered (automatic BASE moved forward)"
        return older, how
    return remembered, "remembered (automatic BASE unrelated to remembered)"


def remember(repo: Path, base: str | None, explicit: bool) -> None:
    """Record the BASE for the current branch: first value wins unless explicit."""
    if base is None:
        return
    path = repo / STATE_FILE
    state = _load(repo)
    branch = _branch(repo)
    if not explicit and branch in state:
        return
    state[branch] = base
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.resolve().is_relative_to(repo.resolve()):
            return
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return  # state is an optimisation of safety, never required for a verdict
