"""Gate decisions (owner: agent "planner"; roadmap M6, M14 reason text; architecture §4.2).

decide_gates must implement the pseudo-code and RULES table of architecture §4.2, with these
precisions (the contract):
- Every inventory gate gets exactly one GateDecision (gate_id set). A kind with no inventory gate
  gets one kind-level decision (gate_id None). Output order: model.KIND_ORDER, then gate id.
- Each kind's rule yields (needed, trigger_files, reasons). trigger_files None means "all scopes"
  (e.g. triggered only by task.* signals or by widening).
- Relevant gates of a needed kind: g.scope == "" or g.covers(f) for some trigger file. If a
  WIDENING signal is present, every gate of kinds FORMAT/LINT/TYPECHECK/UNIT is relevant.
- needed & relevant & available -> REQUIRED; needed & relevant & not available -> UNAVAILABLE
  (reason includes unavailable_reason); needed & no relevant gate -> kind-level NOT_APPLICABLE
  for kinds in model.WHEN_CONFIGURED, else kind-level UNAVAILABLE; gates of a needed kind that
  are not relevant -> SKIPPED "no changed files under <scope>"; not needed -> every gate SKIPPED
  with the reason naming the missing trigger, and no gates -> kind-level NOT_APPLICABLE.
- task.* signals count as triggers (add-only), with trigger_files None.
- M14: a REQUIRED gate with definition_changed=True keeps REQUIRED and gets an extra reason
  "gate definition changed in this change (<source>); its result will be UNKNOWN".
- Every decision has at least one Reason. Reasons cite signal ids where one applies.

Implementation notes (precisions within the contract):
- A kind can get both gate decisions and one kind-level decision: when it is needed but none of
  its gates is relevant (Scenario C: frontend:runtime SKIPPED plus kind-level runtime
  UNAVAILABLE). Within a kind, gate decisions come first (by id), then the kind-level one.
- Widening makes FORMAT/LINT/TYPECHECK/UNIT needed as well as relevant in every scope: a
  manifest or unknown file can change what those gates check.
- Elevated risk triggers secrets. The trigger files are the elevated signal's files, except for
  task.* and WIDENING signals, which trigger every scope.
- Integration (field test, docs/evaluation.md): elevated risk on its own does not demand
  integration tests, which cannot verify a CI, agent-config or policy edit. Integration is needed
  for security.auth_path, db.migration or dependency.changed (and their task.* analogues); for a
  test-tamper signal (every scope, as for widening); or when risk is elevated AND code.changed
  fires, scoped to the code.changed files. CI/agent/policy edits alone still widen the cheap and
  standard gates and still require the secrets gate.
- task.* triggers (TASK_TRIGGERS below) map task domains onto kinds; they can only add a
  trigger, so task text can never remove a requirement (threat T8).

v0.2 taint (threat T2): every REQUIRED or UNAVAILABLE gate decision of a test-running kind
(TEST_RUNNING_KINDS) whose gate covers a file of a tamper signal (signals.TAMPER_SIGNALS) gets
one taint Reason per covered signal note, e.g. "tests.deleted: backend/tests/test_refresh.py
removes test_rotation; this gate's result cannot vouch for the change". A root-scope gate covers
every file. tests.discovery_narrowed from a repository-root file (no directory) taints the
test-running gates of every scope: runners read config from parent directories. The gate stays
REQUIRED, so it still runs and its evidence shows the real exit code; api.done reports its
result as UNKNOWN. Kind-level decisions (no gate) are not tainted: they are already UNKNOWN or
not counted.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from repomarshal.model import (
    KIND_ORDER,
    WHEN_CONFIGURED,
    ChangeSet,
    DecisionStatus,
    Gate,
    GateDecision,
    GateKind,
    Inventory,
    Reason,
    Risk,
    Signal,
    max_risk,
)
from repomarshal.signals import TAMPER_SIGNALS, WIDENING, tamper_notes

# Kinds whose gates are widened to every scope by a WIDENING signal.
WIDENED_KINDS: frozenset[GateKind] = frozenset(
    {GateKind.FORMAT, GateKind.LINT, GateKind.TYPECHECK, GateKind.UNIT}
)

# Kinds whose gates run tests, and so can be fooled by deleted, skipped or undiscovered tests.
TEST_RUNNING_KINDS: frozenset[GateKind] = frozenset(
    {GateKind.UNIT, GateKind.INTEGRATION, GateKind.RUNTIME, GateKind.A11Y}
)
TAINT_SUFFIX = "this gate's result cannot vouch for the change"
TAINTED_STATUSES: frozenset[DecisionStatus] = frozenset(
    {DecisionStatus.REQUIRED, DecisionStatus.UNAVAILABLE}
)

# Diff signals that trigger each kind (files-scoped triggers).
DIFF_TRIGGERS: dict[GateKind, tuple[str, ...]] = {
    GateKind.DOCS: ("docs.changed",),
    GateKind.FORMAT: ("code.changed", "tests.changed"),
    GateKind.LINT: ("code.changed", "tests.changed"),
    GateKind.TYPECHECK: ("code.changed", "tests.changed"),
    GateKind.UNIT: ("code.changed", "tests.changed"),
    GateKind.INTEGRATION: ("security.auth_path", "db.migration", "dependency.changed"),
    GateKind.SECRETS: ("dependency.changed", "agent.config_changed"),  # + security.*, risk
    GateKind.DEP_AUDIT: ("dependency.changed",),
    GateKind.SECURITY_SCAN: ("security.auth_path",),
    GateKind.SKILL_AUDIT: ("agent.skill_changed",),
    GateKind.RUNTIME: ("frontend.ui",),  # + security.auth_path with code.changed
    GateKind.A11Y: ("frontend.ui",),
}

# Task signals that trigger each kind (all scopes; add-only).
TASK_TRIGGERS: dict[GateKind, tuple[str, ...]] = {
    GateKind.DOCS: ("task.docs",),
    GateKind.UNIT: ("task.tests",),
    GateKind.INTEGRATION: ("task.data", "task.dependencies", "task.security"),  # + elevated+code
    GateKind.SECRETS: ("task.security", "task.dependencies"),
    GateKind.DEP_AUDIT: ("task.dependencies",),
    GateKind.SECURITY_SCAN: ("task.security",),
    GateKind.RUNTIME: ("task.frontend",),
    GateKind.A11Y: ("task.frontend",),
}

# The "missing trigger" text for SKIPPED / NOT_APPLICABLE decisions of a kind that is not needed.
NOT_NEEDED: dict[GateKind, str] = {
    GateKind.DOCS: "no docs.changed signal",
    GateKind.FORMAT: "no code.changed or tests.changed signal",
    GateKind.LINT: "no code.changed or tests.changed signal",
    GateKind.TYPECHECK: "no code.changed or tests.changed signal",
    GateKind.UNIT: "no code.changed or tests.changed signal",
    GateKind.INTEGRATION: (
        "no security.auth_path, db.migration, dependency.changed or test-tamper signal, and no "
        "code.changed under elevated risk"
    ),
    GateKind.SECRETS: (
        "risk is not elevated and no security.*, dependency.changed or agent.config_changed signal"
    ),
    GateKind.DEP_AUDIT: "no dependency.changed signal",
    GateKind.SECURITY_SCAN: "no security.auth_path signal",
    GateKind.SKILL_AUDIT: "no agent.skill_changed signal",
    GateKind.RUNTIME: "no frontend.ui signal, and no security.auth_path with code.changed",
    GateKind.A11Y: "no frontend.ui signal",
}


@dataclass(frozen=True)
class _Trigger:
    signal: str
    files: frozenset[str] | None  # None = every scope
    text: str  # used when files is None, or for a kind-level decision
    always_text: bool = False  # use text for scoped gates too (files are not the signal's own)


def assess_risk(signals: tuple[Signal, ...]) -> Risk:
    """Highest signal risk (model.max_risk); LOW when there are no signals."""
    return max_risk([s.risk for s in signals])


def domains_of(signals: tuple[Signal, ...]) -> tuple[str, ...]:
    """Sorted unique domains of the signals (task.* signals included)."""
    return tuple(sorted({s.domain for s in signals}))


def _is_task(s: Signal) -> bool:
    return s.id.startswith("task.")


def _file_trigger(s: Signal, what: str) -> _Trigger:
    if _is_task(s):
        return _Trigger(s.id, None, f"{s.id} from task text requires {what}")
    return _Trigger(s.id, frozenset(s.files), f"{s.id} requires {what}")


def _triggers(kind: GateKind, by_id: dict[str, Signal], risk: Risk) -> list[_Trigger]:
    out: dict[str, _Trigger] = {}
    for sid in DIFF_TRIGGERS.get(kind, ()) + TASK_TRIGGERS.get(kind, ()):
        if sid in by_id:
            out[sid] = _file_trigger(by_id[sid], kind.value)

    if kind is GateKind.SECRETS:
        for sid, s in by_id.items():
            if sid.startswith("security."):
                out.setdefault(sid, _file_trigger(s, kind.value))

    if kind is GateKind.RUNTIME and "security.auth_path" in by_id and "code.changed" in by_id:
        auth, code = by_id["security.auth_path"], by_id["code.changed"]
        files = frozenset(auth.files) & frozenset(code.files) or frozenset(auth.files)
        out.setdefault(
            "security.auth_path",
            _Trigger(
                "security.auth_path",
                files,
                "security.auth_path together with code.changed requires a runtime check",
            ),
        )

    if kind is GateKind.INTEGRATION:
        _integration_triggers(out, by_id, risk)

    if kind is GateKind.SECRETS and risk is Risk.ELEVATED:
        for sid, s in by_id.items():
            if s.risk is not Risk.ELEVATED or sid in out:
                continue
            files = None if _is_task(s) or sid in WIDENING else frozenset(s.files)
            src = " (from task text)" if _is_task(s) else ""
            out[sid] = _Trigger(sid, files, f"risk is elevated by {sid}{src}; requires {kind}")

    if kind in WIDENED_KINDS:
        for sid in sorted(by_id):
            if sid in WIDENING and sid not in out:
                out[sid] = _Trigger(sid, None, f"{sid} widens {kind} to every scope")

    return [out[k] for k in sorted(out)]


def _integration_triggers(out: dict[str, _Trigger], by_id: dict[str, Signal], risk: Risk) -> None:
    """Integration beyond DIFF_TRIGGERS: a test-tamper signal (every scope), or elevated risk
    together with code.changed (the code files). Elevated risk alone (a CI, agent-config or
    policy edit) does not demand integration tests: they cannot verify such an edit."""
    for sid in TAMPER_SIGNALS:
        if sid in by_id and sid not in out:
            out[sid] = _Trigger(sid, None, f"{sid} requires integration")
    code = by_id.get("code.changed")
    if risk is not Risk.ELEVATED or code is None:
        return
    for sid, s in by_id.items():
        if s.risk is not Risk.ELEVATED or sid in out:
            continue
        src = " (from task text)" if _is_task(s) else ""
        out[sid] = _Trigger(
            sid,
            frozenset(code.files),
            f"risk is elevated by {sid}{src} and code.changed; requires integration",
            always_text=True,
        )


def _covers(g: Gate, t: _Trigger) -> bool:
    return t.files is None or g.scope == "" or any(g.covers(f) for f in t.files)


def _scope_label(scope: str) -> str:
    return scope if scope else "the repository root scope"


def _gate_reason(g: Gate, t: _Trigger) -> Reason:
    # a root-scope gate covers every changed file
    if t.files is None or g.scope == "" or t.always_text:
        return Reason(t.text, t.signal)
    return Reason(f"{t.signal} in {g.scope}", t.signal)


def _unavailable_text(g: Gate) -> str:
    if g.unavailable_reason:
        return f"gate cannot run: {g.unavailable_reason}"
    tool = g.argv[0] if g.argv else g.id
    return f"gate cannot run: {tool} not found"


def _decide_kind(kind: GateKind, gates: list[Gate], triggers: list[_Trigger]) -> list[GateDecision]:
    out: list[GateDecision] = []
    if not triggers:
        why = NOT_NEEDED[kind]
        for g in gates:
            out.append(GateDecision(kind, DecisionStatus.SKIPPED, (Reason(why),), g.id))
        if not gates:
            out.append(
                GateDecision(
                    kind, DecisionStatus.NOT_APPLICABLE, (Reason(f"{why}; none configured"),)
                )
            )
        return out

    any_relevant = False
    for g in gates:
        hit = [t for t in triggers if _covers(g, t)]
        if not hit:
            out.append(
                GateDecision(
                    kind,
                    DecisionStatus.SKIPPED,
                    (Reason(f"no changed files under {_scope_label(g.scope)}"),),
                    g.id,
                )
            )
            continue
        any_relevant = True
        reasons = tuple(_gate_reason(g, t) for t in hit)
        if not g.available:
            reasons += (Reason(_unavailable_text(g)),)
            out.append(GateDecision(kind, DecisionStatus.UNAVAILABLE, reasons, g.id))
            continue
        if g.definition_changed:
            reasons += (
                Reason(
                    f"gate definition changed in this change ({g.source}); "
                    "its result will be UNKNOWN"
                ),
            )
        out.append(GateDecision(kind, DecisionStatus.REQUIRED, reasons, g.id))

    if not any_relevant:
        reasons = tuple(Reason(t.text, t.signal) for t in triggers)
        if kind in WHEN_CONFIGURED:
            status = DecisionStatus.NOT_APPLICABLE
            tail = f"no {kind} tool configured for the changed files"
        else:
            status = DecisionStatus.UNAVAILABLE
            tail = f"no {kind} gate or provider configured for the changed files"
        out.append(GateDecision(kind, status, reasons + (Reason(tail),)))
    return out


def _taint(g: Gate, tamper: list[Signal]) -> tuple[Reason, ...]:
    out: list[Reason] = []
    for s in tamper:
        for note, paths in tamper_notes(s):
            root_config = s.id == "tests.discovery_narrowed" and any("/" not in p for p in paths)
            if root_config or any(g.covers(p) for p in paths):
                out.append(Reason(f"{s.id}: {note}; {TAINT_SUFFIX}", s.id))
    return tuple(dict.fromkeys(out))


def _with_taint(
    decisions: list[GateDecision], gates: dict[str, Gate], signals: dict[str, Signal]
) -> list[GateDecision]:
    tamper = [signals[sid] for sid in TAMPER_SIGNALS if sid in signals]
    if not tamper:
        return decisions
    out = []
    for d in decisions:
        g = gates.get(d.gate_id) if d.gate_id else None
        if g is not None and d.kind in TEST_RUNNING_KINDS and d.status in TAINTED_STATUSES:
            taint = _taint(g, tamper)
            if taint:
                d = dataclasses.replace(d, taint=d.taint + taint)
        out.append(d)
    return out


def decide_gates(
    signals: tuple[Signal, ...], inventory: Inventory, change: ChangeSet
) -> tuple[GateDecision, ...]:
    """Deterministic gate decisions; see module docstring."""
    by_id = {s.id: s for s in sorted(signals, key=lambda s: s.id)}
    risk = assess_risk(signals)
    out: list[GateDecision] = []
    for kind in KIND_ORDER:
        gates = sorted((g for g in inventory.gates if g.kind == kind), key=lambda g: g.id)
        out.extend(_decide_kind(kind, gates, _triggers(kind, by_id, risk)))
    return tuple(_with_taint(out, {g.id: g for g in inventory.gates}, by_id))
