"""Evidence records (owner: lead; ADR-004). Records are outputs only: nothing in RepoMarshal reads
them back to decide a verdict."""

from __future__ import annotations

from pathlib import Path

from repomarshal.model import EvidenceRecord, Plan

EVIDENCE_DIR = ".repomarshal/evidence"
PLANS_DIR = ".repomarshal/plans"


def write_plan(repo: Path, plan: Plan) -> Path:
    raise NotImplementedError


def write_record(repo: Path, record: EvidenceRecord) -> Path:
    raise NotImplementedError
