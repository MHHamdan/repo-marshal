"""`repomarshal doctor` (owner: agent "hosts"; roadmap SHOULD #5): read-only facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import doctor, git
from tests.acceptance.conftest import Webapp

INVALID_POLICY = 'version = 1\n\n[gates.security]\nkind = "security_scan"\nargv = "bandit -r app"\n'
TOP_KEYS = {
    "repomarshal_version",
    "python",
    "git",
    "repository",
    "policy",
    "scopes",
    "gates",
    "providers",
    "plannable",
    "problems",
}


@pytest.fixture(name="webapp")
def webapp_fixture(tmp_path: Path) -> Webapp:
    """The zero-config webapp acceptance fixture with passing stub tools."""
    return Webapp(tmp_path)


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def _doctor_json(w: Webapp, *extra: str) -> tuple[int, dict]:
    proc = w.cli("doctor", "--json", *extra)
    assert proc.returncode in (0, 3), proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def test_doctor_reports_the_webapp(webapp):
    code, f = _doctor_json(webapp)
    assert code == 0
    assert set(f) == TOP_KEYS
    assert f["plannable"] is True
    rep = f["repository"]
    assert rep["is_git"] and rep["branch"] == "main"
    assert rep["head"] == webapp.base and rep["base"] == webapp.base
    assert rep["base_resolution"].startswith("automatic")
    assert rep["uncommitted"] is False and rep["changed_paths"] == 0
    assert f["policy"] == {"status": "default", "source": "default", "errors": []}
    assert set(f["scopes"]) >= {"frontend/", "backend/"}
    ids = {g["id"] for g in f["gates"]}
    assert {"frontend:lint", "backend:unit", "native:docs-links", "native:secrets-diff"} <= ids
    assert all(g["available"] for g in f["gates"]), f["gates"]
    assert f["git"]["found"] is True and f["git"]["version"]
    assert f["python"]["version"].count(".") == 2


def test_doctor_is_read_only_and_runs_no_gate(webapp):
    for tool in ("npm", "ruff", "mypy", "python"):
        webapp.marker_stub(tool)
    webapp.scenario_c()  # an uncommitted change, so a gate run would have work to do
    digest = git.tree_digest(webapp.root)
    files = _files(webapp.root)  # includes .git (index, refs, objects)

    code, f = _doctor_json(webapp)
    assert code == 0
    assert f["repository"]["uncommitted"] is True and f["repository"]["changed_paths"] == 1
    assert not (webapp.root / ".repomarshal").exists()
    assert git.tree_digest(webapp.root) == digest
    assert _files(webapp.root) == files
    for tool in ("npm", "ruff", "mypy", "python"):
        assert not webapp.ran(tool), tool

    # The human-readable form is read-only as well.
    assert webapp.cli("doctor").returncode == 0
    assert not (webapp.root / ".repomarshal").exists()
    assert _files(webapp.root) == files


def test_doctor_reports_unavailable_tools(webapp):
    webapp.remove_stub("mypy")
    code, f = _doctor_json(webapp)
    assert code == 0  # a missing tool does not stop planning; the plan reports it
    gate = next(g for g in f["gates"] if g["id"] == "backend:typecheck")
    assert gate["available"] is False
    assert "mypy not found on PATH" in gate["unavailable_reason"]
    assert any("backend:typecheck" in p for p in f["problems"])


def test_doctor_invalid_policy_exits_3(webapp):
    webapp.commit_on_base("repomarshal.toml", INVALID_POLICY)
    code, f = _doctor_json(webapp)
    assert code == 3
    assert f["plannable"] is False
    assert f["policy"]["status"] == "invalid"
    assert any("gates.security.argv" in e for e in f["policy"]["errors"])
    text = webapp.cli("doctor")
    assert text.returncode == 3
    assert "gates.security.argv" in text.stdout


def test_doctor_valid_policy(webapp):
    webapp.commit_on_base(
        "repomarshal.toml",
        'version = 1\n[gates.sec]\nkind = "security_scan"\nargv = ["bandit"]\nscope = "backend/"\n',
    )
    code, f = _doctor_json(webapp)
    assert code == 0
    assert f["policy"]["status"] == "valid"
    assert f["policy"]["source"] == f"base:{webapp.base}:repomarshal.toml"
    sec = next(g for g in f["gates"] if g["id"] == "sec")
    assert sec["available"] is False  # no bandit stub on PATH


def test_doctor_flags_an_uncommitted_policy(webapp):
    webapp.repo.write("repomarshal.toml", "version = 1\n")
    code, f = _doctor_json(webapp)
    assert code == 0
    assert f["policy"]["status"] == "default"
    assert any("not at BASE" in p for p in f["problems"])


def test_doctor_not_a_git_repository(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    code = doctor.run(argparse.Namespace(repo=str(plain), json=True))
    assert code == 3
    f = json.loads(capsys.readouterr().out)
    assert set(f) == TOP_KEYS
    assert f["repository"]["is_git"] is False
    assert f["plannable"] is False
    assert f["gates"] is None
    assert list(plain.iterdir()) == []


def test_doctor_unborn_repository_is_plannable(tmp_path, capsys):
    r = GitRepo(tmp_path / "r")
    r.write("a.txt", "x\n")
    code = doctor.run(argparse.Namespace(repo=str(r.root), json=True))
    f = json.loads(capsys.readouterr().out)
    assert code == 0, f
    assert f["repository"]["head"] is None and f["repository"]["base"] is None
    assert f["repository"]["uncommitted"] is True


def test_doctor_providers_detected_never_claimed(webapp):
    webapp.stub("playwright")
    _, f = _doctor_json(webapp)
    prov = f["providers"]
    assert set(prov) == {"mcp_extra", "skillspector", "playwright", "reticle"}
    assert prov["playwright"]["found"] is True
    assert prov["skillspector"]["found"] is False and prov["reticle"]["found"] is False
    assert all(set(v) <= {"name", "found", "path", "module"} for v in prov.values())
    text = webapp.cli("doctor").stdout
    assert "not a claim of support" in text


def test_doctor_internal_error_exits_4(monkeypatch, capsys, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "collect", boom)
    assert doctor.run(argparse.Namespace(repo=str(tmp_path), json=True)) == 4
    assert capsys.readouterr().out == ""
