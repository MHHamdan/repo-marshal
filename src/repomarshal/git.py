"""Git plumbing (owner: agent "git"; roadmap M1, M2; architecture §5.1, §7.3).

Rules:
- Only `subprocess.run([...], ...)` with argv lists; never shell=True. Use `git -C <repo>`.
- Never mutate the user's index, refs, HEAD or working tree. The tree digest uses a temporary
  index file (GIT_INDEX_FILE) created outside the repository and always removed.
- Every failure raises model.GitError (or NotAGitRepository) with a clear message.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
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
    "GIT_EXTERNAL_DIFF",
    "GIT_DIFF_OPTS",
    "GIT_ATTR_SOURCE",
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
    repo: Path,
    *args: str,
    check: bool = True,
    text: bool = True,
    env: dict[str, str] | None = None,
    input: bytes | str | None = None,
) -> subprocess.CompletedProcess:
    """Run git with an argv list. Raises GitError when git is missing or (check) fails."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=text,
            env=env,
            input=input,
            check=False,
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


# Tool caches that write their own `.gitignore` ("*") into the working tree. Files under them are
# never treated as hidden changes even when BASE does not ignore them; otherwise every ruff or
# pytest run would make the next plan noisy. Code executed from these directories would have to
# be wired in by a visible change (residual risk, threat model T18). node_modules is deliberately
# NOT listed: its contents are importable, so hiding it stays fail-closed.
TOOL_CACHE_DIRS = frozenset(
    {".ruff_cache", ".pytest_cache", ".mypy_cache", ".pytype", "__pycache__", ".hypothesis"}
)


def _is_tool_cache(path: str) -> bool:
    return any(seg in TOOL_CACHE_DIRS for seg in path.rstrip("/").split("/"))


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


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}


def _encode_path(path: str) -> bytes:
    return path.encode("utf-8", "surrogateescape")


def _nul_split(raw: bytes) -> list[str]:
    return [_decode_path(p) for p in raw.split(b"\0") if p]


def _literal(path: str, *magic: str) -> str:
    return f":({','.join(('top', 'literal', *magic))}){path}"


# Above this many files hidden by ignore rules that BASE does not have, the files are not added to
# the digest; their top-level directories are reported in ChangeSet.opaque instead.
MAX_UNTRUSTED_IGNORED = 500

# Pathspecs per git invocation when expanding ignored directories.
_PATHSPEC_BATCH = 200


def _embedded_reason(path: str) -> str:
    return f"{path.rstrip('/')} (embedded git repository; contents not inspected)"


def _add_all(root: Path, env: dict[str, str]) -> list[str]:
    """`git add -A` the whole tree into the temporary index.

    A nested repository without a commit cannot be recorded as a gitlink and makes `git add`
    fail; such repositories are skipped and returned (as opaque reasons) instead.
    """
    args = (*_TMP_INDEX_CONFIG, "add", "-A", "--", ":(top)")
    r = _git(root, *args, check=False, env=env)
    if r.returncode == 0:
        return []
    out = _git(root, "ls-files", "-z", "--others", "--exclude-standard", env=env, text=False).stdout
    unborn = [
        p.rstrip("/")
        for p in _nul_split(out)
        if p.endswith("/") and not _is_evidence_path(p.rstrip("/")) and _is_unborn_repo(root / p)
    ]
    if not unborn:
        raise GitError(f"git add -A failed: {r.stderr.strip()}")
    _git(root, *args, *(_literal(p, "exclude") for p in unborn), env=env)
    return [_embedded_reason(p) for p in unborn]


def _is_unborn_repo(path: Path) -> bool:
    r = _git(path, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False, env=_clean_env())
    return r.returncode != 0


def _drop_evidence(root: Path, head: str | None, env: dict[str, str]) -> None:
    """Keep `.repomarshal/` out of the temporary index (its HEAD version, if tracked, is kept).

    Done after `git add -A` rather than with an exclude pathspec: naming an ignored path in a
    pathspec makes `git add` fail.
    """
    _git(
        root,
        *_TMP_INDEX_CONFIG,
        "rm",
        "-r",
        "--cached",
        "-q",
        "--ignore-unmatch",
        "--",
        _literal(EVIDENCE_DIR),
        env=env,
    )
    if head is None:
        return
    tracked = _git(
        root, "ls-tree", "-r", "-z", "--full-tree", head, "--", EVIDENCE_DIR, text=False
    ).stdout
    if tracked:
        _git(
            root,
            *_TMP_INDEX_CONFIG,
            "update-index",
            "-z",
            "--index-info",
            env=env,
            input=tracked,
            text=False,
        )


def _read_blobs(root: Path, shas: list[str]) -> list[bytes]:
    """Contents of the given (small) blobs, in order, via one `git cat-file --batch`."""
    out = _git(
        root, "cat-file", "--batch", input="".join(s + "\n" for s in shas).encode(), text=False
    ).stdout
    blobs: list[bytes] = []
    pos = 0
    for sha in shas:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].split()
        if len(header) != 3 or header[1] != b"blob":
            raise GitError(f"git cat-file: unexpected object {sha}: {out[pos:nl]!r}")
        size = int(header[2])
        blobs.append(out[nl + 1 : nl + 1 + size])
        pos = nl + 1 + size + 1
    return blobs


class _BaseRules:
    """The `.gitignore` files of BASE, materialised in a scratch repository outside the work
    tree, answering "would BASE's rules ignore this path?" with `git check-ignore --no-index`.
    Nothing else (info/exclude, core.excludesFile, the working tree's .gitignore) is consulted.
    """

    def __init__(self, root: Path, base: str | None, scratch: Path):
        self.root = root
        self.base = base
        self.dir = scratch / "base-rules"
        self._ready = False

    def _prepare(self) -> None:
        self._ready = True
        self.dir.mkdir()
        listing = _git(self.root, "ls-tree", "-r", "-z", "--full-tree", self.base, text=False)
        files: list[tuple[str, str]] = []
        for entry in listing.stdout.split(b"\0"):
            if not entry:
                continue
            meta, _, raw_path = entry.partition(b"\t")
            mode, _, sha = meta.decode("ascii").split()
            path = _decode_path(raw_path)
            if path.rsplit("/", 1)[-1] == ".gitignore" and mode in ("100644", "100755"):
                files.append((path, sha))
        for (path, _), content in zip(
            files, _read_blobs(self.root, [s for _, s in files]), strict=True
        ):
            target = self.dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        icase = _git(self.root, "config", "--type=bool", "core.ignorecase", check=False)
        self.icase = icase.stdout.strip() == "true"
        self.env = {
            **_clean_env(),
            "GIT_DIR": str(self.dir / ".git"),
            "GIT_WORK_TREE": str(self.dir),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        _git(self.dir, "init", "-q", "--template=", env=self.env)

    def ignored(self, paths: list[str]) -> set[str]:
        """The subset of `paths` (directories with a trailing slash) BASE's rules ignore, either
        directly or because a leading directory is ignored."""
        if self.base is None or not paths:
            return set()
        if not self._ready:
            self._prepare()
        r = _git(
            self.dir,
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            f"core.ignoreCase={'true' if self.icase else 'false'}",
            "check-ignore",
            "--no-index",
            "--stdin",
            "-z",
            check=False,
            text=False,
            env=self.env,
            input=b"".join(_encode_path(p) + b"\0" for p in paths),
        )
        if r.returncode not in (0, 1):
            err = r.stderr.decode("utf-8", "replace").strip()
            raise GitError(f"git check-ignore failed: {err}")
        return set(_nul_split(r.stdout))


def _ignored_others(root: Path, env: dict[str, str], *args: str) -> list[str]:
    out = _git(
        root,
        "ls-files",
        "-z",
        "--others",
        "--ignored",
        "--exclude-standard",
        *args,
        env=env,
        text=False,
    ).stdout
    return [
        p for p in _nul_split(out) if not _is_evidence_path(p.rstrip("/")) and not _is_tool_cache(p)
    ]


def _add_untrusted_ignored(
    root: Path, base: str | None, env: dict[str, str], scratch: Path
) -> list[str]:
    """Force-add files that are ignored now but not by BASE's `.gitignore` files (rules the
    change itself controls: info/exclude, core.excludesFile, edited .gitignore). Returns opaque
    reasons for what cannot be added: embedded repositories and anything over the cap.
    """
    entries = _ignored_others(root, env, "--directory", "--no-empty-directory")
    if not entries:
        return []
    rules = _BaseRules(root, base, scratch)
    trusted = rules.ignored(entries)
    files = [e for e in entries if not e.endswith("/") and e not in trusted]
    dirs = [e for e in entries if e.endswith("/") and e not in trusted]
    nested: list[str] = []
    for i in range(0, len(dirs), _PATHSPEC_BATCH):
        batch = dirs[i : i + _PATHSPEC_BATCH]
        expanded = _ignored_others(root, env, "--", *(_literal(d) for d in batch))
        trusted_inner = rules.ignored(expanded)
        for e in expanded:
            if e in trusted_inner:
                continue
            (nested if e.endswith("/") else files).append(e)
    opaque = [_embedded_reason(n) for n in sorted(set(nested))]
    files = sorted(set(files))
    if len(files) > MAX_UNTRUSTED_IGNORED:
        groups: dict[str, int] = {}
        for f in files:
            top, sep, _ = f.partition("/")
            groups[top + sep] = groups.get(top + sep, 0) + 1
        opaque.extend(
            f"{top} ({n} file{'s' if n != 1 else ''} hidden by ignore rules not present at BASE)"
            for top, n in sorted(groups.items())
        )
    elif files:
        _git(
            root,
            *_TMP_INDEX_CONFIG,
            "add",
            "-f",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            env={**env, "GIT_LITERAL_PATHSPECS": "1"},
            input=b"".join(_encode_path(f) + b"\0" for f in files),
            text=False,
        )
    return opaque


def _snapshot(root: Path, base: str | None) -> tuple[str, tuple[str, ...]]:
    """(tree id, opaque reasons) for the working tree, judged against BASE's ignore rules.

    The single source of the tree digest: tree_digest() and collect_changes() both use it, so
    the digest covers exactly the files the change set reports plus unchanged tracked files.
    """
    tmp_parent = Path(tempfile.gettempdir()).resolve()
    if tmp_parent == root or tmp_parent.is_relative_to(root):
        raise GitError(
            f"temporary directory {tmp_parent} is inside the repository; set TMPDIR elsewhere"
        )
    with tempfile.TemporaryDirectory(prefix="repomarshal-index-") as tmp:
        env = _temp_index_env(Path(tmp) / "index")
        head = head_sha(root)
        if head is None:
            _git(root, *_TMP_INDEX_CONFIG, "read-tree", "--empty", env=env)
        else:
            _git(root, *_TMP_INDEX_CONFIG, "read-tree", head, env=env)
        opaque = _add_all(root, env)
        _drop_evidence(root, head, env)
        opaque += _add_untrusted_ignored(root, base, env, Path(tmp))
        digest = _git(root, *_TMP_INDEX_CONFIG, "write-tree", env=env).stdout.strip()
    return digest, tuple(opaque)


def tree_digest(repo: Path, *, base: str | None = None) -> str:
    """Git tree id of the current working tree, including uncommitted work (architecture §5.1).

    Algorithm: temporary index file outside the repo -> `git read-tree HEAD` (or `--empty` when
    unborn) -> `git add -A` -> drop `.repomarshal/` (keeping its HEAD version if tracked) ->
    force-add files ignored only by rules BASE does not have -> `git write-tree`. Covers staged,
    unstaged, deleted and untracked files that BASE's `.gitignore` files do not ignore; excludes
    `.repomarshal/`. The user's `.git/index` must be byte-identical before and after.
    Deterministic: the same content gives the same id.

    `base` selects the trusted ignore rules; None resolves it as `resolve_base(repo)` does, so
    `tree_digest(repo) == collect_changes(repo, resolve_base(repo)).tree_digest`.

    `repo` may be any directory inside the work tree; the digest always covers the whole tree.
    """
    root = repo_root(repo)
    if base is None:
        base = resolve_base(root)
    return _snapshot(root, base)[0]


def _tree_of(repo: Path, rev: str | None) -> str:
    if rev is None:
        return EMPTY_TREE
    r = _git(
        repo, "rev-parse", "--verify", "-q", "--end-of-options", f"{rev}^{{tree}}", check=False
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise GitError(f"{rev!r} does not resolve to a tree")
    return r.stdout.strip()


def _parse_raw(raw: bytes) -> list[tuple[str, str, str | None, tuple[str, str, str, str]]]:
    """Parse `--raw -z` output into (status letter, path, old_path, (old mode, new mode, old sha,
    new sha)) entries, in output order."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    entries: list[tuple[str, str, str | None, tuple[str, str, str, str]]] = []
    i = 0
    while i < len(fields):
        meta = fields[i].decode("ascii", "replace")
        if not meta.startswith(":"):
            raise GitError(f"unexpected git diff-tree output: {meta!r}")
        parts = meta[1:].split()
        if len(parts) != 5:
            raise GitError(f"unexpected git diff-tree output: {meta!r}")
        info = (parts[0], parts[1], parts[2], parts[3])
        letter = parts[4][:1]
        if letter in ("R", "C"):
            old, new = _decode_path(fields[i + 1]), _decode_path(fields[i + 2])
            entries.append((letter, new, old, info))
            i += 3
        else:
            entries.append((letter, _decode_path(fields[i + 1]), None, info))
            i += 2
    return entries


_GITLINK = "160000"
_NULL_SHA = "0" * 40
BINARY_SNIFF_BYTES = 8000  # git's own heuristic: a NUL byte in the first 8000 bytes


def _binary_blobs(root: Path, shas: set[str]) -> set[str]:
    """The blobs among `shas` whose first 8000 bytes contain a NUL byte. Decided by content
    only, never by attributes. Streams `git cat-file --batch` so large blobs are not held."""
    if not shas:
        return set()
    try:
        proc = subprocess.Popen(
            ["git", "-C", str(root), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_clean_env(),
        )
    except FileNotFoundError as e:
        raise GitError("git executable not found on PATH") from e
    order = sorted(shas)
    request = "".join(s + "\n" for s in order).encode()

    def feed() -> None:
        try:
            proc.stdin.write(request)  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    binary: set[str] = set()
    out = proc.stdout
    assert out is not None
    try:
        for sha in order:
            header = out.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise GitError(f"git cat-file: cannot read blob {sha}")
            remaining = int(header[2]) + 1  # content plus the trailing newline
            head = out.read(min(remaining, BINARY_SNIFF_BYTES))
            remaining -= len(head)
            if b"\0" in head[:BINARY_SNIFF_BYTES]:
                binary.add(sha)
            while remaining > 0:
                chunk = out.read(min(remaining, 1 << 20))
                if not chunk:
                    raise GitError(f"git cat-file: truncated output for {sha}")
                remaining -= len(chunk)
    finally:
        out.close()
        proc.wait()
        writer.join()
    return binary


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
    """(binary, added, removed, truncated) for one file's -U0 patch section. `binary` means git
    printed no lines (it should not, with --text); the caller decides binary-ness by content."""
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
    untracked files not ignored by BASE's `.gitignore` files), as a ChangeSet.

    The digest comes from the same snapshot as tree_digest(repo, base=base). It is diffed
    against `base^{tree}` (or EMPTY_TREE when base is None) with rename detection, and a
    `--text -U0` patch gives added/removed lines whatever the attributes say. Binary status is
    decided by content (a NUL byte in the first 8000 bytes of either side); binary files get no
    lines. Files are sorted by path. `uncommitted` is True when the digest differs from HEAD's
    tree. `.repomarshal/` never appears. `opaque` lists what could not be inspected: embedded
    repositories added or changed since BASE, and files hidden by ignore rules BASE does not have
    when there are too many to add.
    """
    root = repo_root(repo)
    digest, opaque_list = _snapshot(root, base)
    opaque = list(opaque_list)
    head = head_sha(root)
    base_tree = _tree_of(root, base)
    out = _git(
        root,
        "-c",
        "diff.external=",
        "-c",
        "diff.noprefix=false",
        "-c",
        "diff.mnemonicPrefix=false",
        "diff-tree",
        "-r",
        "-M",
        "-z",
        "--raw",
        "-p",
        "-U0",
        "--text",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-relative",
        "--ignore-submodules=none",
        "--full-index",
        base_tree,
        digest,
        text=False,
        env=_clean_env(),
    ).stdout
    files: list[ChangedFile] = []
    if out:
        sep = out.find(b"\0\0")
        raw, patch = (out, b"") if sep < 0 else (out[: sep + 1], out[sep + 2 :])
        entries = _parse_raw(raw)
        sections = _split_patches(patch)
        # A type change (file <-> symlink) is one raw entry but two patches: deletion, creation.
        expected = sum(2 if letter == "T" else 1 for letter, _, _, _ in entries)
        if len(sections) != expected:
            raise GitError(f"git diff-tree: expected {expected} patches, got {len(sections)}")
        blobs = {
            sha
            for _, _, _, (old_mode, new_mode, old_sha, new_sha) in entries
            for mode, sha in ((old_mode, old_sha), (new_mode, new_sha))
            if mode not in (_GITLINK, "000000") and sha != _NULL_SHA
        }
        binary_blobs = _binary_blobs(root, blobs)
        cursor = 0
        for letter, path, old_path, (_, new_mode, old_sha, new_sha) in entries:
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
            if new_mode == _GITLINK and status is not FileStatus.DELETED:
                opaque.append(_embedded_reason(path))
            binary = old_sha in binary_blobs or new_sha in binary_blobs
            # Lines git withheld although the content is text: report them as incomplete.
            withheld = not binary and any(p[0] for p in parsed)
            added = [] if binary else [ln for p in parsed for ln in p[1]]
            removed = [] if binary else [ln for p in parsed for ln in p[2]]
            truncated = withheld or (not binary and any(p[3] for p in parsed))
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
        opaque=tuple(dict.fromkeys(opaque)),
    )


_FULL_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# Commit content is immutable, so reads at a full sha are cached for the life of the process.
_TREE_CACHE: dict[tuple[str, str], frozenset[str]] = {}
_BLOB_CACHE: dict[tuple[str, str, str], bytes | None] = {}


def _tree_set(repo: Path, rev: str) -> frozenset[str]:
    key = (str(Path(repo).resolve()), rev)
    cached = _TREE_CACHE.get(key)
    if cached is None:
        out = _git(repo, "ls-tree", "-r", "-z", "--name-only", rev, text=False).stdout
        cached = frozenset(p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p)
        if _FULL_SHA.fullmatch(rev):
            _TREE_CACHE[key] = cached
    return cached


def read_at(repo: Path, rev: str | None, path: str) -> bytes | None:
    """Content of `path` at commit `rev` (`git show rev:path`), or None if the path does not exist
    there. rev None means "no base" and always returns None. Never reads the working tree.
    """
    if rev is None:
        return None
    if _FULL_SHA.fullmatch(rev):
        key = (str(Path(repo).resolve()), rev, path)
        if key in _BLOB_CACHE:
            return _BLOB_CACHE[key]
        if path not in _tree_set(repo, rev):  # one ls-tree per commit instead of one call per path
            _BLOB_CACHE[key] = None
            return None
        r = _git(repo, "cat-file", "blob", f"{rev}:{path}", text=False, check=False)
        _BLOB_CACHE[key] = r.stdout if r.returncode == 0 else None
        return _BLOB_CACHE[key]
    r = _git(repo, "cat-file", "-e", f"{rev}:{path}", check=False)
    if r.returncode != 0:
        return None
    return _git(repo, "cat-file", "blob", f"{rev}:{path}", text=False).stdout


def list_files_at(repo: Path, rev: str | None) -> tuple[str, ...]:
    """All file paths in the tree of `rev` (sorted). rev None returns ()."""
    if rev is None:
        return ()
    return tuple(sorted(_tree_set(repo, rev)))


def read_worktree(repo: Path, path: str) -> bytes | None:
    """Content of `path` in the working tree, or None if missing. Refuses (returns None for) paths
    that resolve outside the repository root, including via symlinks.
    """
    root = Path(repo).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target.read_bytes()
