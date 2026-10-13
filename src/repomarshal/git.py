"""Git plumbing (owner: agent "git"; roadmap M1, M2; architecture §5.1, §7.3).

Rules:
- Only `subprocess.run([...], ...)` with argv lists; never shell=True. Use `git -C <repo>`.
- Never mutate the user's index, refs, HEAD or working tree. The tree digest uses a temporary
  index file (GIT_INDEX_FILE) created outside the repository and always removed.
- Every failure raises model.GitError (or NotAGitRepository) with a clear message.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from repomarshal.model import (
    EMPTY_TREE,
    ChangedFile,
    ChangeSet,
    FileStatus,
    GitError,
    NotAGitRepository,
)

MAX_LINES_PER_FILE = 2000  # cap for ChangedFile.added / .removed; set truncated=True when hit

EVIDENCE_DIR = ".repomarshal"

# Candidate refs for the default BASE, in order (architecture §7.3). Full ref names so that a tag
# or a file called "main" can never shadow the branch.
_BASE_CANDIDATES = (
    "refs/remotes/origin/HEAD",
    "refs/remotes/origin/main",
    "refs/remotes/origin/master",
    "refs/heads/main",
    "refs/heads/master",
)

# Environment variables that would redirect or reinterpret our git calls if inherited.
_SCRUBBED_ENV = (
    "GIT_INDEX_FILE",
    "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
)

# Config overrides for the temporary-index commands: no split index, untracked cache or fsmonitor,
# so nothing besides loose objects is written into the user's .git directory.
_TMP_INDEX_CONFIG = (
    "-c",
    "core.splitIndex=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.fsmonitor=false",
)


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


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _is_evidence_path(path: str) -> bool:
    return path == EVIDENCE_DIR or path.startswith(EVIDENCE_DIR + "/")


def repo_root(path: str | Path) -> Path:
    """Absolute top-level directory of the repository containing `path`.

    Raises NotAGitRepository if `path` is not inside a git work tree, or GitError if git is
    not installed.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise NotAGitRepository(f"{p} does not exist")
    r = _git(p if p.is_dir() else p.parent, "rev-parse", "--show-toplevel", check=False)
    if r.returncode != 0 or not r.stdout.strip():
        raise NotAGitRepository(f"{p} is not inside a git work tree")
    return Path(r.stdout.strip()).resolve()


def head_sha(repo: Path) -> str | None:
    """Full sha of HEAD, or None on an unborn branch (no commits yet).

    Raises NotAGitRepository if `repo` is not inside a git work tree.
    """
    r = _git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False)
    if r.returncode == 0:
        return r.stdout.strip()
    repo_root(repo)  # distinguishes "unborn" from "not a repository"
    return None


def _resolve_commit(repo: Path, rev: str) -> str | None:
    """Full commit sha for `rev`, or None if it does not name a commit."""
    r = _git(
        repo, "rev-parse", "--verify", "-q", "--end-of-options", f"{rev}^{{commit}}", check=False
    )
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


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

    A candidate that exists but shares no history with HEAD is skipped.
    """
    root = repo_root(repo)
    if explicit is not None:
        sha = _resolve_commit(root, explicit) if explicit.strip() else None
        if sha is None:
            raise GitError(f"--base {explicit!r} does not resolve to a commit")
        return sha
    head = head_sha(root)
    if head is None:
        return None
    for ref in _BASE_CANDIDATES:
        if _resolve_commit(root, ref) is None:
            continue
        r = _git(root, "merge-base", head, ref, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        if r.returncode not in (0, 1):  # 1 means "no common ancestor"
            raise GitError(f"git merge-base HEAD {ref} failed: {r.stderr.strip()}")
    return head


def _temp_index_env(index_file: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
    env["GIT_INDEX_FILE"] = str(index_file)
    return env


def tree_digest(repo: Path) -> str:
    """Git tree id of the current working tree, including uncommitted work (architecture §5.1).

    Algorithm: temporary index file outside the repo -> `git read-tree HEAD` (or `--empty` when
    unborn) -> `git add -A -- . ':(exclude).repomarshal'` -> `git write-tree`. Covers staged,
    unstaged, deleted and untracked non-ignored files; excludes `.repomarshal/`. The user's
    `.git/index` must be byte-identical before and after. Deterministic: the same content gives
    the same id.

    `repo` may be any directory inside the work tree; the digest always covers the whole tree.
    Changes to a tracked `.repomarshal/` are ignored (its HEAD version is kept).
    """
    root = repo_root(repo)
    tmp_parent = Path(tempfile.gettempdir()).resolve()
    if tmp_parent == root or tmp_parent.is_relative_to(root):
        raise GitError(
            f"temporary directory {tmp_parent} is inside the repository; set TMPDIR elsewhere"
        )
    with tempfile.TemporaryDirectory(prefix="repomarshal-index-") as tmp:
        env = _temp_index_env(Path(tmp) / "index")
        if head_sha(root) is None:
            _git(root, *_TMP_INDEX_CONFIG, "read-tree", "--empty", env=env)
        else:
            _git(root, *_TMP_INDEX_CONFIG, "read-tree", "HEAD", env=env)
        _git(
            root,
            *_TMP_INDEX_CONFIG,
            "add",
            "-A",
            "--",
            ":(top)",
            f":(top,exclude){EVIDENCE_DIR}",
            env=env,
        )
        return _git(root, *_TMP_INDEX_CONFIG, "write-tree", env=env).stdout.strip()


def _tree_of(repo: Path, rev: str | None) -> str:
    if rev is None:
        return EMPTY_TREE
    r = _git(
        repo, "rev-parse", "--verify", "-q", "--end-of-options", f"{rev}^{{tree}}", check=False
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise GitError(f"{rev!r} does not resolve to a tree")
    return r.stdout.strip()


def _parse_raw(raw: bytes) -> list[tuple[str, str, str | None]]:
    """Parse `--raw -z` output into (status letter, path, old_path) entries, in output order."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    entries: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(fields):
        meta = fields[i].decode("ascii", "replace")
        if not meta.startswith(":"):
            raise GitError(f"unexpected git diff-tree output: {meta!r}")
        letter = meta.split()[-1][:1]
        if letter in ("R", "C"):
            old, new = _decode_path(fields[i + 1]), _decode_path(fields[i + 2])
            entries.append((letter, new, old))
            i += 3
        else:
            entries.append((letter, _decode_path(fields[i + 1]), None))
            i += 2
    return entries


def _split_patches(patch: bytes) -> list[list[bytes]]:
    """Split a multi-file patch into per-file line lists, one per `diff --git` header."""
    sections: list[list[bytes]] = []
    for line in patch.split(b"\n"):
        if line.startswith(b"diff --git "):
            sections.append([])
        elif sections:
            sections[-1].append(line)
    return sections


def _parse_patch(lines: list[bytes]) -> tuple[bool, list[str], list[str], bool]:
    """(binary, added, removed, truncated) for one file's -U0 patch section."""
    added: list[str] = []
    removed: list[str] = []
    truncated = False
    in_hunks = False
    for line in lines:
        if not in_hunks:
            if line.startswith(b"Binary files ") or line.startswith(b"GIT binary patch"):
                return True, [], [], False
            if line.startswith(b"@@"):
                in_hunks = True
            continue
        if line.startswith(b"@@"):
            continue
        if line.startswith(b"+"):
            target = added
        elif line.startswith(b"-"):
            target = removed
        else:  # "\ No newline at end of file" or the trailing empty split
            continue
        if len(target) >= MAX_LINES_PER_FILE:
            truncated = True
            continue
        target.append(line[1:].decode("utf-8", "replace"))
    return False, added, removed, truncated


_STATUS = {
    "A": FileStatus.ADDED,
    "D": FileStatus.DELETED,
    "M": FileStatus.MODIFIED,
    "T": FileStatus.MODIFIED,  # type change (e.g. file <-> symlink)
    "R": FileStatus.RENAMED,
}


def collect_changes(repo: Path, base: str | None) -> ChangeSet:
    """Everything that differs between BASE and the working tree (committed + uncommitted +
    untracked non-ignored), as a ChangeSet.

    Suggested approach: digest = tree_digest(repo); diff `base^{tree}` (or EMPTY_TREE when base is
    None) against that digest with rename detection (`git diff-tree -r -M -z --raw`), then a
    unified diff with `--no-color -U0` for added/removed lines. Binary files get binary=True and
    no lines. Files are sorted by path. `uncommitted` is True when the digest differs from
    HEAD's tree. `.repomarshal/` never appears.
    """
    root = repo_root(repo)
    digest = tree_digest(root)
    head = head_sha(root)
    base_tree = _tree_of(root, base)
    out = _git(
        root,
        "diff-tree",
        "-r",
        "-M",
        "-z",
        "--raw",
        "-p",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--full-index",
        base_tree,
        digest,
        text=False,
    ).stdout
    files: list[ChangedFile] = []
    if out:
        sep = out.find(b"\0\0")
        raw, patch = (out, b"") if sep < 0 else (out[: sep + 1], out[sep + 2 :])
        entries = _parse_raw(raw)
        sections = _split_patches(patch)
        # A type change (file <-> symlink) is one raw entry but two patches: deletion, creation.
        expected = sum(2 if letter == "T" else 1 for letter, _, _ in entries)
        if len(sections) != expected:
            raise GitError(f"git diff-tree: expected {expected} patches, got {len(sections)}")
        cursor = 0
        for letter, path, old_path in entries:
            span = 2 if letter == "T" else 1
            parsed = [_parse_patch(sec) for sec in sections[cursor : cursor + span]]
            cursor += span
            status = _STATUS.get(letter)
            if status is None:
                raise GitError(f"unexpected diff status {letter!r} for {path!r}")
            if status is FileStatus.RENAMED:
                if _is_evidence_path(path) and _is_evidence_path(old_path or ""):
                    continue
                if _is_evidence_path(path):  # moved into .repomarshal/: a deletion outside it
                    path, old_path, status = old_path or "", None, FileStatus.DELETED
                elif _is_evidence_path(old_path or ""):  # moved out: an addition
                    old_path, status = None, FileStatus.ADDED
            elif _is_evidence_path(path):
                continue
            binary = any(p[0] for p in parsed)
            added = [] if binary else [ln for p in parsed for ln in p[1]]
            removed = [] if binary else [ln for p in parsed for ln in p[2]]
            truncated = any(p[3] for p in parsed)
            files.append(
                ChangedFile(
                    path=path,
                    status=status,
                    old_path=old_path,
                    added=tuple(added),
                    removed=tuple(removed),
                    binary=binary,
                    truncated=truncated,
                )
            )
    files.sort(key=lambda f: f.path)
    head_tree = _tree_of(root, head)
    return ChangeSet(
        base=base,
        head=head,
        tree_digest=digest,
        files=tuple(files),
        uncommitted=digest != head_tree,
    )


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
