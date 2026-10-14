"""BASE anchoring across invocations (owner: lead; fail-open review #2).

The automatic BASE comes from refs (origin/*, main, master) that the coding agent can move, for
example by fast-forwarding local `main` onto its own commits so they are never judged. The first
BASE seen for a branch is therefore remembered in `$GIT_DIR/repomarshal/base.json`. Later automatic
resolutions use merge-base(remembered, automatic): the older commit wins, so moving a ref can only
*widen* the diff. An explicit `--base` can likewise only widen the diff (merge-base with the
automatic/remembered BASE) and is never remembered, because the agent can pass one too (v0.2
fail-open review).

The state lives inside the git directory, never in the working tree (v0.3 fail-open review #2): a
file in the tree can be committed, so a pull request could ship its own "remembered" BASE and move
the CI judge's BASE (and with it policy and CODEOWNERS) to an older commit it picked. Locally the
file is still agent-writable, like everything the agent's user owns. Deleting or editing it
only restores plain automatic resolution. It never narrows the diff below what the refs give.
That is the same "may only add work" rule as for duration data (architecture §3.3). The
authoritative anchor remains a CI run with an explicit BASE.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from repomarshal import git

STATE_NAME = "repomarshal/base.json"  # relative to the git directory (`git rev-parse --git-path`)
MAX_STATE_BYTES = 1 << 20


def state_path(repo: Path) -> Path | None:
    r = git._git(repo, "rev-parse", "--git-path", STATE_NAME, check=False)
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        return None
    path = Path(out)
    return path if path.is_absolute() else repo / path


def read_small_json(path: Path | None, limit: int = MAX_STATE_BYTES) -> Any:
    """Parse a small JSON state file, or return None. Never follows a final symlink, reads only a
    regular file, and never reads more than `limit` bytes: a state file pointed at /dev/zero or a
    FIFO must not hang or exhaust memory (v0.3 fail-open review #4)."""
    if path is None:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > limit:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _branch(repo: Path) -> str:
    r = git._git(repo, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    return r.stdout.strip() or "(detached)"


def _load(repo: Path) -> dict[str, str]:
    data = read_small_json(state_path(repo))
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _is_commit(repo: Path, sha: str) -> bool:
    return git._git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def _floor(repo: Path) -> tuple[str | None, str]:
    """The automatic BASE combined with the remembered one (the older wins)."""
    auto = git.resolve_base(repo, None)
    remembered = _load(repo).get(_branch(repo))
    if not remembered or not _is_commit(repo, remembered):
        return auto, "automatic"
    if auto is None:
        return remembered, "remembered"
    if remembered == auto:
        return auto, "automatic (matches remembered)"
    older = _merge_base(repo, remembered, auto)
    if older:
        how = "automatic" if older == auto else "remembered (automatic BASE moved forward)"
        return older, how
    return remembered, "remembered (automatic BASE unrelated to remembered)"


def _merge_base(repo: Path, a: str, b: str) -> str | None:
    mb = git._git(repo, "merge-base", a, b, check=False)
    return mb.stdout.strip() if mb.returncode == 0 and mb.stdout.strip() else None


def effective_base(repo: Path, explicit: str | None) -> tuple[str | None, str]:
    """Return (base sha, how it was chosen). Never writes state.

    An explicit BASE (CLI --base, MCP `base`) may come from the agent itself, so it can only
    WIDEN the diff: the result is merge-base(explicit, floor), where floor is the automatic BASE
    combined with the remembered one (v0.2 fail-open review #1/#2).
    """
    floor, how = _floor(repo)
    if explicit is None:
        return floor, how
    pinned = git.resolve_base(repo, explicit)  # raises GitError for a bad ref
    if floor is None or pinned is None or pinned == floor:
        return pinned if pinned is not None else floor, "explicit"
    older = _merge_base(repo, pinned, floor)
    if older == pinned:
        return pinned, "explicit"
    if older:
        return older, f"explicit, widened to the older {how} BASE (an explicit BASE cannot narrow)"
    return floor, f"{how} (explicit BASE unrelated; ignored)"


def remember(repo: Path, base: str | None, explicit: bool) -> None:
    """Record the BASE for the current branch, once. Explicit (possibly agent-supplied) BASEs are
    never recorded, so they cannot poison later automatic runs (v0.2 review #1)."""
    if base is None or explicit:
        return
    path = state_path(repo)
    if path is None:
        return
    state = _load(repo)
    branch = _branch(repo)
    if branch in state:
        return
    state[branch] = base
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return  # state is an optimisation of safety, never required for a verdict
