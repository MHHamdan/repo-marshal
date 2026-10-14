"""Core data model: the Change Plan IR and evidence records (schema_version 0.1).

This module is the contract shared by every other module. It is stdlib-only and has no
behaviour beyond construction, ordering helpers and serialisation. See docs/architecture.md §2.

Conventions used everywhere:
- Paths are POSIX, relative to the repository root, without a leading "./".
- A scope is "" for the repository root, or a directory path with a trailing slash
  ("frontend/"). A file is in a scope when its path starts with the scope string.
- Gate ids are "<scope-name>:<kind>" where scope-name is the scope without the trailing slash, or
  "root"; native gates use "native:<name>"; policy gates use their [gates.<id>] key.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "0.1"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's well-known empty tree id


# --------------------------------------------------------------------------- enums


class FileStatus(enum.StrEnum):
    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"
    RENAMED = "R"


class Risk(enum.StrEnum):
    LOW = "low"
    STANDARD = "standard"
    ELEVATED = "elevated"

    @property
    def rank(self) -> int:
        return _RISK_RANK[self]


_RISK_RANK = {Risk.LOW: 0, Risk.STANDARD: 1, Risk.ELEVATED: 2}


def max_risk(risks: list[Risk] | tuple[Risk, ...]) -> Risk:
    """Highest risk in the sequence; LOW for an empty sequence."""
    return max(risks, key=lambda r: r.rank, default=Risk.LOW)


class GateKind(enum.StrEnum):
    DOCS = "docs"
    FORMAT = "format"
    LINT = "lint"
    TYPECHECK = "typecheck"
    UNIT = "unit"
    INTEGRATION = "integration"
    SECRETS = "secrets"
    DEP_AUDIT = "dep_audit"
    SECURITY_SCAN = "security_scan"
    SKILL_AUDIT = "skill_audit"
    RUNTIME = "runtime"
    A11Y = "a11y"


# Fixed evaluation order for gate decisions (architecture §4.2).
KIND_ORDER: tuple[GateKind, ...] = tuple(GateKind)

# Kinds whose absence is NOT_APPLICABLE ("no tool configured, no obligation").
# Every other kind, when needed but absent, is UNAVAILABLE (folds to UNKNOWN).
WHEN_CONFIGURED: frozenset[GateKind] = frozenset(
    {GateKind.DOCS, GateKind.FORMAT, GateKind.LINT, GateKind.TYPECHECK}
)


class Cost(enum.StrEnum):
    CHEAP = "cheap"
    STANDARD = "standard"
    EXPENSIVE = "expensive"


DEFAULT_COST: dict[GateKind, Cost] = {
    GateKind.DOCS: Cost.CHEAP,
    GateKind.FORMAT: Cost.CHEAP,
    GateKind.LINT: Cost.CHEAP,
    GateKind.SECRETS: Cost.CHEAP,
    GateKind.TYPECHECK: Cost.STANDARD,
    GateKind.UNIT: Cost.STANDARD,
    GateKind.INTEGRATION: Cost.EXPENSIVE,
    GateKind.DEP_AUDIT: Cost.EXPENSIVE,
    GateKind.SECURITY_SCAN: Cost.EXPENSIVE,
    GateKind.SKILL_AUDIT: Cost.EXPENSIVE,
    GateKind.RUNTIME: Cost.EXPENSIVE,
    GateKind.A11Y: Cost.EXPENSIVE,
}


class DecisionStatus(enum.StrEnum):
    REQUIRED = "REQUIRED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ResultStatus(enum.StrEnum):
    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    WAIVED = "WAIVED"


class Verdict(enum.StrEnum):
    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"

    @property
    def exit_code(self) -> int:
        return _VERDICT_EXIT[self]


_VERDICT_EXIT = {Verdict.PASS: 0, Verdict.FAIL: 1, Verdict.UNKNOWN: 2, Verdict.BLOCKED: 3}
EXIT_INTERNAL_ERROR = 4  # never confusable with PASS


class PlanMode(enum.StrEnum):
    FORECAST = "forecast"  # no diff yet: compiled from task text + repo metadata, advisory
    BINDING = "binding"  # compiled from the actual diff; what `done` evaluates


# --------------------------------------------------------------------------- errors


class RepoMarshalError(Exception):
    """Base class for structured, user-facing errors."""


class NotAGitRepository(RepoMarshalError):
    pass


class GitError(RepoMarshalError):
    pass


class PolicyInvalid(RepoMarshalError):
    """repomarshal.toml at BASE is invalid. Always yields BLOCKED, never a silent default."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


# --------------------------------------------------------------------------- change set


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: FileStatus
    old_path: str | None = None  # set for RENAMED
    added: tuple[str, ...] = ()  # added lines (text files only; capped per file)
    removed: tuple[str, ...] = ()  # removed lines (text files only; capped per file)
    binary: bool = False
    truncated: bool = False  # True when added/removed were capped


@dataclass(frozen=True)
class ChangeSet:
    base: str | None  # BASE commit sha; None means "no base" (empty tree)
    head: str | None  # HEAD commit sha; None on an unborn branch
    tree_digest: str  # git tree id of the working tree (architecture §5.1)
    files: tuple[ChangedFile, ...]  # BASE tree -> working tree, sorted by path
    uncommitted: bool  # True when the working tree differs from HEAD
    # Paths whose content RepoMarshal could not inspect (e.g. an embedded git repository, or
    # files hidden by ignore rules that are not part of BASE). Any entry makes the verdict
    # UNKNOWN: unseen content can never be verified.
    opaque: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)


# --------------------------------------------------------------------------- inventory


@dataclass(frozen=True)
class Gate:
    id: str
    kind: GateKind
    argv: tuple[str, ...]  # empty for native gates
    cwd: str  # repo-relative working directory ("" = root)
    scope: str  # "" or "dir/"
    cost: Cost
    source: str  # e.g. "frontend/package.json#scripts.lint", "repomarshal.toml#gates.sec"
    provider: str = "command"  # "command" | "native"
    available: bool = True
    unavailable_reason: str | None = None
    timeout_s: int = 600
    # M14: digest of the gate's definition at BASE, and whether the working tree differs.
    definition_digest: str | None = None
    definition_changed: bool = False
    # v0.6 (architecture §3.5): the root gate of the same kind that already runs this workspace
    # package gate's checks, and the evidence for it (read at BASE, confirmed in the worktree).
    covered_by: str | None = None
    coverage_evidence: str | None = None

    def covers(self, path: str) -> bool:
        return self.scope == "" or path.startswith(self.scope)


@dataclass(frozen=True)
class Inventory:
    gates: tuple[Gate, ...]
    scopes: tuple[str, ...]  # every detected scope, including "" when the root has a manifest

    def of_kind(self, kind: GateKind) -> tuple[Gate, ...]:
        return tuple(g for g in self.gates if g.kind == kind)


# --------------------------------------------------------------------------- policy


@dataclass(frozen=True)
class SignalRuleSpec:
    """A policy-defined signal rule ([[signals]] in repomarshal.toml). Add-only."""

    id: str  # must start with "custom."
    domain: str
    risk: Risk
    paths: tuple[str, ...]  # fnmatch-style globs over repo-relative paths ("**" allowed)


@dataclass(frozen=True)
class Policy:
    version: int = 1
    token_budget: int = 12_000
    skills_max: int = 3
    waivers_permit_pass: bool = False
    allow_author_waivers: bool = False
    gates: tuple[Gate, ...] = ()  # from [gates.<id>]; already validated
    signals: tuple[SignalRuleSpec, ...] = ()
    source: str = "default"  # "default" or "base:<sha>:repomarshal.toml"
    digest: str = ""  # sha256 of the raw file bytes at BASE, or of "default"


# --------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class Signal:
    id: str  # stable id, e.g. "security.auth_path"; task-derived ids start with "task."
    domain: str
    risk: Risk
    files: tuple[str, ...] = ()  # empty for task signals
    detail: str = ""


@dataclass(frozen=True)
class Reason:
    text: str
    signal: str | None = None  # signal id that caused this, if any


@dataclass(frozen=True)
class GateDecision:
    kind: GateKind
    status: DecisionStatus
    reasons: tuple[Reason, ...]
    gate_id: str | None = None  # None for a kind-level decision (no matching gate)
    # v0.2: reasons why this gate's result cannot be trusted even if it runs (e.g. the change
    # deletes or skips tests the gate runs, or narrows test discovery). A REQUIRED gate with a
    # non-empty taint still runs, but `done` reports its result as UNKNOWN.
    taint: tuple[Reason, ...] = ()


@dataclass(frozen=True)
class ContextItem:
    path: str
    why: str
    priority: int  # 1..4 (architecture §4.4)
    est_tokens: int  # bytes // 4, an estimate


@dataclass(frozen=True)
class ContextPlan:
    items: tuple[ContextItem, ...]
    excluded: tuple[ContextItem, ...]
    budget_tokens: int
    selected_tokens: int
    candidate_tokens: int
    method: str = "deterministic-heuristic"
    estimate: str = "bytes/4"


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    path: str  # repo-relative path to SKILL.md


@dataclass(frozen=True)
class SkillPick:
    name: str
    path: str
    matched_domains: tuple[str, ...]
    score: int
    trust: str  # "repo@base"


@dataclass(frozen=True)
class PolicyRef:
    source: str
    digest: str
    # v0.3: copied from the BASE policy so `done` can fold waivers without re-reading it.
    waivers_permit_pass: bool = False
    # v0.4: whether the PR author may grant a waiver (solo-maintainer repositories).
    allow_author_waivers: bool = False


@dataclass(frozen=True)
class Plan:
    mode: PlanMode
    task: str | None
    change: ChangeSet
    signals: tuple[Signal, ...]
    domains: tuple[str, ...]  # sorted, unique
    risk: Risk
    inventory: tuple[Gate, ...]
    gates: tuple[GateDecision, ...]
    context: ContextPlan
    skills: tuple[SkillPick, ...]
    evidence_level: str  # "standard" | "elevated"
    policy: PolicyRef
    generator: dict[str, str]
    schema_version: str = SCHEMA_VERSION
    plan_hash: str = ""  # filled by with_hash()

    def with_hash(self) -> Plan:
        body = to_dict(dataclasses.replace(self, plan_hash=""))
        body.pop("plan_hash", None)
        return dataclasses.replace(self, plan_hash=sha256_json(body))

    def required(self) -> tuple[GateDecision, ...]:
        return tuple(d for d in self.gates if d.status == DecisionStatus.REQUIRED)


# --------------------------------------------------------------------------- evidence


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    kind: GateKind
    status: ResultStatus
    reason: str
    argv: tuple[str, ...] = ()
    exit_code: int | None = None
    duration_ms: int = 0
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None
    output_tail: tuple[str, ...] = ()  # last lines, redacted
    tool: str | None = None  # v0.3: provider tool name and version, e.g. "skillspector 2.12.0"


class WaiverSource(enum.StrEnum):
    GITHUB_LABEL = "github-label"  # a PR label applied by an authorised human (CI only)
    GITHUB_REVIEW = "github-review"  # an approving review by a CODEOWNER (CI only)
    LOCAL_CLI = "local-cli"  # `repomarshal waive` at an interactive terminal (local only)


@dataclass(frozen=True)
class Waiver:
    """A human decision to accept an unverifiable gate (MISSION.md §54).

    Waivers apply only to gates whose outcome is UNKNOWN or UNAVAILABLE; a FAIL is negative
    evidence and is never waivable. A waiver counts towards PASS only when the BASE policy sets
    completion.waivers_permit_pass; it is always listed in the evidence record.
    """

    target: str  # a gate id ("backend:unit") or a kind for kind-level decisions ("integration")
    reason: str  # non-empty, human-written
    identity: str  # who granted it (GitHub login, or local git/OS identity)
    source: WaiverSource
    granted_at: str  # UTC ISO-8601
    reference: str = ""  # e.g. the label event / review URL, or the local waiver file path
    tree_digest: str | None = None  # local waivers bind to the tree they were granted for


@dataclass(frozen=True)
class EvidenceRecord:
    plan_hash: str
    base: str | None
    head: str | None
    tree_digest_before: str
    tree_digest_after: str
    policy_digest: str
    results: tuple[GateResult, ...]
    verdict: Verdict
    verdict_reasons: tuple[str, ...]
    repomarshal_version: str
    created_at: str  # UTC ISO-8601
    advisory: str = (
        "Local verdict: advisory for the agent loop. The authoritative verdict is a re-run of "
        "`repomarshal done` where the agent cannot write (CI or a reviewer)."
    )
    waivers: tuple[Waiver, ...] = ()  # v0.3: every waiver that was considered
    schema_version: str = SCHEMA_VERSION
    record_digest: str = ""

    def with_digest(self) -> EvidenceRecord:
        body = to_dict(dataclasses.replace(self, record_digest=""))
        body.pop("record_digest", None)
        return dataclasses.replace(self, record_digest=sha256_json(body))


# --------------------------------------------------------------------------- serialisation


def to_dict(obj: Any) -> Any:
    """Convert dataclasses/enums/tuples recursively into JSON-ready structures."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    return obj


def canonical_json(data: Any) -> str:
    """Sorted keys, no insignificant whitespace, UTF-8 safe. Used for every digest."""
    return json.dumps(to_dict(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(data: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
