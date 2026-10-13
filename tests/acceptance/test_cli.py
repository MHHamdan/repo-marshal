"""The CLI contract (src/repomarshal/cli.py): JSON shapes, schemas, exit codes, advisory line.
Runs `sys.executable -m repomarshal` with PYTHONPATH pointing at this worktree's src/."""

from __future__ import annotations

import json

import jsonschema
import pytest

from repomarshal.model import Verdict

from .conftest import ADVISORY, TASK_C, Webapp, load_schema

INVALID_POLICY = 'version = 1\n\n[gates.security]\nkind = "security_scan"\nargv = "bandit -r app"\n'


def _validate(instance, schema_name):
    schema = load_schema(schema_name)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(instance)


def test_plan_json_validates_against_plan_schema(webapp):
    webapp.scenario_c()
    proc = webapp.cli("plan", TASK_C, "--json")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    _validate(data, "plan")
    assert data["mode"] == "binding"
    assert data["task"] == TASK_C


def test_plan_json_matches_api_plan_hash(webapp):
    webapp.scenario_c()
    proc = webapp.cli("plan", TASK_C, "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["plan_hash"] == webapp.plan(TASK_C).plan_hash


def test_done_json_record_validates_against_evidence_schema(webapp):
    task = webapp.scenario_a()
    proc = webapp.cli("done", "--task", task, "--json")
    data = json.loads(proc.stdout)
    assert set(data) >= {"verdict", "reasons", "evidence_path", "record"}
    assert data["record"] is not None
    _validate(data["record"], "evidence")
    assert data["record"]["verdict"] == data["verdict"]
    assert proc.returncode == Verdict(data["verdict"]).exit_code


@pytest.mark.parametrize(
    ("scenario", "verdict"),
    [
        ("scenario_a", Verdict.PASS),
        ("scenario_a_broken_link", Verdict.FAIL),
        ("scenario_c", Verdict.UNKNOWN),
    ],
)
def test_done_exit_code_matches_verdict(webapp, scenario, verdict):
    task = getattr(webapp, scenario)()
    proc = webapp.cli("done", "--task", task, "--json")
    data = json.loads(proc.stdout)
    assert data["verdict"] == verdict.value, data["reasons"]
    assert proc.returncode == verdict.exit_code


def test_cli_invalid_policy_is_blocked_exit_3(tmp_path):
    w = Webapp(tmp_path, policy=INVALID_POLICY)
    w.scenario_c()
    plan_proc = w.cli("plan", TASK_C)
    assert plan_proc.returncode == 3, plan_proc.stdout + plan_proc.stderr
    done_proc = w.cli("done", "--task", TASK_C, "--json")
    assert done_proc.returncode == 3, done_proc.stdout + done_proc.stderr
    data = json.loads(done_proc.stdout)
    assert data["verdict"] == "BLOCKED"


def test_cli_not_a_git_repository_is_blocked(webapp, tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    proc = webapp.cli("plan", repo=plain)
    assert proc.returncode == 3, proc.stdout + proc.stderr


def test_human_done_output_has_advisory_line(webapp):
    task = webapp.scenario_a()
    proc = webapp.cli("done", "--task", task)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ADVISORY in proc.stdout


def test_human_done_output_has_advisory_line_when_not_pass(webapp):
    task = webapp.scenario_c()
    proc = webapp.cli("done", "--task", task)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert ADVISORY in proc.stdout
