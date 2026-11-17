"""`repomarshal status`: is the latest verdict still valid for the working tree? (ADR-004,
architecture §5.3).

Reads the evidence records under .repomarshal/evidence/ and reports:
- record digest: the newest record's `record_digest` recomputes from its own content;
- chain: every record from the first one that carries `prev_digest` names the record before it
  (records written before the chain existed are counted as legacy, not checked);
- tree: FRESH when the current tree digest equals the newest record's `tree_digest_after`,
  STALE otherwise. A STALE verdict counts as no evidence.

Exit code: the newest verdict's code (0 PASS, 1 FAIL, 2 UNKNOWN, 3 BLOCKED) only when the record
is intact, the chain is unbroken and the tree is FRESH; otherwise 2. 4 on an internal error.
Like `done`, a local status is advisory: these digests catch accidents, not an attacker who can
rewrite the files (architecture §7.5).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from repomarshal import evidence, git
from repomarshal.model import (
    EXIT_INTERNAL_ERROR,
    GitError,
    NotAGitRepository,
    Verdict,
    sha256_json,
)

FRESH, STALE, NO_EVIDENCE, INVALID = "FRESH", "STALE", "NO_EVIDENCE", "INVALID"


def _digest_ok(rec: dict[str, Any] | None) -> bool:
    if rec is None or not isinstance(rec.get("record_digest"), str):
        return False
    body = {k: v for k, v in rec.items() if k != "record_digest"}
    return sha256_json(body) == rec["record_digest"]


def _chain(files: list[Path], recs: list[dict[str, Any] | None]) -> dict[str, Any]:
    start = next((i for i, r in enumerate(recs) if r is not None and "prev_digest" in r), None)
    breaks: list[str] = []
    if start is not None:
        for i in range(start, len(recs)):
            rec = recs[i]
            prev = recs[i - 1] if i > 0 else None
            expected = prev.get("record_digest") if prev is not None else None
            if i > 0 and prev is None:
                breaks.append(f"{files[i - 1].name}: unreadable record")
            elif rec is None:
                breaks.append(f"{files[i].name}: unreadable record")
            elif "prev_digest" not in rec or rec["prev_digest"] != expected:
                breaks.append(f"{files[i].name}: prev_digest does not name the record before it")
            elif not _digest_ok(rec):
                breaks.append(f"{files[i].name}: record_digest does not match its content")
    legacy = len(recs) if start is None else start
    return {"records": len(recs), "legacy": legacy, "ok": not breaks, "breaks": breaks}


def check(repo: str | Path = ".") -> dict[str, Any]:
    root = git.repo_root(repo)
    files = evidence.record_files(root)
    facts: dict[str, Any] = {
        "state": NO_EVIDENCE,
        "verdict": None,
        "evidence_path": None,
        "created_at": None,
        "record_digest_ok": None,
        "chain": {"records": 0, "legacy": 0, "ok": True, "breaks": []},
        "tree_digest_after": None,
        "tree_digest_now": None,
        "reasons": [],
    }
    if not files:
        facts["reasons"].append("no evidence records; run `repomarshal done`")
        return facts
    recs = [evidence.read_record(f) for f in files]
    latest = recs[-1]
    facts["evidence_path"] = str(files[-1])
    facts["chain"] = _chain(files, recs)
    facts["record_digest_ok"] = _digest_ok(latest)
    if latest is None or not facts["record_digest_ok"]:
        facts["state"] = INVALID
        facts["reasons"].append(
            "the newest record is unreadable or its record_digest does not match"
        )
        return facts
    facts["verdict"] = latest.get("verdict")
    facts["created_at"] = latest.get("created_at")
    facts["tree_digest_after"] = latest.get("tree_digest_after")
    base = latest.get("base")
    try:
        now = git.tree_digest(root, base=base if isinstance(base, str) else None)
    except GitError as e:
        facts["state"] = INVALID
        facts["reasons"].append(f"could not compute the current tree digest: {e}")
        return facts
    facts["tree_digest_now"] = now
    if now == facts["tree_digest_after"]:
        facts["state"] = FRESH
    else:
        facts["state"] = STALE
        facts["reasons"].append("the working tree changed since this verdict; it is not evidence")
    if not facts["chain"]["ok"]:
        facts["reasons"].append(
            "the evidence chain is broken: " + "; ".join(facts["chain"]["breaks"])
        )
    return facts


def exit_code(facts: dict[str, Any]) -> int:
    if facts["state"] != FRESH or not facts["chain"]["ok"]:
        return Verdict.UNKNOWN.exit_code
    try:
        return Verdict(facts["verdict"]).exit_code
    except ValueError:
        return Verdict.UNKNOWN.exit_code


def _short(digest: Any) -> str:
    return str(digest)[:12] if digest else "-"


def _text(facts: dict[str, Any]) -> str:
    chain = facts["chain"]
    if facts["state"] == NO_EVIDENCE:
        return "STATUS: NO EVIDENCE\n  " + "\n  ".join(facts["reasons"])
    lines = [
        f"EVIDENCE  {facts['evidence_path']}",
        f"  verdict: {facts['verdict'] or '-'}  (created {facts['created_at'] or '-'})",
        f"  record digest: {'ok' if facts['record_digest_ok'] else 'MISMATCH'}",
        f"  chain: {'ok' if chain['ok'] else 'BROKEN'} ({chain['records']} records, "
        f"{chain['legacy']} from before chaining)",
    ]
    lines += [f"    - {b}" for b in chain["breaks"]]
    if facts["tree_digest_now"] is not None:
        lines.append(
            f"  tree: {facts['state']} (now {_short(facts['tree_digest_now'])}, verified "
            f"{_short(facts['tree_digest_after'])})"
        )
    lines += [f"  ! {r}" for r in facts["reasons"]]
    head = facts["verdict"] if exit_code(facts) != Verdict.UNKNOWN.exit_code else None
    lines.append(f"STATUS: {head or 'UNKNOWN'} ({facts['state']})")
    lines.append(
        "Local status: advisory. The authoritative verdict is a re-run in CI or by a reviewer."
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    try:
        facts = check(getattr(args, "repo", "."))
    except NotAGitRepository as e:
        print(f"repomarshal status: {e}", file=sys.stderr)
        return Verdict.BLOCKED.exit_code
    except Exception:  # noqa: BLE001 - an internal error must never look like PASS
        traceback.print_exc()
        print("repomarshal status: internal error (exit 4)", file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    if getattr(args, "json", False):
        print(json.dumps({**facts, "exit_code": exit_code(facts)}, indent=2, sort_keys=True))
    else:
        print(_text(facts))
    return exit_code(facts)
