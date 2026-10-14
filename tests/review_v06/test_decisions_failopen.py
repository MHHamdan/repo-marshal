"""Attacks on the v0.6 decision changes (decisions.py): the secrets gate is no longer required for
a lockfile-only dependency change (`_lockfile_only`, `_secrets_exemption`), and integration is no
longer required for dependency.changed.

The exemption's only content check is `_CREDENTIAL_URL` (decisions.py:103), a URL of the form
`scheme://user:pass@`. The native secrets gate (native.SECRET_PATTERNS) would have caught the
credentials below; with the exemption it is SKIPPED, so a credential an agent commits into a
lockfile reaches PASS unseen. Lockfile content is attacker-controlled text: nothing makes a file
named pnpm-lock.yaml or yarn.lock "generated".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from repomarshal import api
from repomarshal.model import DecisionStatus, Plan, Verdict
from tests.helpers import GitRepo, env_with_path, stub_bin

failopen = pytest.mark.failopen

SECRETS_GATE = "native:secrets-diff"
GH_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # fake, 36 chars after the prefix
AWS_KEY = "AKIA" + "Z7Q2W4E6R8T0Y1U3"  # fake

BASE_LOCK = (
    "lockfileVersion: '9.0'\n\npackages:\n\n  left-pad@1.3.0:\n"
    "    resolution: {integrity: sha512-AAAA}\n"
)


@dataclass
class Case:
    repo: GitRepo
    base: str
    env: dict[str, str]
    plan: Plan


def case_for(tmp_path: Path, work: dict[str, str], base_lock: str = BASE_LOCK) -> Case:
    repo = GitRepo(tmp_path / "proj")
    repo.write(
        "package.json",
        json.dumps({"name": "p", "packageManager": "pnpm@9.1.0",
                    "dependencies": {"left-pad": "1.3.0"}, "scripts": {"test": "vitest run"}}),
    )  # fmt: skip
    repo.write("pnpm-lock.yaml", base_lock)
    repo.write("src/index.ts", "export const x = 1;\n")
    # A configured dependency auditor, so dep_audit can PASS and the verdict can be PASS.
    repo.write(
        "repomarshal.toml", 'version = 1\n[gates.audit]\nkind = "dep_audit"\nargv = ["audit"]\n'
    )
    base = repo.commit("BASE")
    for path, text in work.items():
        repo.write(path, text)
    bindir = tmp_path / "bin"
    for tool in ("pnpm", "npm", "audit"):
        stub_bin(bindir, tool)
    env = env_with_path(bindir, keep_system=False)
    return Case(repo, base, env, api.plan(repo.root, base=base, env=env))


def plan_for(tmp_path: Path, work: dict[str, str]) -> Plan:
    return case_for(tmp_path, work).plan


def status(plan: Plan, gate_id: str) -> DecisionStatus:
    return next(d.status for d in plan.gates if d.gate_id == gate_id)


def explain(plan: Plan) -> str:
    rows = [f"{d.gate_id or d.kind}: {d.status}" for d in plan.gates]
    rows += [f"  {r.text}" for d in plan.gates if d.gate_id == SECRETS_GATE for r in d.reasons]
    rows += [f"signal {s.id}: {', '.join(s.files)}" for s in plan.signals]
    return "\n".join(rows)


def lock_with(line: str) -> str:
    return BASE_LOCK + f"\n  evil@1.0.0:\n    resolution: {line}\n"


@failopen
@pytest.mark.parametrize(
    "line",
    [
        # token-only userinfo (no ':'): how git+https URLs carry a GitHub token
        f"{{tarball: https://{GH_TOKEN}@codeload.github.com/acme/evil/tar.gz/abc}}",
        # empty user, token as password: `[^\s/@:]+` needs a non-empty user
        f"{{tarball: https://:{GH_TOKEN}@npm.pkg.github.com/evil.tgz}}",
        # a credential that is not a URL at all
        f"{{integrity: sha512-AAAA, aws_access_key_id: {AWS_KEY}}}",
        # a private key pasted into a lockfile
        "{integrity: sha512-AAAA}\n# -----BEGIN OPENSSH PRIVATE KEY-----\n# b3BlbnNzaC1rZXktdjE=",
    ],
    ids=["token-userinfo", "empty-user", "aws-key", "private-key"],
)
def test_lockfile_credentials_skip_the_secrets_gate(tmp_path, line):
    """`_lockfile_only` (decisions.py:323) exempts a lockfile-only change from the secrets gate
    unless an added line matches `scheme://user:pass@`. Each of these added lockfile lines holds a
    credential the native secrets gate detects (native.SECRET_PATTERNS), yet secrets is SKIPPED."""
    plan = plan_for(tmp_path, {"pnpm-lock.yaml": lock_with(line)})
    assert status(plan, SECRETS_GATE) is DecisionStatus.REQUIRED, explain(plan)


@failopen
def test_lockfile_token_reaches_a_pass_verdict(tmp_path):
    """End to end: a GitHub token committed in a lockfile URL (token-only userinfo) and
    `repomarshal done` returns PASS, every required gate having passed (root:unit, the dep_audit
    gate); the native secrets gate, which flags the token, never ran."""
    line = f"{{tarball: https://{GH_TOKEN}@codeload.github.com/acme/evil/tar.gz/abc}}"
    c = case_for(tmp_path, {"pnpm-lock.yaml": lock_with(line)})
    result = api.done(c.repo.root, base=c.base, env=c.env)
    assert result.verdict is not Verdict.PASS, result.reasons


@failopen
def test_new_lockfile_anywhere_carries_a_secret_without_the_secrets_gate(tmp_path):
    """A lockfile is recognised by its name at any depth (signals.is_pure_lockfile). A new
    `config/yarn.lock` that is really a credentials file is still "lockfile-only"."""
    plan = plan_for(
        tmp_path,
        {"config/yarn.lock": f'# deploy credentials\naws_access_key_id "{AWS_KEY}"\n'},
    )
    assert status(plan, SECRETS_GATE) is DecisionStatus.REQUIRED, explain(plan)


def test_lockfile_credential_url_with_password_is_defended(tmp_path):
    """Defended: `https://user:token@host` in an added lockfile line keeps secrets REQUIRED."""
    plan = plan_for(
        tmp_path, {"pnpm-lock.yaml": lock_with(f"{{tarball: https://ci:{GH_TOKEN}@host/x.tgz}}")}
    )
    assert status(plan, SECRETS_GATE) is DecisionStatus.REQUIRED, explain(plan)


def test_manifest_dependency_change_keeps_secrets_is_defended(tmp_path):
    """Defended: a package.json dependency edit (a manifest, not a lockfile) keeps secrets."""
    plan = plan_for(
        tmp_path,
        {
            "package.json": json.dumps(
                {"name": "p", "packageManager": "pnpm@9.1.0",
                 "dependencies": {"left-pad": "1.3.1"}, "scripts": {"test": "vitest run"}}
            ),
        },
    )  # fmt: skip
    assert status(plan, SECRETS_GATE) is DecisionStatus.REQUIRED, explain(plan)


def test_rename_code_to_lockfile_is_defended(tmp_path):
    """Defended: renaming a source file onto a lockfile name counts both ends of the rename, so
    the exemption does not apply."""
    repo = GitRepo(tmp_path / "proj")
    repo.write("package.json", json.dumps({"name": "p", "scripts": {"test": "vitest run"}}))
    repo.write("src/secret_config.ts", f'export const key = "{AWS_KEY}";\n' + "// pad\n" * 20)
    base = repo.commit("BASE")
    repo.git("mv", "src/secret_config.ts", "src/yarn.lock")
    bindir = tmp_path / "bin"
    stub_bin(bindir, "npm")
    plan = api.plan(repo.root, base=base, env=env_with_path(bindir, keep_system=False))
    assert status(plan, SECRETS_GATE) is DecisionStatus.REQUIRED, explain(plan)


def test_lockfile_only_bump_still_requires_unit_and_dep_audit_is_defended(tmp_path):
    """Defended: without integration, a lockfile-only bump still widens unit to every scope and
    requires dep_audit (kind-level UNAVAILABLE when no auditor is configured, which reports
    UNKNOWN, never PASS)."""
    plan = plan_for(tmp_path, {"pnpm-lock.yaml": BASE_LOCK.replace("1.3.0", "1.3.1")})
    unit = [d for d in plan.gates if d.kind.value == "unit"]
    audit = [d for d in plan.gates if d.kind.value == "dep_audit"]
    assert any(d.status is DecisionStatus.REQUIRED for d in unit), explain(plan)
    assert audit and all(
        d.status in (DecisionStatus.REQUIRED, DecisionStatus.UNAVAILABLE) for d in audit
    ), explain(plan)
