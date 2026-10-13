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
"""

from __future__ import annotations

from repomarshal.model import ChangeSet, GateDecision, Inventory, Risk, Signal


def assess_risk(signals: tuple[Signal, ...]) -> Risk:
    """Highest signal risk (model.max_risk); LOW when there are no signals."""
    raise NotImplementedError


def domains_of(signals: tuple[Signal, ...]) -> tuple[str, ...]:
    """Sorted unique domains of the signals (task.* signals included)."""
    raise NotImplementedError


def decide_gates(
    signals: tuple[Signal, ...], inventory: Inventory, change: ChangeSet
) -> tuple[GateDecision, ...]:
    """Deterministic gate decisions; see module docstring."""
    raise NotImplementedError
