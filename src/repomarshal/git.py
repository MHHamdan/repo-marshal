"""Git plumbing (owner: agent "git"; roadmap M1, M2; architecture §5.1, §7.3).

Rules:
- Only `subprocess.run([...], ...)` with argv lists; never shell=True. Use `git -C <repo>`.
- Never mutate the user's index, refs, HEAD or working tree. The tree digest uses a temporary
  index file (GIT_INDEX_FILE) created outside the repository and always removed.
- Every failure raises model.GitError (or NotAGitRepository) with a clear message.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repomarshal.model import ChangeSet, GitError, NotAGitRepository

MAX_LINES_PER_FILE = 2000  # cap for ChangedFile.added / .removed; set truncated=True when hit


def _git(
    repo: Path, *args: str, check: bool = True, text: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run git with an argv list. Raises GitError when git is missing or (check) fails."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=text, env=env, check=False
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH") from e
    if check and r.returncode != 0:
        err = r.stderr if text else r.stderr.decode("utf-8", "replace")
        raise GitError(f"git {' '.join(args)} failed: {err.strip()}")
    return r


def repo_root(path: str | Path) -> Path:
    """Absolute top-level directory of the repository containing `path`.

    Raises NotAGitRepository if `path` is not inside a git work tree, or GitError if git is
    not installed.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise NotAGitRepository(f"{p} does not exist")
    r = _git(p if p.is_dir() else p.parent, "rev-parse", "--show-toplevel", check=False)
    if r.returncode != 0:
        raise NotAGitRepository(f"{p} is not inside a git work tree")
    return Path(r.stdout.strip()).resolve()


def head_sha(repo: Path) -> str | None:
    """Full sha of HEAD, or None on an unborn branch (no commits yet)."""
    r = _git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def resolve_base(repo: Path, explicit: str | None = None) -> str | None:
    """Resolve the BASE commit sha (architecture §7.3).

    Order:
      1. `explicit` (any rev git understands); raise GitError if it does not resolve.
      2. merge-base(HEAD, origin/HEAD)
      3. merge-base(HEAD, origin/main), then origin/master
      4. merge-base(HEAD, main), then master
      5. HEAD itself (when on the default branch with uncommitted work, the change is the
         uncommitted work).
    Return None only when the repository has no commits (unborn HEAD); callers then diff against
    the empty tree (model.EMPTY_TREE).
    """
    raise NotImplementedError


def tree_digest(repo: Path) -> str:
    """Git tree id of the current working tree, including uncommitted work (architecture §5.1).

    Algorithm: temporary index file outside the repo -> `git read-tree HEAD` (or `--empty` when
    unborn) -> `git add -A -- . ':(exclude).repomarshal'` -> `git write-tree`. Covers staged,
    unstaged, deleted and untracked non-ignored files; excludes `.repomarshal/`. The user's
    `.git/index` must be byte-identical before and after. Deterministic: the same content gives
    the same id.
    """
    raise NotImplementedError


def collect_changes(repo: Path, base: str | None) -> ChangeSet:
    """Everything that differs between BASE and the working tree (committed + uncommitted +
    untracked non-ignored), as a ChangeSet.

    Suggested approach: digest = tree_digest(repo); diff `base^{tree}` (or EMPTY_TREE when base is
    None) against that digest with rename detection (`git diff-tree -r -M -z --raw`), then a
    unified diff with `--no-color -U0` for added/removed lines. Binary files get binary=True and
    no lines. Files are sorted by path. `uncommitted` is True when the digest differs from
    HEAD's tree. `.repomarshal/` never appears.
    """
    raise NotImplementedError


def read_at(repo: Path, rev: str | None, path: str) -> bytes | None:
    """Content of `path` at commit `rev` (`git show rev:path`), or None if the path does not exist
    there. rev None means "no base" and always returns None. Never reads the working tree.
    """
    if rev is None:
        return None
    r = _git(repo, "cat-file", "-e", f"{rev}:{path}", check=False)
    if r.returncode != 0:
        return None
    return _git(repo, "cat-file", "blob", f"{rev}:{path}", text=False).stdout


def list_files_at(repo: Path, rev: str | None) -> tuple[str, ...]:
    """All file paths in the tree of `rev` (sorted). rev None returns ()."""
    if rev is None:
        return ()
    out = _git(repo, "ls-tree", "-r", "-z", "--name-only", rev, text=False).stdout
    return tuple(sorted(p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p))


def read_worktree(repo: Path, path: str) -> bytes | None:
    """Content of `path` in the working tree, or None if missing. Refuses (returns None for) paths
    that resolve outside the repository root, including via symlinks.
    """
    root = Path(repo).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target.read_bytes()
