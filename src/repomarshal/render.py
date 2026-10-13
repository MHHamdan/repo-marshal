"""Human-readable output (owner: lead). Machine output is model.to_dict; this is presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repomarshal.model import DecisionStatus, Plan

if TYPE_CHECKING:
    from repomarshal.api import DoneResult

_STATUS_LABEL = {
    DecisionStatus.REQUIRED: "required",
    DecisionStatus.SKIPPED: "skipped",
    DecisionStatus.NOT_APPLICABLE: "not applicable",
    DecisionStatus.UNAVAILABLE: "UNAVAILABLE",
}


def plan_text(p: Plan, explain: bool = False) -> str:
    out: list[str] = []
    ch = p.change
    out.append(f"CHANGE ANALYSIS ({p.mode.value})")
    out.append(
        f"  base {ch.base or 'none'}  head {ch.head or 'none'}"
        f"  files {len(ch.files)}{'  (uncommitted)' if ch.uncommitted else ''}"
    )
    out.append(f"  domains: {', '.join(p.domains) or '-'}")
    out.append(f"  risk: {p.risk.value.upper()}")
    for s in p.signals:
        files = (
            f" ({', '.join(s.files[:3])}{', ...' if len(s.files) > 3 else ''})" if s.files else ""
        )
        out.append(f"    signal {s.id} [{s.risk.value}]{files}")
    out.append("")
    c = p.context
    out.append(f"CONTEXT ({c.method}; sizes are {c.estimate} estimates)")
    for item in c.items:
        out.append(f"  P{item.priority} {item.path}  ~{item.est_tokens} tok  ({item.why})")
    out.append(
        f"  selected ~{c.selected_tokens} of ~{c.candidate_tokens} candidate tokens"
        f" (budget {c.budget_tokens}); excluded {len(c.excluded)}"
    )
    out.append("")
    out.append("SKILLS")
    if p.skills:
        for sk in p.skills:
            out.append(f"  {sk.name}  ({sk.path}; matched {', '.join(sk.matched_domains)})")
    else:
        out.append("  none recommended")
    out.append("")
    out.append("GATES")
    for d in p.gates:
        label = d.gate_id or f"{d.kind.value} (no gate)"
        out.append(f"  {label:<26} {d.kind.value:<14} {_STATUS_LABEL[d.status]}")
        reasons = d.reasons if explain else d.reasons[:1]
        for r in reasons:
            cite = f" [{r.signal}]" if r.signal else ""
            out.append(f"      - {r.text}{cite}")
    out.append("")
    out.append(f"EVIDENCE POLICY\n  level: {p.evidence_level}\n  policy: {p.policy.source}")
    out.append(f"\nplan {p.plan_hash}")
    return "\n".join(out)


def done_text(r: DoneResult) -> str:
    out: list[str] = ["TASK COMPLETION"]
    if r.plan is not None and r.record is not None:
        for res in r.record.results:
            out.append(f"  {res.gate_id:<26} {res.status.value:<8} {res.reason}")
    if r.would_run:
        out.append("  would run: " + ", ".join(r.would_run))
    out.append("")
    out.append(f"OVERALL  {r.verdict.value}")
    for reason in r.reasons:
        out.append(f"  - {reason}")
    if r.evidence_path:
        out.append(f"\nevidence: {r.evidence_path}")
    return "\n".join(out)
