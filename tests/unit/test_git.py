"""Unit tests for repomarshal.git (roadmap M1, M2; architecture §5.1, §7.3)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import git
from repomarshal.model import EMPTY_TREE, FileStatus, GitError, NotAGitRepository

# --------------------------------------------------------------------------- fixtures / helpers


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    return GitRepo(tmp_path / "repo")


@pytest.fixture
def committed(repo: GitRepo) -> GitRepo:
    repo.write("README.md", "hello\n")
    repo.write("src/app.py", "print('hi')\n")
    repo.write(".gitignore", "build/\n*.log\n")
    repo.commit("initial")
    return repo


@pytest.fixture
def private_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point tempfile at an empty directory outside the repo so leftovers can be detected."""
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


def head_tree(r: GitRepo) -> str:
    return r.git("rev-parse", "HEAD^{tree}")


def snapshot(r: GitRepo) -> dict[str, object]:
    """Everything the tree digest must leave untouched."""
    index = r.root / ".git" / "index"
    return {
        "index": index.read_bytes() if index.exists() else None,
        "HEAD": (r.root / ".git" / "HEAD").read_bytes(),
        "refs": r.git("for-each-ref", "--format=%(refname) %(objectname)"),
        "status": r.git(
            "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ),
        "stash": r.git("stash", "list"),
    }


def by_path(cs) -> dict:
    return {f.path: f for f in cs.files}


# --------------------------------------------------------------------------- errors


def test_not_a_repo_raises(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(NotAGitRepository):
        git.repo_root(plain)
    with pytest.raises(NotAGitRepository):
        git.tree_digest(plain)
    with pytest.raises(NotAGitRepository):
        git.collect_changes(plain, None)
    with pytest.raises(NotAGitRepository):
        git.resolve_base(plain)
    with pytest.raises(NotAGitRepository):
        git.head_sha(plain)


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(NotAGitRepository):
        git.repo_root(tmp_path / "nope")


def test_git_missing_raises_git_error(repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(repo.root / "no-bin-here"))
    with pytest.raises(GitError, match="not found"):
        git.repo_root(repo.root)


def test_never_uses_shell_true(monkeypatch: pytest.MonkeyPatch, committed: GitRepo) -> None:
    calls = []
    real = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    committed.write("new.txt", "x\n")
    git.collect_changes(committed.root, git.resolve_base(committed.root))
    assert calls
    for args, kwargs in calls:
        assert not kwargs.get("shell", False)
        assert isinstance(args[0], list)


# --------------------------------------------------------------------------- repo_root / head_sha


def test_repo_root_from_subdir_and_file(committed: GitRepo) -> None:
    root = committed.root.resolve()
    assert git.repo_root(committed.root / "src") == root
    assert git.repo_root(committed.root / "src" / "app.py") == root


def test_head_sha_unborn_and_committed(repo: GitRepo) -> None:
    assert git.head_sha(repo.root) is None
    repo.write("a", "a\n")
    sha = repo.commit()
    assert git.head_sha(repo.root) == sha


# --------------------------------------------------------------------------- resolve_base


def test_resolve_base_unborn_is_none(repo: GitRepo) -> None:
    assert git.resolve_base(repo.root) is None


def test_resolve_base_unborn_with_bad_explicit_raises(repo: GitRepo) -> None:
    with pytest.raises(GitError):
        git.resolve_base(repo.root, "HEAD")


def test_resolve_base_explicit(committed: GitRepo) -> None:
    first = committed.head()
    committed.write("b", "b\n")
    second = committed.commit()
    assert git.resolve_base(committed.root, "HEAD~1") == first
    assert git.resolve_base(committed.root, first[:10]) == first
    assert git.resolve_base(committed.root, "main") == second
    committed.git("tag", "-a", "v1", "-m", "v1", first)
    assert git.resolve_base(committed.root, "v1") == first  # annotated tag peeled to commit


@pytest.mark.parametrize("bad", ["does-not-exist", "", "  ", "HEAD~99", "--all", "-q"])
def test_resolve_base_explicit_bad_ref_raises(committed: GitRepo, bad: str) -> None:
    with pytest.raises(GitError):
        git.resolve_base(committed.root, bad)


def test_resolve_base_explicit_tree_is_not_a_commit(committed: GitRepo) -> None:
    with pytest.raises(GitError):
        git.resolve_base(committed.root, head_tree(committed))


def test_resolve_base_on_main_is_head(committed: GitRepo) -> None:
    committed.write("dirty.txt", "x\n")
    assert git.resolve_base(committed.root) == committed.head()


def test_resolve_base_feature_branch_is_merge_base(committed: GitRepo) -> None:
    fork = committed.head()
    committed.git("switch", "-q", "-c", "feature")
    committed.write("feat.txt", "f\n")
    committed.commit("feature work")
    committed.git("switch", "-q", "main")
    committed.write("main.txt", "m\n")
    main_tip = committed.commit("main moves on")
    committed.git("switch", "-q", "feature")
    base = git.resolve_base(committed.root)
    assert base == fork
    assert base != main_tip


def test_resolve_base_master_fallback(tmp_path: Path) -> None:
    r = GitRepo(tmp_path / "r", branch="master")
    r.write("a", "a\n")
    fork = r.commit()
    r.git("switch", "-q", "-c", "topic")
    r.write("b", "b\n")
    r.commit()
    assert git.resolve_base(r.root) == fork


def test_resolve_base_main_preferred_over_master(committed: GitRepo) -> None:
    fork = committed.head()
    committed.git("switch", "-q", "-c", "master")
    committed.write("m", "m\n")
    master_tip = committed.commit()
    committed.git("switch", "-q", "-c", "topic")
    committed.write("t", "t\n")
    committed.commit()
    assert git.resolve_base(committed.root) == fork  # merge-base with main, not master
    committed.git("branch", "-q", "-D", "main")
    assert git.resolve_base(committed.root) == master_tip


def test_resolve_base_no_default_branch_falls_back_to_head(tmp_path: Path) -> None:
    r = GitRepo(tmp_path / "r", branch="trunk")
    r.write("a", "a\n")
    sha = r.commit()
    assert git.resolve_base(r.root) == sha


def test_resolve_base_origin_main_beats_local_main(committed: GitRepo) -> None:
    old = committed.head()
    committed.write("b", "b\n")
    committed.commit()
    committed.git("switch", "-q", "-c", "feature")
    committed.write("c", "c\n")
    committed.commit()
    committed.git("update-ref", "refs/remotes/origin/main", old)
    assert git.resolve_base(committed.root) == old  # origin/main checked before local main


def test_resolve_base_origin_master(committed: GitRepo) -> None:
    old = committed.head()
    committed.write("b", "b\n")
    committed.commit()
    committed.git("update-ref", "refs/remotes/origin/master", old)
    assert git.resolve_base(committed.root) == old


def test_resolve_base_origin_head_first(committed: GitRepo) -> None:
    first = committed.head()
    committed.write("b", "b\n")
    second = committed.commit()
    committed.write("c", "c\n")
    committed.commit()
    committed.git("update-ref", "refs/remotes/origin/develop", first)
    committed.git("update-ref", "refs/remotes/origin/main", second)
    committed.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
    assert git.resolve_base(committed.root) == first


def test_resolve_base_with_bare_remote(tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    r = GitRepo(tmp_path / "work")
    r.write("a", "a\n")
    fork = r.commit()
    r.git("remote", "add", "origin", str(bare))
    r.git("push", "-q", "origin", "main")
    r.git("remote", "set-head", "origin", "main")
    r.git("switch", "-q", "-c", "feature")
    r.write("f", "f\n")
    r.commit()
    # local main moves ahead (unpushed): origin/HEAD still wins
    r.git("switch", "-q", "main")
    r.write("m", "m\n")
    r.commit()
    r.git("switch", "-q", "feature")
    assert git.resolve_base(r.root) == fork


def test_resolve_base_skips_unrelated_history(committed: GitRepo) -> None:
    fork = committed.head()
    committed.git("switch", "-q", "--orphan", "orphan")
    committed.write("o", "o\n")
    orphan_tip = committed.commit("unrelated")
    committed.git("update-ref", "refs/remotes/origin/main", orphan_tip)
    committed.git("switch", "-qf", "main")
    committed.git("switch", "-q", "-c", "feature")
    committed.write("f", "f\n")
    committed.commit()
    assert git.resolve_base(committed.root) == fork


def test_resolve_base_ignores_tag_named_main(tmp_path: Path) -> None:
    r = GitRepo(tmp_path / "r", branch="trunk")
    r.write("a", "a\n")
    first = r.commit()
    r.write("b", "b\n")
    head = r.commit()
    r.git("tag", "main", first)  # a tag is not the main branch
    assert git.resolve_base(r.root) == head


def test_resolve_base_from_subdir(committed: GitRepo) -> None:
    assert git.resolve_base(committed.root / "src") == committed.head()


# --------------------------------------------------------------------------- tree_digest


def test_tree_digest_clean_equals_head_tree(committed: GitRepo) -> None:
    assert git.tree_digest(committed.root) == head_tree(committed)


def test_tree_digest_unborn_empty_is_empty_tree(repo: GitRepo) -> None:
    assert git.tree_digest(repo.root) == EMPTY_TREE


def test_tree_digest_unborn_with_files(repo: GitRepo) -> None:
    repo.write("a.txt", "a\n")
    d = git.tree_digest(repo.root)
    assert d != EMPTY_TREE
    repo.commit()
    assert d == head_tree(repo)


def test_tree_digest_equals_tree_of_committing_everything(committed: GitRepo) -> None:
    committed.write("README.md", "staged\n")
    committed.git("add", "README.md")
    committed.write("src/app.py", "unstaged\n")
    committed.write("untracked/new.txt", "new\n")
    committed.write("build/out.bin", "ignored\n")
    committed.write("debug.log", "ignored\n")
    committed.write(".repomarshal/evidence/x.json", "{}\n")
    d = git.tree_digest(committed.root)
    committed.git("add", "-A")
    committed.git("rm", "-rq", "--cached", ".repomarshal")
    assert d == committed.git("write-tree")


def test_tree_digest_preserves_user_state(committed: GitRepo, private_tmp: Path) -> None:
    committed.write("README.md", "staged\n")
    committed.git("add", "README.md")
    committed.write("src/app.py", "unstaged\n")
    committed.write("new.txt", "untracked\n")
    committed.delete(".gitignore")
    before = snapshot(committed)
    d1 = git.tree_digest(committed.root)
    d2 = git.tree_digest(committed.root)
    assert d1 == d2
    assert snapshot(committed) == before
    assert list(private_tmp.iterdir()) == []


def test_tree_digest_preserves_unborn_state(repo: GitRepo) -> None:
    repo.write("a.txt", "a\n")
    before = snapshot(repo)
    git.tree_digest(repo.root)
    assert snapshot(repo) == before
    assert not (repo.root / ".git" / "index").exists()


def test_tree_digest_preserves_partially_staged_index(committed: GitRepo) -> None:
    committed.write("README.md", "v2\n")
    committed.git("add", "README.md")
    committed.write("README.md", "v3\n")
    before = snapshot(committed)
    git.tree_digest(committed.root)
    assert snapshot(committed) == before
    assert committed.git("show", ":README.md") == "v2"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.write("README.md", "hello!\n"), id="staged-or-unstaged-content"),
        pytest.param(lambda r: r.write("new.txt", ""), id="untracked-empty-file"),
        pytest.param(lambda r: r.delete("src/app.py"), id="deletion"),
        pytest.param(lambda r: r.write("README.md", "hello\r\n"), id="one-byte"),
    ],
)
def test_tree_digest_changes_with_content(committed: GitRepo, mutate) -> None:
    before = git.tree_digest(committed.root)
    mutate(committed)
    assert git.tree_digest(committed.root) != before


def test_tree_digest_staged_vs_worktree(committed: GitRepo) -> None:
    """Only the working-tree content counts: staging alone does not change the digest."""
    committed.write("README.md", "changed\n")
    d_unstaged = git.tree_digest(committed.root)
    committed.git("add", "README.md")
    assert git.tree_digest(committed.root) == d_unstaged
    committed.git("rm", "-q", "--cached", "src/app.py")  # staged deletion, file still present
    assert git.tree_digest(committed.root) == d_unstaged


@pytest.mark.skipif(os.name != "posix", reason="exec bits and symlinks need POSIX")
def test_tree_digest_exec_bit_and_symlink(committed: GitRepo) -> None:
    base = git.tree_digest(committed.root)
    p = committed.root / "src" / "app.py"
    p.chmod(0o755)
    exec_digest = git.tree_digest(committed.root)
    assert exec_digest != base
    p.chmod(0o644)
    assert git.tree_digest(committed.root) == base

    (committed.root / "link").symlink_to("README.md")
    d1 = git.tree_digest(committed.root)
    (committed.root / "link").unlink()
    (committed.root / "link").symlink_to("src/app.py")
    d2 = git.tree_digest(committed.root)
    assert len({base, d1, d2}) == 3


def test_tree_digest_excludes_ignored_and_evidence(committed: GitRepo) -> None:
    base = git.tree_digest(committed.root)
    committed.write("build/out.bin", b"\0\1")
    committed.write("x.log", "log\n")
    committed.write(".repomarshal/evidence/1.json", "{}\n")
    committed.write(".repomarshal/plan.json", "{}\n")
    assert git.tree_digest(committed.root) == base


def test_tree_digest_ignores_edits_to_tracked_evidence(committed: GitRepo) -> None:
    committed.write(".repomarshal/evidence/old.json", "{}\n")
    committed.commit()
    base = git.tree_digest(committed.root)
    assert base == head_tree(committed)
    committed.write(".repomarshal/evidence/old.json", "changed\n")
    committed.write(".repomarshal/evidence/new.json", "{}\n")
    assert git.tree_digest(committed.root) == base


def test_tree_digest_does_not_exclude_similar_names(committed: GitRepo) -> None:
    base = git.tree_digest(committed.root)
    committed.write(".repomarshal-not/x", "x\n")
    assert git.tree_digest(committed.root) != base


def test_tree_digest_from_subdirectory_covers_whole_tree(committed: GitRepo) -> None:
    committed.write("top-level-new.txt", "x\n")
    committed.write("src/inner.txt", "y\n")
    assert git.tree_digest(committed.root / "src") == git.tree_digest(committed.root)
    assert git.tree_digest(committed.root / "src" / "app.py") == git.tree_digest(committed.root)


def test_tree_digest_unicode_and_spaces(committed: GitRepo) -> None:
    base = git.tree_digest(committed.root)
    committed.write("dir with space/naïve ünïcode 文件.txt", "x\n")
    assert git.tree_digest(committed.root) != base


def test_tree_digest_ignores_inherited_git_index_file(
    committed: GitRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "bogus-index"))
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    committed.write(".repomarshal/x", "x\n")
    assert git.tree_digest(committed.root) == head_tree(committed)
    assert not (tmp_path / "bogus-index").exists()


def test_tree_digest_temp_index_outside_repo_and_removed(
    committed: GitRepo, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = git._git

    def spy(repo, *args, env=None, **kw):
        if env and "GIT_INDEX_FILE" in env:
            seen.append(env["GIT_INDEX_FILE"])
            assert Path(env["GIT_INDEX_FILE"]).parent.exists()
        return real(repo, *args, env=env, **kw)

    monkeypatch.setattr(git, "_git", spy)
    committed.write("n.txt", "n\n")
    git.tree_digest(committed.root)
    assert seen and len(set(seen)) == 1
    idx = Path(seen[0])
    assert idx.is_relative_to(private_tmp.resolve()) or idx.is_relative_to(private_tmp)
    assert not idx.resolve().is_relative_to(committed.root.resolve())
    assert not idx.exists()
    assert list(private_tmp.iterdir()) == []


def test_tree_digest_temp_index_removed_on_error(
    committed: GitRepo, private_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = git._git

    def failing(repo, *args, **kw):
        if "write-tree" in args:
            raise GitError("boom")
        return real(repo, *args, **kw)

    monkeypatch.setattr(git, "_git", failing)
    before = snapshot(committed)
    with pytest.raises(GitError, match="boom"):
        git.tree_digest(committed.root)
    assert list(private_tmp.iterdir()) == []
    assert snapshot(committed) == before


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs an unreadable file (not root)"
)
def test_tree_digest_unreadable_file_raises_and_cleans_up(
    committed: GitRepo, private_tmp: Path
) -> None:
    p = committed.write("secret.txt", "s\n")
    p.chmod(0)
    try:
        with pytest.raises(GitError):
            git.tree_digest(committed.root)
    finally:
        p.chmod(0o644)
    assert list(private_tmp.iterdir()) == []


def test_tree_digest_refuses_temp_dir_inside_repo(
    committed: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    inside = committed.root / "tmp-inside"
    inside.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(inside))
    with pytest.raises(GitError, match="inside the repository"):
        git.tree_digest(committed.root)
    assert list(inside.iterdir()) == []


# --------------------------------------------------------------------------- collect_changes


def test_collect_changes_clean_tree(committed: GitRepo) -> None:
    cs = git.collect_changes(committed.root, committed.head())
    assert cs.files == ()
    assert cs.uncommitted is False
    assert cs.base == cs.head == committed.head()
    assert cs.tree_digest == head_tree(committed)


def test_collect_changes_committed_uncommitted_and_untracked(committed: GitRepo) -> None:
    base = committed.head()
    committed.git("switch", "-q", "-c", "feature")
    committed.write("committed.txt", "one\n")
    committed.commit("on branch")
    committed.write("README.md", "hello\nworld\n")  # unstaged modification
    committed.write("staged.txt", "s\n")
    committed.git("add", "staged.txt")
    committed.write("untracked.txt", "u\n")
    committed.delete("src/app.py")
    committed.write("build/ignored.o", "x\n")
    committed.write(".repomarshal/evidence/e.json", "{}\n")

    cs = git.collect_changes(committed.root, git.resolve_base(committed.root))
    assert cs.base == base
    assert cs.head == committed.head()
    assert cs.uncommitted is True
    assert cs.tree_digest == git.tree_digest(committed.root)
    assert cs.paths == (
        "README.md",
        "committed.txt",
        "src/app.py",
        "staged.txt",
        "untracked.txt",
    )
    f = by_path(cs)
    assert f["README.md"].status is FileStatus.MODIFIED
    assert f["README.md"].added == ("world",)
    assert f["README.md"].removed == ()
    assert f["committed.txt"].status is FileStatus.ADDED
    assert f["committed.txt"].added == ("one",)
    assert f["src/app.py"].status is FileStatus.DELETED
    assert f["src/app.py"].removed == ("print('hi')",)
    assert f["src/app.py"].added == ()
    assert f["staged.txt"].status is FileStatus.ADDED
    assert f["untracked.txt"].status is FileStatus.ADDED


def test_collect_changes_committed_only_is_not_uncommitted(committed: GitRepo) -> None:
    base = committed.head()
    committed.git("switch", "-q", "-c", "feature")
    committed.write("x.txt", "x\n")
    committed.commit()
    cs = git.collect_changes(committed.root, git.resolve_base(committed.root))
    assert cs.base == base
    assert cs.paths == ("x.txt",)
    assert cs.uncommitted is False


def test_collect_changes_ignored_only_is_not_uncommitted(committed: GitRepo) -> None:
    committed.write("build/a.o", "x\n")
    committed.write(".repomarshal/plan.json", "{}\n")
    cs = git.collect_changes(committed.root, committed.head())
    assert cs.files == ()
    assert cs.uncommitted is False


def test_collect_changes_rename(committed: GitRepo) -> None:
    body = "".join(f"line {i}\n" for i in range(20))
    committed.write("old name.py", body)
    base = committed.commit()
    (committed.root / "new").mkdir()
    committed.git("mv", "old name.py", "new/név.py")
    committed.write("new/név.py", body + "extra\n")
    cs = git.collect_changes(committed.root, base)
    assert len(cs.files) == 1
    f = cs.files[0]
    assert f.status is FileStatus.RENAMED
    assert f.path == "new/név.py"
    assert f.old_path == "old name.py"
    assert f.added == ("extra",)
    assert f.removed == ()


def test_collect_changes_unstaged_rename_detected(committed: GitRepo) -> None:
    body = "".join(f"row {i}\n" for i in range(20))
    committed.write("a.txt", body)
    base = committed.commit()
    (committed.root / "a.txt").rename(committed.root / "b.txt")
    cs = git.collect_changes(committed.root, base)
    assert [(f.status, f.path, f.old_path) for f in cs.files] == [
        (FileStatus.RENAMED, "b.txt", "a.txt")
    ]
    assert cs.files[0].added == cs.files[0].removed == ()


def test_collect_changes_rename_into_and_out_of_evidence_dir(committed: GitRepo) -> None:
    body = "".join(f"row {i}\n" for i in range(20))
    committed.write("keep.txt", body)
    committed.write(".repomarshal/was-evidence.txt", body.upper())
    base = committed.commit()
    committed.git("mv", "keep.txt", ".repomarshal/keep.txt")
    committed.git("mv", ".repomarshal/was-evidence.txt", "out.txt")
    committed.commit()
    cs = git.collect_changes(committed.root, base)
    f = by_path(cs)
    assert set(f) == {"keep.txt", "out.txt"}
    assert f["keep.txt"].status is FileStatus.DELETED and f["keep.txt"].old_path is None
    assert f["out.txt"].status is FileStatus.ADDED and f["out.txt"].old_path is None


def test_collect_changes_committed_evidence_never_appears(committed: GitRepo) -> None:
    base = committed.head()
    committed.write(".repomarshal/evidence/1.json", "{}\n")
    committed.write(".repomarshal/nested/deep/x", "x\n")
    committed.commit()
    committed.write(".repomarshal/evidence/2.json", "{}\n")
    cs = git.collect_changes(committed.root, base)
    assert cs.files == ()
    cs = git.collect_changes(committed.root, None)
    assert not any(p.startswith(".repomarshal") for p in cs.paths)


def test_collect_changes_binary(committed: GitRepo) -> None:
    committed.write("img.png", b"\x89PNG\r\n\x1a\n\0\0\0data")
    base = committed.commit()
    committed.write("img.png", b"\x89PNG\r\n\x1a\n\0\0\0other")
    committed.write("new.bin", bytes(range(256)))
    cs = git.collect_changes(committed.root, base)
    f = by_path(cs)
    assert f["img.png"].status is FileStatus.MODIFIED
    assert f["new.bin"].status is FileStatus.ADDED
    for name in ("img.png", "new.bin"):
        assert f[name].binary is True
        assert f[name].added == f[name].removed == ()
        assert f[name].truncated is False


def test_collect_changes_binary_deleted(committed: GitRepo) -> None:
    committed.write("blob.bin", b"\0\1\2")
    base = committed.commit()
    committed.delete("blob.bin")
    (f,) = git.collect_changes(committed.root, base).files
    assert (f.status, f.binary, f.added, f.removed) == (FileStatus.DELETED, True, (), ())


def test_collect_changes_line_cap(committed: GitRepo) -> None:
    n = git.MAX_LINES_PER_FILE
    committed.write("big.txt", "".join(f"old {i}\n" for i in range(n + 5)))
    base = committed.commit()
    committed.write("big.txt", "".join(f"new {i}\n" for i in range(n + 10)))
    committed.write("exact.txt", "".join(f"e {i}\n" for i in range(n)))
    committed.write("small.txt", "a\nb\n")
    cs = git.collect_changes(committed.root, base)
    f = by_path(cs)
    big = f["big.txt"]
    assert len(big.added) == n and len(big.removed) == n
    assert big.added[0] == "new 0" and big.added[-1] == f"new {n - 1}"
    assert big.removed[0] == "old 0"
    assert big.truncated is True
    assert len(f["exact.txt"].added) == n
    assert f["exact.txt"].truncated is False  # nothing was dropped
    assert f["small.txt"].added == ("a", "b") and f["small.txt"].truncated is False


def test_collect_changes_only_removed_lines_over_cap(committed: GitRepo) -> None:
    n = git.MAX_LINES_PER_FILE
    committed.write("big.txt", "".join(f"{i}\n" for i in range(n + 1)))
    base = committed.commit()
    committed.write("big.txt", "")
    (f,) = git.collect_changes(committed.root, base).files
    assert len(f.removed) == n and f.added == () and f.truncated is True


def test_collect_changes_lines_that_look_like_headers(committed: GitRepo) -> None:
    committed.write("t.txt", "-- a\n++ b\nkeep\n")
    base = committed.commit()
    committed.write("t.txt", "keep\n--- x\n+++ y\n@@ z\ndiff --git a b\n")
    (f,) = git.collect_changes(committed.root, base).files
    assert f.removed == ("-- a", "++ b")
    assert f.added == ("--- x", "+++ y", "@@ z", "diff --git a b")


def test_collect_changes_no_trailing_newline_and_crlf(committed: GitRepo) -> None:
    committed.write("n.txt", "a\nb")
    base = committed.commit()
    committed.write("n.txt", "a\nc")
    committed.write("crlf.txt", "x\r\ny\r\n")
    f = by_path(git.collect_changes(committed.root, base))
    assert f["n.txt"].added == ("c",) and f["n.txt"].removed == ("b",)
    assert f["crlf.txt"].added == ("x\r", "y\r")


def test_collect_changes_non_utf8_content_is_decoded(committed: GitRepo) -> None:
    committed.write("latin1.txt", "caf\xe9\n".encode("latin-1"))
    (f,) = git.collect_changes(committed.root, committed.head()).files
    assert f.binary is False
    assert f.added == ("caf�",)


def test_collect_changes_paths_with_spaces_and_unicode(committed: GitRepo) -> None:
    names = ["dir with space/a b.txt", "ünï/文件.md", 'quote"and\ttab.txt', "z-émoji-🙂.txt"]
    for n in names:
        committed.write(n, "x\n")
    cs = git.collect_changes(committed.root, committed.head())
    assert set(cs.paths) == set(names)
    assert list(cs.paths) == sorted(cs.paths)
    assert all(f.added == ("x",) for f in cs.files)


def test_collect_changes_sorted_by_path(committed: GitRepo) -> None:
    for n in ["zeta.txt", "Alpha.txt", "a/b.txt", "a.txt", "mid/z.txt"]:
        committed.write(n, n + "\n")
    cs = git.collect_changes(committed.root, committed.head())
    assert list(cs.paths) == sorted(cs.paths)


def test_collect_changes_base_none_everything_added(committed: GitRepo) -> None:
    committed.write("extra.txt", "e\n")
    cs = git.collect_changes(committed.root, None)
    assert cs.base is None
    assert cs.head == committed.head()
    assert set(cs.paths) == {".gitignore", "README.md", "src/app.py", "extra.txt"}
    assert all(f.status is FileStatus.ADDED for f in cs.files)
    assert by_path(cs)["README.md"].added == ("hello",)
    assert cs.uncommitted is True


def test_collect_changes_unborn(repo: GitRepo) -> None:
    cs = git.collect_changes(repo.root, git.resolve_base(repo.root))
    assert (cs.base, cs.head, cs.files, cs.uncommitted) == (None, None, (), False)
    assert cs.tree_digest == EMPTY_TREE

    repo.write("a.txt", "a\n")
    repo.write("b/c.txt", "c\n")
    cs = git.collect_changes(repo.root, git.resolve_base(repo.root))
    assert cs.base is None and cs.head is None
    assert cs.paths == ("a.txt", "b/c.txt")
    assert all(f.status is FileStatus.ADDED for f in cs.files)
    assert cs.uncommitted is True


@pytest.mark.skipif(os.name != "posix", reason="exec bits and symlinks need POSIX")
def test_collect_changes_mode_and_symlink_changes(committed: GitRepo) -> None:
    (committed.root / "target-link").symlink_to("README.md")
    base = committed.commit()
    (committed.root / "src" / "app.py").chmod(0o755)
    (committed.root / "target-link").unlink()
    (committed.root / "target-link").symlink_to("src/app.py")
    f = by_path(git.collect_changes(committed.root, base))
    assert f["src/app.py"].status is FileStatus.MODIFIED
    assert f["src/app.py"].added == f["src/app.py"].removed == ()
    assert f["target-link"].status is FileStatus.MODIFIED
    assert f["target-link"].added == ("src/app.py",)
    assert f["target-link"].removed == ("README.md",)


@pytest.mark.skipif(os.name != "posix", reason="symlinks need POSIX")
def test_collect_changes_type_change_is_modified(committed: GitRepo) -> None:
    base = committed.head()
    committed.delete("README.md")
    (committed.root / "README.md").symlink_to("src/app.py")
    (f,) = git.collect_changes(committed.root, base).files
    assert f.path == "README.md"
    assert f.status is FileStatus.MODIFIED
    assert f.removed == ("hello",)
    assert f.added == ("src/app.py",)


def test_collect_changes_from_subdirectory(committed: GitRepo) -> None:
    committed.write("top.txt", "t\n")
    cs_sub = git.collect_changes(committed.root / "src", committed.head())
    cs_root = git.collect_changes(committed.root, committed.head())
    assert cs_sub == cs_root
    assert cs_sub.paths == ("top.txt",)


def test_collect_changes_preserves_user_state(committed: GitRepo) -> None:
    committed.write("README.md", "staged\n")
    committed.git("add", "README.md")
    committed.write("new.txt", "n\n")
    before = snapshot(committed)
    git.collect_changes(committed.root, committed.head())
    assert snapshot(committed) == before


def test_collect_changes_bad_base_raises(committed: GitRepo) -> None:
    with pytest.raises(GitError):
        git.collect_changes(committed.root, "no-such-rev")


def test_collect_changes_deterministic(committed: GitRepo) -> None:
    committed.write("a.txt", "a\n")
    committed.write("bin", b"\0")
    assert git.collect_changes(committed.root, None) == git.collect_changes(committed.root, None)


# --------------------------------------------------------------------------- read helpers


def test_read_helpers(committed: GitRepo) -> None:
    sha = committed.head()
    committed.write("README.md", "worktree\n")
    assert git.read_at(committed.root, sha, "README.md") == b"hello\n"
    assert git.read_at(committed.root, sha, "missing") is None
    assert git.read_at(committed.root, None, "README.md") is None
    assert git.list_files_at(committed.root, sha) == (".gitignore", "README.md", "src/app.py")
    assert git.list_files_at(committed.root, None) == ()
    assert git.read_worktree(committed.root, "README.md") == b"worktree\n"
    assert git.read_worktree(committed.root, "missing") is None
    assert git.read_worktree(committed.root, "../outside") is None
