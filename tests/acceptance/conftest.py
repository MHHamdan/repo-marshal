"""Builder for the `webapp` acceptance fixture (roadmap M13).

The acceptance suite tests RepoMarshal only through its public contract: `repomarshal.api`
(plan, done, DoneResult), the CLI (`python -m repomarshal`), the model types and the JSON
Schemas. It never imports internal modules.

Every test gets a fresh copy of `tests/fixtures/webapp/` in tmp_path, committed as BASE on `main`.
Scenario methods then apply a change, uncommitted. Gate tools are hermetic: `npm`, `ruff`, `mypy`
and `python` are stub executables in a private bin directory, and the PATH handed to RepoMarshal
holds only that directory plus a directory with a `git` symlink, so no real tool is ever used.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import GitRepo, env_with_path, stub_bin
from repomarshal import api
from repomarshal.model import DecisionStatus, EvidenceRecord, GateDecision, GateResult, Plan

WORKTREE = Path(__file__).resolve().parents[2]
SRC = WORKTREE / "src"
TEMPLATE = WORKTREE / "tests" / "fixtures" / "webapp"
SCHEMAS = SRC / "repomarshal" / "schemas"

TASK_A = "Update installation documentation"
TASK_B = "Improve responsive login form"
TASK_C = "Modify refresh-token handling"

FRONTEND_GATES = (
    "frontend:lint",
    "frontend:typecheck",
    "frontend:unit",
    "frontend:runtime",
    "frontend:a11y",
)
BACKEND_GATES = ("backend:lint", "backend:typecheck", "backend:unit", "backend:integration")
NATIVE_GATES = ("native:docs-links", "native:secrets-diff")

# Stubs that every workspace starts with; each exits 0 (PASS) unless a test replaces it.
DEFAULT_STUBS = ("npm", "ruff", "mypy", "python")

# Scenario C, configured: the two gates the zero-config plan reports as UNAVAILABLE.
CONFIGURED_POLICY = """\
version = 1

[gates.security]
kind = "security_scan"
scope = "backend/"
argv = ["bandit", "-r", "app"]

[gates.backend-runtime]
kind = "runtime"
scope = "backend/"
argv = ["smoke-test"]
"""

ADVISORY = EvidenceRecord.__dataclass_fields__["advisory"].default


class Webapp:
    """A git checkout of the webapp fixture plus a hermetic bin directory of stub tools."""

    def __init__(self, base_dir: Path, policy: str | None = None):
        self.root = base_dir / "webapp"
        self.bindir = base_dir / "bin"
        self.sysbin = base_dir / "sysbin"
        self.markers = base_dir / "markers"
        self.markers.mkdir(parents=True)
        shutil.copytree(
            TEMPLATE,
            self.root,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".ruff_cache", ".pytest_cache", ".mypy_cache"
            ),
        )
        self.repo = GitRepo(self.root)
        if policy is not None:
            self.repo.write("repomarshal.toml", policy)
        self.base = self.repo.commit("BASE: webapp fixture")
        self._link_system_tools()
        for name in DEFAULT_STUBS:
            self.stub(name)

    # ------------------------------------------------------------------ environment

    def _link_system_tools(self) -> None:
        """Only git is reachable besides the stubs (no real npm, ruff, mypy or pytest)."""
        self.sysbin.mkdir(parents=True, exist_ok=True)
        git = shutil.which("git")
        assert git, "git is required to build the fixture"
        (self.sysbin / "git").symlink_to(git)

    @property
    def path_env(self) -> str:
        return os.pathsep.join([str(self.bindir), str(self.sysbin)])

    @property
    def env(self) -> dict[str, str]:
        env = env_with_path(self.bindir, self.sysbin, keep_system=False)
        env["PATH"] = self.path_env
        return env

    def cli_env(self) -> dict[str, str]:
        env = self.env
        env["PYTHONPATH"] = str(SRC)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    # ------------------------------------------------------------------ stubs

    def stub(self, name: str, exit_code: int = 0, output: str = "") -> Path:
        return stub_bin(self.bindir, name, exit_code=exit_code, output=output)

    def script_stub(self, name: str, body: str) -> Path:
        """A stub whose /bin/sh body is given verbatim."""
        self.bindir.mkdir(parents=True, exist_ok=True)
        p = self.bindir / name
        p.write_text("#!/bin/sh\n" + body.rstrip("\n") + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return p

    def marker_stub(self, name: str, exit_code: int = 0) -> Path:
        """A stub that records each invocation by creating markers/<name>."""
        return self.script_stub(name, f": > '{self.markers / name}'\nexit {exit_code}")

    def remove_stub(self, name: str) -> None:
        (self.bindir / name).unlink()

    def ran(self, name: str) -> bool:
        return (self.markers / name).exists()

    # ------------------------------------------------------------------ files

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def append(self, path: str, text: str) -> None:
        with (self.root / path).open("a") as fh:
            fh.write(text)

    def commit_on_base(self, path: str, content: str, message: str = "update BASE") -> str:
        self.repo.write(path, content)
        self.base = self.repo.commit(message)
        return self.base

    # ------------------------------------------------------------------ scenarios

    def scenario_a(self) -> str:
        """Docs-only change with a valid relative link."""
        self.append(
            "docs/install.md",
            "\n## Upgrading\n\nRe-run the steps above. The [README](../README.md) lists what"
            " changed.\n",
        )
        return TASK_A

    def scenario_a_broken_link(self) -> str:
        self.append(
            "docs/install.md",
            "\n## Troubleshooting\n\nSee [troubleshooting](troubleshooting/missing-page.md).\n",
        )
        return TASK_A

    def scenario_b(self) -> str:
        """Frontend UI change: LoginForm.tsx and login.css."""
        self.edit_login_form()
        self.append(
            "frontend/src/styles/login.css",
            "\n@media (max-width: 480px) {\n  .login-form--responsive {\n"
            "    max-width: 100%;\n    padding: 0 1rem;\n  }\n}\n",
        )
        return TASK_B

    def edit_login_form(self) -> None:
        path = "frontend/src/components/LoginForm.tsx"
        tsx = self.read(path).replace(
            'className="login-form"', 'className="login-form login-form--responsive"'
        )
        assert "login-form--responsive" in tsx
        self.repo.write(path, tsx)

    def scenario_c(self) -> str:
        """Auth change: refresh.py gains lines mentioning refresh_token and jwt."""
        self.append(
            "backend/app/auth/refresh.py",
            "\n\ndef rotate(refresh_token: str, key: str) -> dict[str, object]:\n"
            '    """Decode a refresh_token and return its claims."""\n'
            "    import jwt\n\n"
            '    claims: dict[str, object] = jwt.decode(refresh_token, key, algorithms=["HS256"])\n'
            "    return claims\n",
        )
        return TASK_C

    def routine_backend_change(self) -> None:
        """A non-security backend code change (standard risk)."""
        src = self.read("backend/app/api/routes.py").replace(
            '    "/health": "health",\n', '    "/health": "health",\n    "/version": "version",\n'
        )
        assert '"/version"' in src
        self.repo.write("backend/app/api/routes.py", src)

    # ------------------------------------------------------------------ RepoMarshal

    def plan(self, task: str | None = None, base: str | None = None) -> Plan:
        return api.plan(self.root, task=task, base=base, env=self.env)

    def done(
        self, task: str | None = None, base: str | None = None, dry_run: bool = False
    ) -> api.DoneResult:
        return api.done(self.root, task=task, base=base, env=self.env, dry_run=dry_run)

    def cli(
        self, *args: str, repo: Path | None = None, timeout: float = 120
    ) -> subprocess.CompletedProcess[str]:
        """`python -m repomarshal <args> --repo <root>`, run by the real interpreter."""
        target = self.root if repo is None else repo
        return subprocess.run(
            [sys.executable, "-m", "repomarshal", *args, "--repo", str(target)],
            cwd=self.root,
            env=self.cli_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


# ---------------------------------------------------------------------- fixtures


@pytest.fixture
def webapp(tmp_path: Path) -> Webapp:
    """Zero-config webapp (no repomarshal.toml) with BASE committed and passing stubs."""
    return Webapp(tmp_path)


@pytest.fixture
def configured_webapp(tmp_path: Path) -> Webapp:
    """Webapp whose BASE carries CONFIGURED_POLICY, with passing bandit and smoke-test stubs."""
    w = Webapp(tmp_path, policy=CONFIGURED_POLICY)
    w.stub("bandit")
    w.stub("smoke-test")
    return w


# ---------------------------------------------------------------------- plan/record helpers


def decision(plan: Plan, gate_id: str) -> GateDecision:
    found = [d for d in plan.gates if d.gate_id == gate_id]
    assert len(found) == 1, f"expected exactly one decision for {gate_id}, got {found}"
    return found[0]


def status_of(plan: Plan, gate_id: str) -> DecisionStatus:
    return decision(plan, gate_id).status


def decisions_of_kind(plan: Plan, kind: str) -> list[GateDecision]:
    return [d for d in plan.gates if d.kind == kind]


def kind_level(plan: Plan, kind: str) -> list[GateDecision]:
    return [d for d in plan.gates if d.kind == kind and d.gate_id is None]


def required_ids(plan: Plan) -> set[str]:
    return {d.gate_id for d in plan.gates if d.status == DecisionStatus.REQUIRED and d.gate_id}


def skill_names(plan: Plan) -> set[str]:
    return {s.name for s in plan.skills}


def signal_ids(plan: Plan) -> set[str]:
    return {s.id for s in plan.signals}


def context_paths(plan: Plan) -> set[str]:
    return {i.path for i in plan.context.items}


def result(record: EvidenceRecord, gate_id: str) -> GateResult:
    found = [r for r in record.results if r.gate_id == gate_id]
    assert len(found) == 1, f"expected exactly one result for {gate_id}, got {found}"
    return found[0]


def explain(plan: Plan) -> str:
    """Compact decision table for assertion messages."""
    return "\n".join(f"{d.gate_id or d.kind}: {d.status}" for d in plan.gates)


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())
