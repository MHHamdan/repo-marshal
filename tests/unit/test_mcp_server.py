"""MCP stdio server (roadmap SHOULD #7; ADR-003).

The install-hint test runs everywhere. Every other test needs the optional `mcp` extra and skips
cleanly without it; those tests start `python -m repomarshal mcp` as a real subprocess and talk to
it over stdio with the SDK's own `stdio_client` + `ClientSession`.

Run with the SDK:  <sdk-venv>/bin/python -m pytest -o addopts="" -q tests/unit/test_mcp_server.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from tests.acceptance.conftest import FRONTEND_GATES, SRC, Webapp, load_schema

PLAN, DONE, EXPLAIN = "repomarshal_plan", "repomarshal_done", "repomarshal_explain"


# ---------------------------------------------------------------------- without the SDK


def test_mcp_without_extra_prints_install_hint_and_exits_4(tmp_path: Path):
    """`repomarshal mcp` without the SDK: install hint on stderr, nothing on stdout, exit 4.

    The SDK is hidden by poisoning sys.modules, so this also runs (and means the same) in a venv
    that has the extra installed.
    """
    code = (
        "import sys\n"
        "for name in ('mcp', 'mcp.types', 'mcp.server', 'mcp.server.lowlevel',\n"
        "             'mcp.server.stdio'):\n"
        "    sys.modules[name] = None\n"
        "from repomarshal import cli\n"
        "sys.exit(cli.main(['mcp']))\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 4, r.stderr
    assert r.stdout == ""
    assert "repomarshal[mcp]" in r.stderr
    assert "pip install" in r.stderr
    assert "Traceback" not in r.stderr


def test_done_internal_error_is_unknown_never_pass(monkeypatch, tmp_path: Path):
    """In-process (no SDK needed): an exception inside done is an isError UNKNOWN result."""
    from repomarshal import api, mcp_server

    w = Webapp(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(api, "done", boom)
    structured, text, is_error = mcp_server.call(DONE, {"repo_path": str(w.root)})
    assert is_error is True
    assert structured["verdict"] == "UNKNOWN"
    assert "simulated crash" in text
    jsonschema.validate(structured, mcp_server._done_schema())


# ---------------------------------------------------------------------- with the SDK


@pytest.fixture
def sdk():
    return pytest.importorskip("mcp")


@pytest.fixture
def webapp(tmp_path: Path) -> Webapp:
    """Zero-config webapp fixture with BASE committed and passing stub tools on PATH."""
    return Webapp(tmp_path)


def _run(env: dict[str, str], body, *, legacy: bool = False):
    """Start the server subprocess, negotiate, and run `await body(session)`."""
    import anyio
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    server_env = dict(env)
    server_env["PYTHONPATH"] = str(SRC)
    server_env["PYTHONDONTWRITEBYTECODE"] = "1"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "repomarshal", "mcp"], env=server_env
    )

    async def main():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                with anyio.fail_after(120):
                    if legacy:
                        await session.initialize()
                    else:
                        await session.discover()
                    return await body(session)

    return anyio.run(main)


def _tools(session_result) -> dict:
    return {t.name: t for t in session_result.tools}


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if c.type == "text")


def test_protocol_versions(sdk, webapp):
    async def version(session):
        return session.protocol_version

    assert _run(webapp.env, version) == "2026-07-28"
    assert _run(webapp.env, version, legacy=True) == "2025-11-25"


def test_list_tools_names_annotations_and_schemas(sdk, webapp):
    async def body(session):
        return await session.list_tools()

    tools = _tools(_run(webapp.env, body))
    assert set(tools) == {PLAN, DONE, EXPLAIN}

    plan, done, explain = tools[PLAN], tools[DONE], tools[EXPLAIN]
    assert plan.annotations.read_only_hint is True
    assert plan.annotations.open_world_hint is False
    assert done.annotations.read_only_hint is False
    assert done.annotations.destructive_hint is False
    assert explain.annotations.read_only_hint is True

    for t in tools.values():
        assert t.input_schema["type"] == "object"
        assert t.input_schema["additionalProperties"] is False
        assert "repo_path" in t.input_schema["required"]
        assert t.output_schema is not None and t.output_schema["type"] == "object"
        jsonschema.Draft202012Validator.check_schema(t.output_schema)
    assert "gate_id" in explain.input_schema["required"]
    assert plan.output_schema == load_schema("plan")
    assert set(done.output_schema["required"]) == {"verdict", "reasons", "evidence_path", "record"}


def test_plan_on_webapp_validates_against_schema(sdk, webapp):
    task = webapp.scenario_b()

    async def body(session):
        return await session.call_tool(PLAN, {"repo_path": str(webapp.root), "task": task})

    r = _run(webapp.env, body)
    assert r.is_error is False, _text(r)
    data = r.structured_content
    jsonschema.validate(data, load_schema("plan"))
    assert data["mode"] == "binding"
    assert data["task"] == task
    required = {d["gate_id"] for d in data["gates"] if d["status"] == "REQUIRED"}
    assert set(FRONTEND_GATES) <= required
    assert data["plan_hash"] in _text(r)
    # plan is read-only: no evidence written
    assert not (webapp.root / ".repomarshal" / "evidence").exists()


def _done(webapp: Webapp, task: str):
    async def body(session):
        listed = _tools(await session.list_tools())
        r = await session.call_tool(DONE, {"repo_path": str(webapp.root), "task": task})
        return listed[DONE].output_schema, r

    return _run(webapp.env, body)


def test_done_scenario_b_passes_with_stub_tools(sdk, webapp):
    schema, r = _done(webapp, webapp.scenario_b())
    assert r.is_error is False, _text(r)
    data = r.structured_content
    jsonschema.validate(data, schema)
    assert data["verdict"] == "PASS", data["reasons"]
    assert data["record"] is not None
    jsonschema.validate(data["record"], load_schema("evidence"))
    assert data["record"]["verdict"] == "PASS"
    assert Path(data["evidence_path"]).is_file()
    ran = {res["gate_id"] for res in data["record"]["results"]}
    assert set(FRONTEND_GATES) <= ran


def test_done_scenario_c_zero_config_is_unknown(sdk, webapp):
    schema, r = _done(webapp, webapp.scenario_c())
    data = r.structured_content
    jsonschema.validate(data, schema)
    assert data["verdict"] == "UNKNOWN", data["reasons"]
    assert "Only PASS" in _text(r)


def test_done_failing_gate_is_fail(sdk, webapp):
    task = webapp.scenario_b()
    webapp.stub("npm", exit_code=1, output="lint error")
    _, r = _done(webapp, task)
    assert r.structured_content["verdict"] == "FAIL"


def test_done_invalid_policy_is_blocked(sdk, tmp_path):
    w = Webapp(tmp_path, policy="version = 1\n[gates.bad]\nkind = 'nonsense'\n")
    schema, r = _done(w, w.scenario_b())
    data = r.structured_content
    jsonschema.validate(data, schema)
    assert data["verdict"] == "BLOCKED", data["reasons"]
    assert any("repomarshal.toml" in reason for reason in data["reasons"])


def test_plan_invalid_policy_is_an_error(sdk, tmp_path):
    w = Webapp(tmp_path, policy="version = 1\n[gates.bad]\nkind = 'nonsense'\n")

    async def body(session):
        return await session.call_tool(PLAN, {"repo_path": str(w.root)})

    r = _run(w.env, body)
    assert r.is_error is True
    assert "BLOCKED" in _text(r)


def test_explain_known_and_unknown_gate(sdk, webapp):
    task = webapp.scenario_b()
    repo = str(webapp.root)

    async def body(session):
        known = await session.call_tool(
            EXPLAIN, {"repo_path": repo, "gate_id": "frontend:lint", "task": task}
        )
        skipped = await session.call_tool(
            EXPLAIN, {"repo_path": repo, "gate_id": "backend:unit", "task": task}
        )
        unknown = await session.call_tool(EXPLAIN, {"repo_path": repo, "gate_id": "no:such-gate"})
        return known, skipped, unknown

    known, skipped, unknown = _run(webapp.env, body)
    assert known.is_error is False, _text(known)
    data = known.structured_content
    assert data["gate_id"] == "frontend:lint"
    assert data["status"] == "REQUIRED"
    assert data["reasons"] and all(r["text"] for r in data["reasons"])
    assert skipped.structured_content["status"] == "SKIPPED"
    assert unknown.is_error is True
    assert "unknown gate_id" in _text(unknown)
    assert "frontend:lint" in _text(unknown)  # lists the known gates


def test_untrusted_repo_path_is_rejected_cleanly(sdk, webapp, tmp_path):
    not_git = tmp_path / "plain"
    not_git.mkdir()
    a_file = webapp.root / "README.md"
    bad_paths = [
        "relative/path",
        "",
        "~nosuchuser-xyz/repo",
        str(tmp_path / "missing"),
        str(a_file),
        str(not_git),
    ]

    async def body(session):
        out = []
        for p in bad_paths:
            out.append(await session.call_tool(PLAN, {"repo_path": p}))
            out.append(await session.call_tool(DONE, {"repo_path": p}))
            out.append(await session.call_tool(EXPLAIN, {"repo_path": p, "gate_id": "x"}))
        out.append(await session.call_tool(PLAN, {}))
        out.append(await session.call_tool(PLAN, {"repo_path": 42}))
        out.append(await session.call_tool(PLAN, {"repo_path": str(webapp.root), "extra": 1}))
        out.append(await session.call_tool(DONE, {"repo_path": str(webapp.root), "task": 7}))
        return out

    results = _run(webapp.env, body)
    for r in results:
        assert r.is_error is True, _text(r)
        assert _text(r).strip()
        if r.structured_content is not None:  # done: a verdict, and never PASS
            assert r.structured_content["verdict"] == "BLOCKED"
    # nothing was written into the non-git directory
    assert list(not_git.iterdir()) == []


def test_server_survives_and_stdout_stays_clean(sdk, webapp):
    """A gate printing to stdout must not corrupt the JSON-RPC stream (SDK diverts fd 1)."""
    task = webapp.scenario_b()
    webapp.script_stub("npm", "echo 'noise on stdout'\necho '{\"jsonrpc\": \"2.0\"}'\nexit 0")

    async def body(session):
        r = await session.call_tool(DONE, {"repo_path": str(webapp.root), "task": task})
        again = await session.call_tool(PLAN, {"repo_path": str(webapp.root), "task": task})
        return r, again

    r, again = _run(webapp.env, body)
    assert r.structured_content["verdict"] == "PASS"
    assert again.is_error is False


def test_structured_content_round_trips_as_json(sdk, webapp):
    task = webapp.scenario_a()

    async def body(session):
        return await session.call_tool(PLAN, {"repo_path": str(webapp.root), "task": task})

    r = _run(webapp.env, body)
    data = r.structured_content
    assert json.loads(json.dumps(data)) == data
