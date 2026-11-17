"""`repomarshal status` and the evidence hash chain (ADR-004, roadmap SHOULD #1)."""

from __future__ import annotations

import json

import jsonschema

from repomarshal import evidence, status
from repomarshal.model import Verdict, sha256_json

from .conftest import load_schema


def _files(webapp):
    return evidence.record_files(webapp.root)


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _rewrite(path, rec, redigest=False):
    if redigest:
        rec["record_digest"] = sha256_json({k: v for k, v in rec.items() if k != "record_digest"})
    path.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_no_evidence_is_unknown(webapp):
    facts = status.check(webapp.root)
    assert facts["state"] == status.NO_EVIDENCE
    assert status.exit_code(facts) == Verdict.UNKNOWN.exit_code


def test_fresh_pass_then_stale_after_an_edit(webapp):
    webapp.scenario_a()
    assert webapp.done().verdict is Verdict.PASS
    facts = status.check(webapp.root)
    assert facts["state"] == status.FRESH and facts["record_digest_ok"]
    assert status.exit_code(facts) == 0

    webapp.append("docs/install.md", "\nOne more line.\n")
    facts = status.check(webapp.root)
    assert facts["state"] == status.STALE
    assert status.exit_code(facts) == Verdict.UNKNOWN.exit_code


def test_records_chain_to_the_previous_record(webapp):
    webapp.scenario_a()
    webapp.done()
    webapp.done()
    first, second = (_load(p) for p in _files(webapp))
    assert first["prev_digest"] is None
    assert second["prev_digest"] == first["record_digest"]
    validator = jsonschema.Draft202012Validator(load_schema("evidence"))
    for rec in (first, second):
        validator.validate(rec)
    chain = status.check(webapp.root)["chain"]
    assert chain == {"records": 2, "legacy": 0, "ok": True, "breaks": []}


def test_an_edited_older_record_breaks_the_chain(webapp):
    webapp.scenario_a()
    webapp.done()
    webapp.done()
    older = _files(webapp)[0]
    rec = _load(older)
    rec["verdict"] = "FAIL"
    _rewrite(older, rec)
    facts = status.check(webapp.root)
    assert not facts["chain"]["ok"] and older.name in facts["chain"]["breaks"][0]
    assert facts["state"] == status.FRESH  # the newest record and the tree are still fine
    assert status.exit_code(facts) == Verdict.UNKNOWN.exit_code


def test_a_deleted_middle_record_breaks_the_chain(webapp):
    webapp.scenario_a()
    for _ in range(3):
        webapp.done()
    _files(webapp)[1].unlink()
    facts = status.check(webapp.root)
    assert not facts["chain"]["ok"]
    assert status.exit_code(facts) == Verdict.UNKNOWN.exit_code


def test_an_edited_newest_record_is_invalid(webapp):
    webapp.scenario_a()
    webapp.done()
    newest = _files(webapp)[-1]
    rec = _load(newest)
    rec["verdict"] = "PASS"
    rec["results"] = []
    _rewrite(newest, rec)
    facts = status.check(webapp.root)
    assert facts["state"] == status.INVALID and facts["record_digest_ok"] is False
    assert status.exit_code(facts) == Verdict.UNKNOWN.exit_code


def test_records_from_before_the_chain_are_legacy(webapp):
    webapp.scenario_a()
    webapp.done()
    old = _files(webapp)[0]
    rec = _load(old)
    del rec["prev_digest"]
    _rewrite(old, rec, redigest=True)  # what a 0.1.0 record looks like
    webapp.done()
    chain = status.check(webapp.root)["chain"]
    assert chain["ok"] and chain["legacy"] == 1 and chain["records"] == 2


def test_a_symlinked_record_is_not_read(webapp, tmp_path):
    webapp.scenario_a()
    webapp.done()
    newest = _files(webapp)[-1]
    outside = tmp_path / "outside.json"
    outside.write_text(newest.read_text(encoding="utf-8"), encoding="utf-8")
    newest.unlink()
    newest.symlink_to(outside)
    facts = status.check(webapp.root)
    assert facts["state"] == status.INVALID


def test_failing_verdict_keeps_its_exit_code_when_fresh(webapp):
    webapp.scenario_a_broken_link()
    assert webapp.done().verdict is Verdict.FAIL
    facts = status.check(webapp.root)
    assert facts["state"] == status.FRESH
    assert status.exit_code(facts) == Verdict.FAIL.exit_code


def test_cli_status(webapp):
    webapp.scenario_a()
    webapp.done()
    proc = webapp.cli("status")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "STATUS: PASS (FRESH)" in proc.stdout
    data = json.loads(webapp.cli("status", "--json").stdout)
    assert data["state"] == "FRESH" and data["exit_code"] == 0
    webapp.append("docs/install.md", "\nchanged\n")
    proc = webapp.cli("status")
    assert proc.returncode == 2 and "STALE" in proc.stdout
