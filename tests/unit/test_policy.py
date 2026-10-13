"""Unit tests for repomarshal.policy (roadmap M4)."""

from __future__ import annotations

import pytest

from repomarshal.model import (
    Cost,
    GateKind,
    PolicyInvalid,
    Risk,
    sha256_bytes,
    sha256_json,
)
from repomarshal.policy import load_policy
from tests.helpers import GitRepo

VALID = """\
version = 1
[context]
token_budget = 8000
[skills]
max = 2
[completion]
waivers_permit_pass = true
[gates.sec]
kind = "security_scan"
argv = ["bandit", "-r", "app"]
scope = "backend/"
timeout_s = 900
[gates.audit]
kind = "dep_audit"
argv = ["pip-audit"]
cost = "standard"
cwd = "tools"
[[signals]]
id = "custom.payments"
domain = "security"
risk = "elevated"
paths = ["**/payments/**"]
"""


def _repo(tmp_path, policy_text: str | bytes | None) -> tuple[GitRepo, str]:
    repo = GitRepo(tmp_path / "r")
    repo.write("README.md", "x\n")
    if policy_text is not None:
        repo.write("repomarshal.toml", policy_text)
    return repo, repo.commit()


def _errors(tmp_path, text: str) -> list[str]:
    repo, base = _repo(tmp_path, text)
    with pytest.raises(PolicyInvalid) as exc:
        load_policy(repo.root, base)
    return exc.value.errors


def test_missing_file_gives_default(tmp_path):
    repo, base = _repo(tmp_path, None)
    p = load_policy(repo.root, base)
    assert p.source == "default"
    assert p.digest == sha256_bytes(b"default")
    assert (p.token_budget, p.skills_max, p.waivers_permit_pass) == (12_000, 3, False)
    assert p.gates == () and p.signals == ()


def test_no_base_gives_default(tmp_path):
    repo = GitRepo(tmp_path / "r")
    repo.write("repomarshal.toml", "garbage = [")
    assert load_policy(repo.root, None).source == "default"


def test_valid_policy_parsed(tmp_path):
    repo, base = _repo(tmp_path, VALID)
    p = load_policy(repo.root, base)
    assert p.source == f"base:{base}:repomarshal.toml"
    assert p.digest == sha256_bytes(VALID.encode())
    assert (p.token_budget, p.skills_max, p.waivers_permit_pass) == (8000, 2, True)

    audit, sec = p.gates  # sorted by id
    assert sec.id == "sec"
    assert sec.kind == GateKind.SECURITY_SCAN
    assert sec.argv == ("bandit", "-r", "app")
    assert (sec.scope, sec.cwd) == ("backend/", "backend")
    assert sec.cost == Cost.EXPENSIVE  # DEFAULT_COST
    assert sec.timeout_s == 900
    assert sec.provider == "command"
    assert sec.source == "repomarshal.toml#gates.sec"
    assert sec.definition_digest == sha256_json(
        {
            "kind": "security_scan",
            "argv": ["bandit", "-r", "app"],
            "scope": "backend/",
            "timeout_s": 900,
        }
    )
    assert (audit.scope, audit.cwd, audit.cost, audit.timeout_s) == (
        "",
        "tools",
        Cost.STANDARD,
        600,
    )

    (sig,) = p.signals
    assert sig.id == "custom.payments"
    assert sig.risk == Risk.ELEVATED
    assert sig.paths == ("**/payments/**",)


def test_policy_read_from_base_not_worktree(tmp_path):
    repo, base = _repo(tmp_path, VALID)
    repo.write("repomarshal.toml", "version = 1\n[skills]\nmax = 0\n")
    p = load_policy(repo.root, base)
    assert p.skills_max == 2
    assert len(p.gates) == 2


def test_worktree_only_policy_is_ignored(tmp_path):
    repo, base = _repo(tmp_path, None)
    repo.write("repomarshal.toml", "not toml [")
    assert load_policy(repo.root, base).source == "default"


def test_invalid_toml(tmp_path):
    (err,) = _errors(tmp_path, "version = 1\n[gates\n")
    assert "invalid TOML" in err


def test_invalid_utf8(tmp_path):
    repo, base = _repo(tmp_path, b"version = 1\n# \xff\xfe\n")
    with pytest.raises(PolicyInvalid, match="UTF-8"):
        load_policy(repo.root, base)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "version: required"),
        ("version = 2", "version: unsupported"),
        ('version = "1"', "version: unsupported"),
        ("version = true", "version: unsupported"),
    ],
)
def test_version_errors(tmp_path, text, expected):
    errors = _errors(tmp_path, text)
    assert any(e.startswith(expected) for e in errors), errors


def test_unknown_keys_are_errors(tmp_path):
    errors = _errors(
        tmp_path,
        """\
version = 1
colour = "blue"
[context]
token_budget = 1
budget = 2
[gates.x]
kind = "lint"
argv = ["ruff"]
shell = true
[[signals]]
id = "custom.a"
domain = "d"
risk = "low"
paths = ["a"]
extra = 1
""",
    )
    assert errors == [
        "colour: unknown key",
        "context.budget: unknown key",
        "gates.x.shell: unknown key",
        "signals[0].extra: unknown key",
    ]


def test_argv_string_is_error(tmp_path):
    errors = _errors(tmp_path, 'version = 1\n[gates.t]\nkind = "unit"\nargv = "pytest -q"\n')
    assert errors == ["gates.t.argv: must be an array of strings, not a string"]


@pytest.mark.parametrize("argv", ["[]", '[""]', '["a", 1]', "1"])
def test_argv_bad_shapes(tmp_path, argv):
    errors = _errors(tmp_path, f'version = 1\n[gates.t]\nkind = "unit"\nargv = {argv}\n')
    assert errors == ["gates.t.argv: must be a non-empty array of strings"]


@pytest.mark.parametrize(
    "scope", ["../x/", "/abs/", "backend", "a/../b/", "./a/", "a//b/", "a\\\\b/", "1"]
)
def test_bad_scope(tmp_path, scope):
    value = scope if scope == "1" else f'"{scope}"'
    errors = _errors(
        tmp_path, f'version = 1\n[gates.t]\nkind = "unit"\nargv = ["x"]\nscope = {value}\n'
    )
    assert any(e.startswith("gates.t.scope: invalid") for e in errors), errors


@pytest.mark.parametrize("scope", ['""', '"backend/"', '"services/api/"'])
def test_good_scope(tmp_path, scope):
    repo, base = _repo(
        tmp_path, f'version = 1\n[gates.t]\nkind = "unit"\nargv = ["x"]\nscope = {scope}\n'
    )
    (gate,) = load_policy(repo.root, base).gates
    assert gate.cwd == gate.scope.rstrip("/")


@pytest.mark.parametrize("cwd", ['"../x"', '"/x"', '"x/"'])
def test_bad_cwd(tmp_path, cwd):
    errors = _errors(
        tmp_path, f'version = 1\n[gates.t]\nkind = "unit"\nargv = ["x"]\ncwd = {cwd}\n'
    )
    assert any(e.startswith("gates.t.cwd: invalid") for e in errors), errors


def test_bad_kind_cost_timeout_and_id(tmp_path):
    errors = _errors(
        tmp_path,
        """\
version = 1
[gates.a]
kind = "build"
argv = ["make"]
cost = "free"
timeout_s = 0
[gates.b]
argv = ["x"]
[gates.Bad]
kind = "lint"
argv = ["x"]
[gates.c]
kind = ["lint"]
argv = ["x"]
timeout_s = 86401
""",
    )
    assert errors == [
        "gates.Bad: invalid gate id (expected [a-z0-9][a-z0-9._-]*)",
        "gates.a.kind: unknown kind 'build'",
        "gates.a.cost: unknown cost 'free'",
        "gates.a.timeout_s: must be an integer 1..86400",
        "gates.b.kind: required",
        "gates.c.kind: unknown kind ['lint']",
        "gates.c.timeout_s: must be an integer 1..86400",
    ]


def test_signal_errors(tmp_path):
    errors = _errors(
        tmp_path,
        """\
version = 1
[[signals]]
id = "payments"
domain = "security"
risk = "high"
paths = []
[[signals]]
id = "custom.a"
domain = ""
risk = "low"
paths = ["x"]
[[signals]]
id = "custom.a"
domain = "d"
risk = "low"
paths = "x"
""",
    )
    assert errors == [
        "signals[0].id: must be a string starting with \"custom.\", got 'payments'",
        "signals[0].risk: must be one of low|standard|elevated, got 'high'",
        "signals[0].paths: must be a non-empty array of glob strings",
        "signals[1].domain: must be a non-empty string",
        "signals[2].id: duplicate signal id 'custom.a'",
        "signals[2].paths: must be a non-empty array of glob strings",
    ]


def test_section_value_errors(tmp_path):
    errors = _errors(
        tmp_path,
        """\
version = 1
[context]
token_budget = 0
[skills]
max = 11
[completion]
waivers_permit_pass = "yes"
""",
    )
    assert errors == [
        "context.token_budget: must be an integer > 0, got 0",
        "skills.max: must be an integer 0..10, got 11",
        "completion.waivers_permit_pass: must be a boolean, got 'yes'",
    ]


def test_wrong_section_shapes(tmp_path):
    errors = _errors(tmp_path, 'version = 1\ncontext = 3\ngates = "x"\nsignals = 1\n')
    assert errors == [
        "context: must be a table",
        "gates: must be a table of [gates.<id>] tables",
        "signals: must be an array of [[signals]] tables",
    ]


def test_errors_are_collected_in_message(tmp_path):
    repo, base = _repo(tmp_path, 'version = 3\nfoo = 1\n[gates.t]\nkind = "x"\nargv = "y"\n')
    with pytest.raises(PolicyInvalid) as exc:
        load_policy(repo.root, base)
    assert len(exc.value.errors) == 4
    assert str(exc.value) == "; ".join(exc.value.errors)
