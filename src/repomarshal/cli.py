"""Command line (owner: lead; roadmap M10, M11; ADR-003).

    repomarshal plan [TASK] [--repo PATH] [--base REF] [--json] [--explain]
        exit 0 when a plan was produced; 3 BLOCKED (invalid policy, not a git repo); 4 internal.
        --json prints model.to_dict(plan) (validates against schemas/plan.schema.json).
    repomarshal done [--task TEXT] [--repo PATH] [--base REF] [--json] [--dry-run]
        exit = verdict code: 0 PASS, 1 FAIL, 2 UNKNOWN, 3 BLOCKED, 4 internal error.
        --json prints {"verdict", "reasons", "evidence_path", "record"} where record validates
        against schemas/evidence.schema.json (null on dry-run / blocked-before-plan).
Human output ends `done` with the advisory line from EvidenceRecord.advisory.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError
