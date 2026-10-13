"""Native baseline gates (owner: agent "planner"; roadmap M7; architecture §3.2).

Deterministic, offline, labelled as baseline checks. They never read outside the repository.
"""

from __future__ import annotations

import posixpath
import re
import time
from pathlib import Path
from urllib.parse import unquote

from repomarshal import git
from repomarshal.model import (
    ChangeSet,
    Cost,
    FileStatus,
    Gate,
    GateKind,
    GateResult,
    ResultStatus,
)

DOCS_LINKS_ID = "native:docs-links"
SECRETS_DIFF_ID = "native:secrets-diff"


def native_gates() -> tuple[Gate, ...]:
    """The two native gates, always present in the inventory (contract: implemented by lead)."""
    return (
        Gate(
            id=DOCS_LINKS_ID,
            kind=GateKind.DOCS,
            argv=(),
            cwd="",
            scope="",
            cost=Cost.CHEAP,
            source="builtin",
            provider="native",
        ),
        Gate(
            id=SECRETS_DIFF_ID,
            kind=GateKind.SECRETS,
            argv=(),
            cwd="",
            scope="",
            cost=Cost.CHEAP,
            source="builtin",
            provider="native",
        ),
    )


BASELINE = "baseline check"
MAX_LISTED = 50  # cap on findings listed in a reason / output tail

MARKDOWN_EXTENSIONS: tuple[str, ...] = (".md", ".markdown")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
# Inline links and images: "](target" or "](<target>"; reference definitions: "[x]: target".
_INLINE_LINK_RE = re.compile(r"\]\(\s*(<[^>\n]*>|[^)\s]+)")
_REF_DEF_RE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>\n]*>|\S+)")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _listing(items: list[str]) -> str:
    shown = "; ".join(items[:MAX_LISTED])
    more = len(items) - MAX_LISTED
    return shown + (f"; and {more} more" if more > 0 else "")


def _links(text: str) -> list[tuple[int, str]]:
    """(line number, raw target) of every link outside fenced code blocks and code spans."""
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for no, line in enumerate(text.splitlines(), start=1):
        m = _FENCE_RE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                if not line.strip().lstrip(fence[0]):
                    fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        line = _INLINE_CODE_RE.sub("", line)
        for rm in _REF_DEF_RE.finditer(line):
            out.append((no, rm.group(1)))
        for lm in _INLINE_LINK_RE.finditer(line):
            out.append((no, lm.group(1)))
    return out


def _check_target(root: Path, md_path: str, raw: str) -> str | None:
    """None when the link is fine or out of scope; otherwise why it is broken."""
    target = raw[1:-1].strip() if raw.startswith("<") and raw.endswith(">") else raw
    if not target or target.startswith("#") or target.startswith("//"):
        return None
    if _SCHEME_RE.match(target):
        return None  # absolute URL, mailto:, data:, ...
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not target:
        return None
    if target.startswith("/"):
        joined = target.lstrip("/")  # repository-root relative
    else:
        joined = posixpath.join(posixpath.dirname(md_path), target)
    norm = posixpath.normpath(joined)
    if norm == ".." or norm.startswith("../") or "\0" in norm:
        return "escapes the repository"
    resolved = (root / norm).resolve()
    if not resolved.is_relative_to(root):
        return "escapes the repository"  # e.g. via a symlink; never touched further (T15)
    if not resolved.exists():
        return "not found"
    return None


def run_docs_links(repo: Path, change: ChangeSet) -> GateResult:
    """Relative links in changed/added Markdown files (*.md) must resolve to an existing file or
    directory inside the repository (working tree). Ignore absolute URLs (scheme://, mailto:),
    pure anchors (#x), and links inside fenced code blocks. Strip "#fragment" before resolving.
    Links that resolve outside the repo root are failures. PASS if all resolve, FAIL listing
    each "path:line -> target" otherwise. Deleted files are not checked.
    """
    start = time.monotonic()
    root = Path(repo).resolve()
    broken: list[str] = []
    unreadable: list[str] = []
    checked_files = checked_links = 0
    for f in change.files:
        if f.status == FileStatus.DELETED or not f.path.lower().endswith(MARKDOWN_EXTENSIONS):
            continue
        raw = git.read_worktree(root, f.path)
        if raw is None or f.binary:
            unreadable.append(f.path)
            continue
        checked_files += 1
        for no, target in _links(raw.decode("utf-8", "replace")):
            checked_links += 1
            why = _check_target(root, f.path, target)
            if why:
                broken.append(f"{f.path}:{no} -> {target} ({why})")

    if broken:
        status = ResultStatus.FAIL
        reason = f"{BASELINE}: {len(broken)} broken relative link(s): {_listing(broken)}"
    elif unreadable:
        status = ResultStatus.UNKNOWN
        reason = (
            f"{BASELINE}: could not read {len(unreadable)} changed Markdown file(s) inside the "
            f"repository: {_listing(unreadable)}"
        )
    elif checked_files == 0:
        status, reason = ResultStatus.PASS, f"{BASELINE}: no changed Markdown files"
    else:
        status = ResultStatus.PASS
        reason = (
            f"{BASELINE}: {checked_links} link(s) in {checked_files} changed Markdown file(s); "
            "every relative link resolves inside the repository"
        )
    return GateResult(
        gate_id=DOCS_LINKS_ID,
        kind=GateKind.DOCS,
        status=status,
        reason=reason,
        duration_ms=_elapsed_ms(start),
        output_tail=tuple(broken[:MAX_LISTED]),
    )


# Pattern name -> regex. Names (never matched text) appear in results.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    (
        "generic-secret-assignment",
        re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[\"'][^\"']{12,}[\"']"),
    ),
)


def run_secrets_diff(change: ChangeSet) -> GateResult:
    """High-confidence secret patterns in ADDED lines only: PEM private-key headers
    (-----BEGIN ... PRIVATE KEY-----), AWS access key ids (AKIA/ASIA + 16 [A-Z0-9]), GitHub
    tokens (ghp_/gho_/ghu_/ghs_/ghr_ + 36+ chars, github_pat_...), Slack tokens (xox[baprs]-),
    and generic `(api[_-]?key|secret|password|token)\\s*[:=]\\s*["'][^"']{12,}["']`.
    FAIL listing "path:pattern-name" (NEVER the matched secret text). PASS otherwise.
    If any changed file is binary or truncated, the result is UNKNOWN with that reason unless a
    secret was found (then FAIL).
    """
    start = time.monotonic()
    findings: list[str] = []
    unscanned: list[str] = []
    lines = 0
    for f in change.files:
        if f.binary or f.truncated:
            unscanned.append(f"{f.path} ({'binary' if f.binary else 'truncated'})")
        hits = set()
        for line in f.added:
            lines += 1
            for name, rx in SECRET_PATTERNS:
                if name not in hits and rx.search(line):
                    hits.add(name)
        findings.extend(f"{f.path}:{name}" for name, _rx in SECRET_PATTERNS if name in hits)

    if findings:
        status = ResultStatus.FAIL
        reason = f"{BASELINE}: possible secret(s) in added lines: {_listing(findings)}"
    elif unscanned:
        status = ResultStatus.UNKNOWN
        reason = (
            f"{BASELINE}: no secret pattern in {lines} added line(s), but "
            f"{len(unscanned)} file(s) could not be fully scanned: {_listing(unscanned)}"
        )
    else:
        status = ResultStatus.PASS
        reason = f"{BASELINE}: no high-confidence secret pattern in {lines} added line(s)"
    return GateResult(
        gate_id=SECRETS_DIFF_ID,
        kind=GateKind.SECRETS,
        status=status,
        reason=reason,
        duration_ms=_elapsed_ms(start),
        output_tail=tuple(findings[:MAX_LISTED]),
    )
