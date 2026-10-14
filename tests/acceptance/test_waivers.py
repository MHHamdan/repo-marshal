"""Human override end to end (MISSION.md §54): Scenario C with waivers.

Zero-config Scenario C is UNKNOWN because security_scan and runtime are UNAVAILABLE. Waivers granted
on GitHub by a CODEOWNER turn that into PASS only when BASE's policy permits it; a FAIL stays FAIL
and `--waivers none` ignores every waiver.
"""

from __future__ import annotations

import argparse
import sys

import pytest

from repomarshal import api, waivers
from repomarshal.model import ResultStatus, Verdict, WaiverSource
from tests.unit.test_waivers import (
    OWNER,
    FakeGitHub,
    comment,
    github_env,
    label,
    write_event,
)

from .conftest import Webapp, result

PERMIT_POLICY = "version = 1\n\n[completion]\nwaivers_permit_pass = true\n"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "127.0.0.1,localhost")


@pytest.fixture
def gh():
    with FakeGitHub() as fake:
        yield fake


def _webapp(tmp_path, permit: bool) -> Webapp:
    w = Webapp(tmp_path, policy=PERMIT_POLICY if permit else None)
    w.commit_on_base(".github/CODEOWNERS", f"* @{OWNER}\n", "BASE: CODEOWNERS")
    return w


def _grant(gh: FakeGitHub, *targets: str) -> None:
    gh.pr(
        events=[label(t, OWNER, n=i + 1) for i, t in enumerate(targets)],
        comments=[
            comment(OWNER, "\n".join(f"repomarshal-waive {t}: lab offline" for t in targets))
        ],
    )


def _done(w: Webapp, gh: FakeGitHub, tmp_path, mode: str = "github") -> api.DoneResult:
    env = github_env(w.env, gh.url, write_event(tmp_path / "event.json"))
    return api.done(w.root, task=w.scenario_c(), env=env, waivers=mode)


def test_zero_config_c_is_unknown_without_waivers(tmp_path, gh):
    w = _webapp(tmp_path, permit=True)
    gh.pr()
    res = _done(w, gh, tmp_path)
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.record is not None and res.record.waivers == ()


def test_codeowner_label_waivers_give_pass_when_base_permits(tmp_path, gh):
    w = _webapp(tmp_path, permit=True)
    _grant(gh, "security_scan", "runtime")
    res = _done(w, gh, tmp_path)
    assert res.verdict == Verdict.PASS, res.reasons
    assert res.record is not None
    assert {(x.target, x.source) for x in res.record.waivers} == {
        ("security_scan", WaiverSource.GITHUB_LABEL),
        ("runtime", WaiverSource.GITHUB_LABEL),
    }
    assert any("WAIVED by maint" in r for r in res.reasons)


def test_waivers_stay_unknown_without_permit(tmp_path, gh):
    w = _webapp(tmp_path, permit=False)
    _grant(gh, "security_scan", "runtime")
    res = _done(w, gh, tmp_path)
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.record is not None and len(res.record.waivers) == 2
    assert any("policy does not let waivers count" in r for r in res.reasons)


def test_permit_in_worktree_only_does_not_count(tmp_path, gh):
    w = _webapp(tmp_path, permit=False)
    w.repo.write("repomarshal.toml", PERMIT_POLICY)  # the change itself flips the switch
    _grant(gh, "security_scan", "runtime")
    assert _done(w, gh, tmp_path).verdict != Verdict.PASS


def test_fail_plus_waiver_stays_fail(tmp_path, gh):
    w = _webapp(tmp_path, permit=True)
    w.stub("python", exit_code=1)  # backend:unit fails
    _grant(gh, "security_scan", "runtime", "backend:unit")
    res = _done(w, gh, tmp_path)
    assert res.verdict == Verdict.FAIL, res.reasons
    assert result(res.record, "backend:unit").status == ResultStatus.FAIL


def test_waivers_none_ignores_every_waiver(tmp_path, gh):
    w = _webapp(tmp_path, permit=True)
    _grant(gh, "security_scan", "runtime")
    res = _done(w, gh, tmp_path, mode="none")
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.record is not None and res.record.waivers == ()
    assert gh.requests == []


def test_cli_done_waivers_none_ignores_local_waivers(tmp_path, monkeypatch):
    w = _webapp(tmp_path, permit=True)
    w.scenario_c()
    _local_waive(w, monkeypatch, "security_scan")
    _local_waive(w, monkeypatch, "runtime")
    r = w.cli("done", "--waivers", "none", "--json")
    assert r.returncode == Verdict.UNKNOWN.exit_code, r.stdout + r.stderr


# --------------------------------------------------------------------------- local mode


def _local_waive(w: Webapp, monkeypatch, target: str, typed: str | None = None) -> int:
    for var in ("CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(var, raising=False)
    w.repo.git("config", "user.email", "human@example.invalid")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": typed if typed else target)
    args = argparse.Namespace(gate=target, reason="no scanner licence", repo=str(w.root))
    return waivers.cli_waive(args)


def _local_env(w: Webapp) -> dict[str, str]:
    return {k: v for k, v in w.env.items() if not k.startswith("GITHUB_") and k != "CI"}


def test_local_waive_then_done_local_passes(tmp_path, monkeypatch, capsys):
    w = _webapp(tmp_path, permit=True)
    task = w.scenario_c()
    assert _local_waive(w, monkeypatch, "security_scan") == 0
    assert _local_waive(w, monkeypatch, "runtime") == 0
    files = sorted((w.root / ".repomarshal" / "waivers").glob("*.json"))
    assert len(files) == 2
    res = api.done(w.root, task=task, env=_local_env(w), waivers="local")
    assert res.verdict == Verdict.PASS, res.reasons
    assert {x.identity for x in res.record.waivers} == {
        f"human@example.invalid (os user {waivers.getpass.getuser()})"
    }
    # The same waivers are ignored in GitHub mode and once the tree changes.
    w.append("backend/app/auth/refresh.py", "\n# another edit\n")
    res = api.done(w.root, task=task, env=_local_env(w), waivers="local")
    assert res.verdict == Verdict.UNKNOWN, res.reasons
    assert res.record.waivers == ()


def test_local_waive_wrong_confirmation_writes_nothing(tmp_path, monkeypatch, capsys):
    w = _webapp(tmp_path, permit=True)
    w.scenario_c()
    assert _local_waive(w, monkeypatch, "runtime", typed="security_scan") == 1
    assert "did not match" in capsys.readouterr().err
    assert not list((w.root / ".repomarshal").glob("waivers/*.json"))


def test_local_waive_unknown_target_refused(tmp_path, monkeypatch, capsys):
    w = _webapp(tmp_path, permit=True)
    w.scenario_c()
    assert _local_waive(w, monkeypatch, "frontend:a11y") == 1
    assert "not a waivable target" in capsys.readouterr().err
