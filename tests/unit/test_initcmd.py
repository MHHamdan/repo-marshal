"""`repomarshal init` (owner: agent "hosts"; roadmap SHOULD #5)."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from helpers import GitRepo
from repomarshal import initcmd, policy
from repomarshal.model import GateKind, Policy

SRC = Path(__file__).resolve().parents[2] / "src"


def _args(repo: Path | str = ".", print_host: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(repo=str(repo), print_host=print_host)


def _repo(tmp_path: Path) -> GitRepo:
    r = GitRepo(tmp_path / "r")
    r.write("README.md", "hello\n")
    r.write(".gitignore", "build/\n")
    r.commit("base")
    return r


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under root (including .git) with its bytes."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "repomarshal", *args],
        cwd=cwd,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# ---------------------------------------------------------------------- writing the policy


def test_init_writes_a_policy_that_loads_once_committed(tmp_path, capsys):
    r = _repo(tmp_path)
    assert initcmd.run(_args(r.root)) == 0
    path = r.root / policy.POLICY_FILE
    assert path.read_text() == initcmd.TEMPLATE
    out = capsys.readouterr().out
    assert "commit" in out.lower() and "base branch" in out

    sha = r.commit("add policy")
    pol = policy.load_policy(r.root, sha)
    assert pol.source == f"base:{sha}:{policy.POLICY_FILE}"
    assert pol.version == 1
    # Everything else is commented out: the defaults apply.
    assert (pol.token_budget, pol.skills_max, pol.waivers_permit_pass) == (
        Policy.token_budget,
        Policy.skills_max,
        Policy.waivers_permit_pass,
    )
    assert pol.gates == () and pol.signals == ()


def test_template_comments_every_section():
    for section in ("[context]", "[skills]", "[completion]", "[gates.", "[[signals]]"):
        assert f"# {section}" in initcmd.TEMPLATE, section
    assert "version = 1" in initcmd.TEMPLATE


def test_uncommented_examples_are_a_valid_policy(tmp_path):
    """The examples themselves are correct: uncommenting all of them still loads."""
    example = re.compile(r"^# (\[.*\]|[a-z_]+ = .*)$")
    lines = [
        example.match(line).group(1) if example.match(line) else line
        for line in initcmd.TEMPLATE.splitlines()
    ]
    r = _repo(tmp_path)
    r.write(policy.POLICY_FILE, "\n".join(lines) + "\n")
    sha = r.commit("policy with examples")
    pol = policy.load_policy(r.root, sha)
    assert pol.token_budget == 12000 and pol.skills_max == 3
    assert [g.id for g in pol.gates] == ["security"]
    assert pol.gates[0].kind == GateKind.SECURITY_SCAN
    assert [s.id for s in pol.signals] == ["custom.payments"]


def test_init_never_overwrites(tmp_path, capsys):
    r = _repo(tmp_path)
    r.write(policy.POLICY_FILE, "# mine\nversion = 1\n")
    before = _snapshot(r.root)
    assert initcmd.run(_args(r.root)) == 1
    assert "already exists" in capsys.readouterr().err
    assert _snapshot(r.root) == before


def test_init_does_not_follow_a_dangling_symlink(tmp_path):
    r = _repo(tmp_path)
    target = tmp_path / "outside.toml"
    (r.root / policy.POLICY_FILE).symlink_to(target)
    assert initcmd.run(_args(r.root)) == 1
    assert not target.exists()


def test_init_touches_nothing_else(tmp_path):
    r = _repo(tmp_path)
    before = _snapshot(r.root)
    assert initcmd.run(_args(r.root)) == 0
    after = _snapshot(r.root)
    assert set(after) - set(before) == {policy.POLICY_FILE}
    assert {k: v for k, v in after.items() if k != policy.POLICY_FILE} == before


def test_init_from_a_subdirectory_writes_at_the_root(tmp_path):
    r = _repo(tmp_path)
    sub = r.root / "pkg"
    sub.mkdir()
    assert initcmd.run(_args(sub)) == 0
    assert (r.root / policy.POLICY_FILE).is_file()
    assert not (sub / policy.POLICY_FILE).exists()


def test_init_outside_a_git_repository(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert initcmd.run(_args(plain)) == 3
    assert "not inside a git work tree" in capsys.readouterr().err
    assert list(plain.iterdir()) == []


def test_init_cli(tmp_path):
    r = _repo(tmp_path)
    proc = _cli("init", "--repo", str(r.root), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert (r.root / policy.POLICY_FILE).read_text() == initcmd.TEMPLATE
    again = _cli("init", "--repo", str(r.root), cwd=tmp_path)
    assert again.returncode == 1
    assert "already exists" in again.stderr


# ---------------------------------------------------------------------- --print HOST


def _json_blocks(text: str) -> list[object]:
    decoder = json.JSONDecoder()
    blocks, i = [], 0
    while (start := text.find("{", i)) != -1:
        obj, end = decoder.raw_decode(text, start)
        blocks.append(obj)
        i = end
    return blocks


def test_print_claude_code_is_a_stop_hook_snippet(tmp_path, capsys):
    r = _repo(tmp_path)
    before = _snapshot(r.root)
    assert initcmd.run(_args(r.root, "claude-code")) == 0
    out = capsys.readouterr().out
    hook = _json_blocks(out)[0]
    [entry] = hook["hooks"]["Stop"]
    [command] = entry["hooks"]
    assert command["type"] == "command"
    assert command["command"] == "repomarshal done --hook claude-code"
    assert isinstance(command["timeout"], int) and command["timeout"] > 60
    assert ".claude/settings.json" in out
    assert _snapshot(r.root) == before  # writes nothing


def test_print_claude_code_mcp_part_is_labelled_untested(capsys):
    assert initcmd.run(_args(".", "claude-code")) == 0
    out = capsys.readouterr().out
    mcp = _json_blocks(out)[1]
    assert mcp["mcpServers"]["repomarshal"]["args"] == ["mcp"]
    tail = out[out.index(".mcp.json") - 40 :]
    assert "UNTESTED" in tail


@pytest.mark.parametrize("host", ["cursor", "copilot", "gemini"])
def test_print_json_mcp_hosts_are_untested_and_parse(host, capsys):
    assert initcmd.run(_args(".", host)) == 0
    out = capsys.readouterr().out
    assert "UNTESTED" in out
    [cfg] = _json_blocks(out)
    servers = cfg.get("mcpServers") or cfg.get("servers")
    assert servers["repomarshal"] == {**servers["repomarshal"], "command": "repomarshal"}
    assert servers["repomarshal"]["args"] == ["mcp"]
    assert "repomarshal done" in out


def test_print_codex_is_valid_toml_and_untested(capsys):
    assert initcmd.run(_args(".", "codex")) == 0
    out = capsys.readouterr().out
    assert "UNTESTED" in out
    cfg = tomllib.loads(out)  # comments plus one table: the whole output parses
    assert cfg["mcp_servers"]["repomarshal"] == {"command": "repomarshal", "args": ["mcp"]}


def test_print_works_outside_a_repository_and_writes_nothing(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    proc = _cli("init", "--print", "claude-code", cwd=plain)
    assert proc.returncode == 0, proc.stderr
    assert "repomarshal done --hook claude-code" in proc.stdout
    assert list(plain.iterdir()) == []
