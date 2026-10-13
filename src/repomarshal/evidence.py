"""Evidence records (owner: lead; ADR-004). Records are outputs only: nothing in RepoMarshal reads
them back to decide a verdict."""

from __future__ import annotations

import json
import time
from pathlib import Path

from repomarshal.model import EvidenceRecord, Plan, RepoMarshalError, to_dict

EVIDENCE_DIR = ".repomarshal/evidence"
PLANS_DIR = ".repomarshal/plans"


def _safe_dir(repo: Path, rel: str) -> Path:
    root = Path(repo).resolve()
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    if not d.resolve().is_relative_to(root):
        raise RepoMarshalError(f"{rel} resolves outside the repository; refusing to write")
    return d


def _write_json(path: Path, data: dict) -> Path:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _hex(digest: str) -> str:
    return digest.split(":", 1)[-1]


def write_plan(repo: Path, plan: Plan) -> Path:
    d = _safe_dir(repo, PLANS_DIR)
    return _write_json(d / f"{_hex(plan.plan_hash)[:16]}.json", to_dict(plan))


def write_record(repo: Path, record: EvidenceRecord) -> Path:
    d = _safe_dir(repo, EVIDENCE_DIR)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"{stamp}-{_hex(record.plan_hash)[:12]}-{_hex(record.record_digest)[:8]}.json"
    return _write_json(d / name, to_dict(record))
