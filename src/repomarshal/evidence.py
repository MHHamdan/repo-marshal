"""Evidence records (owner: lead; ADR-004). Records are outputs only: nothing in RepoMarshal reads
them back to decide a verdict. `latest_digest` reads the newest one only to chain the next record
to it (`prev_digest`); `repomarshal status` reads them to report integrity and staleness."""

from __future__ import annotations

import json
import time
from pathlib import Path

from repomarshal import anchor
from repomarshal.model import EvidenceRecord, Plan, RepoMarshalError, to_dict

EVIDENCE_DIR = ".repomarshal/evidence"
PLANS_DIR = ".repomarshal/plans"
MAX_RECORD_BYTES = 4 * 1024 * 1024


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


def record_files(repo: Path) -> list[Path]:
    """Evidence record files, oldest first. Names start with a microsecond UTC stamp, so name order
    is write order (records from before the stamp had microseconds still sort by second)."""
    d = Path(repo).resolve() / EVIDENCE_DIR
    if d.is_symlink() or not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if not p.name.endswith(".tmp"))


def read_record(path: Path) -> dict | None:
    """A record file as JSON, or None: bounded read, no symlinks, regular files only."""
    data = anchor.read_small_json(path, limit=MAX_RECORD_BYTES)
    return data if isinstance(data, dict) else None


def latest_digest(repo: Path) -> str | None:
    """record_digest of the newest record, for the next record's prev_digest. None when there is
    none or it is unreadable; `status` then reports the chain as broken at the next record."""
    files = record_files(repo)
    rec = read_record(files[-1]) if files else None
    digest = rec.get("record_digest") if rec else None
    return digest if isinstance(digest, str) else None


def write_record(repo: Path, record: EvidenceRecord) -> Path:
    d = _safe_dir(repo, EVIDENCE_DIR)
    now = time.time()
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now)) + f".{int(now * 1e6) % 1_000_000:06d}Z"
    name = f"{stamp}-{_hex(record.plan_hash)[:12]}-{_hex(record.record_digest)[:8]}.json"
    return _write_json(d / name, to_dict(record))
