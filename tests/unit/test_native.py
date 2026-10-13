"""Unit tests for the native baseline gates (roadmap M7; threat model T11, T15)."""

import os

import pytest

from helpers import GitRepo
from repomarshal.model import ChangedFile, ChangeSet, FileStatus, GateKind, ResultStatus
from repomarshal.native import (
    DOCS_LINKS_ID,
    SECRETS_DIFF_ID,
    native_gates,
    run_docs_links,
    run_secrets_diff,
)

M, A, D = FileStatus.MODIFIED, FileStatus.ADDED, FileStatus.DELETED


def cs(*files: ChangedFile) -> ChangeSet:
    return ChangeSet(None, None, "t" * 40, tuple(sorted(files, key=lambda f: f.path)), True)


def test_native_gates_unchanged():
    assert [g.id for g in native_gates()] == [DOCS_LINKS_ID, SECRETS_DIFF_ID]


# --------------------------------------------------------------------------- docs-links


@pytest.fixture
def repo(tmp_path):
    r = GitRepo(tmp_path / "repo")
    r.write("README.md", "# Top\n")
    r.write("docs/install.md", "x\n")
    r.write("docs/adr/0003-token-refresh.md", "x\n")
    r.write("backend/app/auth/refresh.py", "x = 1\n")
    r.write("assets/logo.png", b"\x89PNG")
    r.commit("base")
    return r


def _md(r: GitRepo, path: str, text: str, status: FileStatus = M) -> ChangeSet:
    r.write(path, text)
    return cs(ChangedFile(path, status))


def test_docs_links_pass(repo):
    text = """\
# Install

See [the ADR](adr/0003-token-refresh.md#context), [root readme](../README.md),
[code](../backend/app/auth/refresh.py), [a dir](adr/), ![logo](../assets/logo.png "Logo"),
[root-relative](/docs/install.md), [spaced](<adr/0003-token-refresh.md>).
[web](https://example.com/x.md), [mail](mailto:a@example.com), [anchor](#install),
[proto](//cdn.example.com/a.md), [query](install.md?plain=1).

[ref]: adr/0003-token-refresh.md
"""
    res = run_docs_links(repo.root, _md(repo, "docs/guide.md", text, A))
    assert res.status is ResultStatus.PASS, res.reason
    assert res.gate_id == DOCS_LINKS_ID and res.kind is GateKind.DOCS
    assert res.reason.startswith("baseline check")


def test_docs_links_ignores_fenced_and_inline_code(repo):
    text = """\
```md
[broken](missing.md)
```
~~~~
[broken](missing2.md)
```
still fenced [broken](missing3.md)
~~~~
Inline `[not a link](missing4.md)` is code.
"""
    res = run_docs_links(repo.root, _md(repo, "docs/guide.md", text))
    assert res.status is ResultStatus.PASS, res.reason


def test_docs_links_fail_lists_path_line_target(repo):
    text = "ok [a](install.md)\n\n[b](nope.md) and [c](adr/missing.md#x)\n[r]: gone/\n"
    res = run_docs_links(repo.root, _md(repo, "docs/guide.md", text))
    assert res.status is ResultStatus.FAIL
    assert res.output_tail == (
        "docs/guide.md:3 -> nope.md (not found)",
        "docs/guide.md:3 -> adr/missing.md#x (not found)",
        "docs/guide.md:4 -> gone/ (not found)",
    )
    assert "docs/guide.md:3 -> nope.md" in res.reason


def test_docs_links_escape_outside_repo_fails(repo, tmp_path):
    (tmp_path / "outside.md").write_text("secret")
    text = "[up](../../outside.md)\n[etc](../../../../../../etc/passwd)\n"
    res = run_docs_links(repo.root, _md(repo, "docs/guide.md", text))
    assert res.status is ResultStatus.FAIL
    assert all("escapes the repository" in line for line in res.output_tail)
    assert len(res.output_tail) == 2


def test_docs_links_symlink_escape_fails(repo, tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "x.md").write_text("x")
    os.symlink(tmp_path / "outside", repo.root / "docs" / "link")
    res = run_docs_links(repo.root, _md(repo, "docs/guide.md", "[x](link/x.md)\n"))
    assert res.status is ResultStatus.FAIL
    assert "escapes the repository" in res.output_tail[0]


def test_docs_links_skips_deleted_and_non_markdown(repo):
    change = cs(
        ChangedFile("docs/old.md", D),
        ChangedFile("backend/app/auth/refresh.py", M),
    )
    res = run_docs_links(repo.root, change)
    assert res.status is ResultStatus.PASS
    assert "no changed Markdown" in res.reason


def test_docs_links_unreadable_markdown_is_unknown(repo):
    res = run_docs_links(repo.root, cs(ChangedFile("docs/vanished.md", M)))
    assert res.status is ResultStatus.UNKNOWN


# --------------------------------------------------------------------------- secrets-diff

# Assembled at runtime so this file never contains a literal credential.
AWS = "AKIA" + "Q" * 16
GHP = "ghp_" + "a1B2" * 9
PAT = "github_pat_" + "x" * 30
SLACK = "xoxb-" + "1234567890-abcdef"
PEM = "-----BEGIN RSA " + "PRIVATE KEY-----"
GENERIC = 'api_key = "' + "s3cr3tv4lu3xyz" + '"'

SECRETS = [
    ("private-key", PEM),
    ("aws-access-key-id", f"key = {AWS}"),
    ("github-token", f"TOKEN={GHP}"),
    ("github-pat", PAT),
    ("slack-token", f"url: {SLACK}"),
    ("generic-secret-assignment", GENERIC),
]


@pytest.mark.parametrize("name,line", SECRETS)
def test_secrets_detected_without_echoing_secret(name, line):
    res = run_secrets_diff(cs(ChangedFile("app/settings.py", M, added=("x = 1", line))))
    assert res.status is ResultStatus.FAIL
    assert res.gate_id == SECRETS_DIFF_ID and res.kind is GateKind.SECRETS
    assert f"app/settings.py:{name}" in res.reason
    assert res.output_tail == (f"app/settings.py:{name}",)
    blob = res.reason + "".join(res.output_tail)
    for _n, secret_line in SECRETS:
        for token in (AWS, GHP, PAT, SLACK, "s3cr3tv4lu3xyz"):
            assert token not in blob
        assert secret_line not in blob


def test_secrets_removed_lines_are_ignored():
    res = run_secrets_diff(cs(ChangedFile("a.py", M, removed=(f"k = {AWS}",))))
    assert res.status is ResultStatus.PASS


@pytest.mark.parametrize(
    "line",
    [
        "password = get_password()",
        'token = ""',
        'api_key = "short"',
        "AKIA_EXAMPLE",
        "-----BEGIN PUBLIC KEY-----",
        "ghp_short",
    ],
)
def test_secrets_no_false_positive(line):
    assert run_secrets_diff(cs(ChangedFile("a.py", M, added=(line,)))).status is ResultStatus.PASS


def test_secrets_binary_or_truncated_is_unknown_unless_found():
    binary = ChangedFile("img.png", M, binary=True)
    trunc = ChangedFile("big.py", M, added=("x = 1",), truncated=True)
    res = run_secrets_diff(cs(binary, ChangedFile("a.py", M, added=("y = 2",))))
    assert res.status is ResultStatus.UNKNOWN
    assert "img.png (binary)" in res.reason
    res = run_secrets_diff(cs(trunc))
    assert res.status is ResultStatus.UNKNOWN and "big.py (truncated)" in res.reason
    found = run_secrets_diff(cs(binary, ChangedFile("a.py", A, added=(PEM,))))
    assert found.status is ResultStatus.FAIL


def test_secrets_multiple_files_sorted_and_deduplicated():
    res = run_secrets_diff(
        cs(
            ChangedFile("b.py", A, added=(AWS, AWS, PEM)),
            ChangedFile("a.py", A, added=(GHP,)),
        )
    )
    assert res.output_tail == ("a.py:github-token", "b.py:private-key", "b.py:aws-access-key-id")


def test_secrets_empty_change_passes():
    res = run_secrets_diff(cs())
    assert res.status is ResultStatus.PASS
    assert res.reason.startswith("baseline check")
