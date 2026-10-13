"""Native baseline gates (owner: agent "planner"; roadmap M7; architecture §3.2).

Deterministic, offline, labelled as baseline checks. They never read outside the repository.
"""

from __future__ import annotations

from pathlib import Path

from repomarshal.model import ChangeSet, Cost, Gate, GateKind, GateResult

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


def run_docs_links(repo: Path, change: ChangeSet) -> GateResult:
    """Relative links in changed/added Markdown files (*.md) must resolve to an existing file or
    directory inside the repository (working tree). Ignore absolute URLs (scheme://, mailto:),
    pure anchors (#x), and links inside fenced code blocks. Strip "#fragment" before resolving.
    Links that resolve outside the repo root are failures. PASS if all resolve, FAIL listing
    each "path:line -> target" otherwise. Deleted files are not checked.
    """
    raise NotImplementedError


def run_secrets_diff(change: ChangeSet) -> GateResult:
    """High-confidence secret patterns in ADDED lines only: PEM private-key headers
    (-----BEGIN ... PRIVATE KEY-----), AWS access key ids (AKIA/ASIA + 16 [A-Z0-9]), GitHub
    tokens (ghp_/gho_/ghu_/ghs_/ghr_ + 36+ chars, github_pat_...), Slack tokens (xox[baprs]-),
    and generic `(api[_-]?key|secret|password|token)\\s*[:=]\\s*["'][^"']{12,}["']`.
    FAIL listing "path:pattern-name" (NEVER the matched secret text). PASS otherwise.
    If any changed file is binary or truncated, the result is UNKNOWN with that reason unless a
    secret was found (then FAIL).
    """
    raise NotImplementedError
