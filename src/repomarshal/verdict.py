"""Verdict folding (owner: lead; architecture §6; product-definition "Output").

Precedence BLOCKED > FAIL > UNKNOWN > PASS. REQUIRED gate results fold in directly; UNAVAILABLE
decisions fold in as UNKNOWN; SKIPPED / NOT_APPLICABLE never count; a plan with no REQUIRED and no
UNAVAILABLE decision is UNKNOWN ("nothing was verified"); tree changed during the run -> at least
UNKNOWN; WAIVED counts as PASS only when the policy permits.
"""

from __future__ import annotations

from repomarshal.model import GateResult, Plan, Verdict


def fold(
    plan: Plan,
    results: tuple[GateResult, ...],
    tree_changed: bool,
    waivers_permit_pass: bool = False,
) -> tuple[Verdict, tuple[str, ...]]:
    raise NotImplementedError
