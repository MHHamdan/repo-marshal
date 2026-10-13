"""Determinism, dry runs and evidence binding (architecture §2, §5; api.done contract)."""

from __future__ import annotations

import json
import re

import jsonschema

from repomarshal.model import Verdict

from .conftest import FRONTEND_GATES, TASK_C, load_schema

HEX40 = re.compile(r"[0-9a-f]{40}")


# 11 --------------------------------------------------------------------------------------------
def test_plan_is_deterministic(webapp):
    webapp.scenario_c()
    first = webapp.plan(TASK_C)
    second = webapp.plan(TASK_C)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", first.plan_hash)
    assert first.plan_hash == second.plan_hash
    assert first == second


def test_plan_hash_is_the_documented_content_hash(webapp):
    webapp.scenario_b()
    plan = webapp.plan()
    assert plan.with_hash().plan_hash == plan.plan_hash


# 12 --------------------------------------------------------------------------------------------
def test_dry_run_runs_no_gate_and_is_not_pass(webapp):
    task = webapp.scenario_b()
    webapp.marker_stub("npm")
    res = webapp.done(task, dry_run=True)
    assert not webapp.ran("npm"), "dry run executed a gate"
    assert res.verdict != Verdict.PASS
    assert res.verdict == Verdict.UNKNOWN
    assert res.record is None
    assert res.evidence_path is None
    assert set(FRONTEND_GATES) <= set(res.would_run)
    assert not (webapp.root / ".repomarshal" / "evidence").exists() or not any(
        (webapp.root / ".repomarshal" / "evidence").iterdir()
    )


def test_cli_dry_run_runs_no_gate_and_is_not_pass(webapp):
    task = webapp.scenario_b()
    webapp.marker_stub("npm")
    proc = webapp.cli("done", "--task", task, "--dry-run", "--json")
    assert not webapp.ran("npm"), "dry run executed a gate"
    assert proc.returncode != 0
    data = json.loads(proc.stdout)
    assert data["verdict"] != "PASS"
    assert data["record"] is None


# 13 --------------------------------------------------------------------------------------------
def test_evidence_is_bound_to_the_tree(webapp):
    task = webapp.scenario_a()
    res = webapp.done(task)
    rec = res.record
    assert rec is not None and res.plan is not None
    assert HEX40.fullmatch(rec.tree_digest_before), rec.tree_digest_before
    assert rec.tree_digest_before == rec.tree_digest_after
    assert rec.tree_digest_before == res.plan.change.tree_digest
    assert rec.plan_hash == res.plan.plan_hash
    assert rec.base == webapp.base
    assert rec.policy_digest == res.plan.policy.digest


def test_evidence_file_written_and_valid(webapp):
    task = webapp.scenario_a()
    res = webapp.done(task)
    assert res.evidence_path is not None
    path = res.evidence_path
    if not path.is_absolute():
        path = webapp.root / path
    assert path.is_file()
    assert ".repomarshal/evidence" in path.as_posix()
    data = json.loads(path.read_text())
    jsonschema.Draft202012Validator(load_schema("evidence")).validate(data)
    assert data["record_digest"] == res.record.record_digest
    assert data["tree_digest_after"] == res.record.tree_digest_after


def test_writing_evidence_does_not_change_the_tree_digest(webapp):
    task = webapp.scenario_a()
    first = webapp.done(task)
    second = webapp.plan(task)
    assert first.record is not None
    assert second.change.tree_digest == first.record.tree_digest_after
