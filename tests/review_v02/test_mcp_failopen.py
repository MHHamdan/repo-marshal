"""MCP stdio server (`repomarshal mcp`): the client is the agent being judged. Every argument it
sends (repo_path, base, task) is adversarial. No call may yield a PASS for a change that is not
verified, an error without isError, or a hang.

Run with the SDK venv:
    PYTHONPATH=src /tmp/claude-0/mcpvenv/bin/python -m pytest -o addopts="" -q tests/review_v02
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from repomarshal import api
from tests.acceptance.conftest import SRC

from .conftest import TEST_REFRESH, Webapp, failing_backend, nested_docs_repo

pytest.importorskip("mcp")

from repomarshal import mcp_server  # noqa: E402

PLAN, DONE, EXPLAIN = mcp_server.PLAN, mcp_server.DONE, mcp_server.EXPLAIN


@pytest.fixture
def on_path(webapp: Webapp, monkeypatch) -> Webapp:
    monkeypatch.setenv("PATH", webapp.path_env)
    return webapp


def _call(name: str, args):
    return mcp_server.call(name, args)


def _commit_failing_change_then_docs(w: Webapp) -> None:
    """The agent commits a backend change whose tests fail, then touches docs on top."""
    failing_backend(w)
    w.repo.commit("agent: backend change")
    w.scenario_a()


# ---------------------------------------------------------------------- confirmed fail-open


@pytest.mark.failopen
def test_client_base_cannot_hide_committed_work(on_path: Webapp):
    """`base` is client-controlled. base="HEAD" diffs only the uncommitted docs edit, so the
    committed failing backend change is never judged and the tool returns PASS. An untrusted
    explicit base must never narrow the diff below the automatic/remembered BASE."""
    w = on_path
    _commit_failing_change_then_docs(w)
    assert api.done(w.root).verdict == api.Verdict.FAIL  # honest verdict

    structured, _text, is_error = _call(DONE, {"repo_path": str(w.root), "base": "HEAD"})
    assert structured["verdict"] != "PASS" or is_error, structured["reasons"]


@pytest.mark.failopen
def test_client_base_does_not_poison_the_anchor_for_the_hook(on_path: Webapp):
    """anchor.remember(explicit=True) lets the MCP call overwrite the remembered BASE. The agent
    then fast-forwards local `main` onto its work (the attack the anchor exists to stop), and the
    next automatic run (what the Stop hook does) diffs from the agent's commit: PASS on a FAIL."""
    w = on_path
    w.repo.git("switch", "-q", "-c", "work")
    w.scenario_a()
    api.done(w.root)  # first automatic run on the branch: remembers BASE = main
    w.repo.git("checkout", "-q", "--", "docs/install.md")
    _commit_failing_change_then_docs(w)

    _call(DONE, {"repo_path": str(w.root), "base": "HEAD"})
    w.repo.git("branch", "-f", "main", "work")
    res = api.done(w.root)  # what `repomarshal done --hook claude-code` runs
    assert res.verdict == api.Verdict.FAIL, res.reasons


@pytest.mark.failopen
def test_repo_path_in_nested_repo_is_not_pass(on_path: Webapp):
    """repo_path is client-controlled: an agent-made repository nested inside the project with a
    docs-only change PASSes while the project fails. The server has no notion of the project
    it serves (no pinned root), so the verdict describes a repository the host never asked for."""
    w = on_path
    failing_backend(w)
    nested = nested_docs_repo(w)
    structured, _text, is_error = _call(DONE, {"repo_path": str(nested.root)})
    assert structured["verdict"] != "PASS" or is_error, structured["reasons"]


# ---------------------------------------------------------------------- regression guards


@pytest.mark.parametrize(
    "args",
    [
        {"repo_path": "relative/path"},
        {"repo_path": "/nonexistent/x"},
        {"repo_path": "/"},
        {"repo_path": 7},
        {"repo_path": ["/tmp"]},
        {"repo_path": "/tmp\x00/x"},
        {"repo_path": "/" + "a" * 5000},
        {"repo_path": "/\ud800"},
        {"repo_path": "/tmp", "evil": True},
        {"repo_path": "/tmp", "task": "x" * 5000},
        {"repo_path": "/tmp", "task": 3},
        {"repo_path": "/tmp", "gate_id": "backend:unit"},
        {},
    ],
    ids=[
        "relative", "missing", "not-git", "int", "list", "nul", "huge", "lone-surrogate",
        "unknown-key", "huge-task", "task-int", "gate-id-on-done", "empty",
    ],
)  # fmt: skip
def test_bad_done_arguments_are_errors_never_pass(args):
    structured, text, is_error = _call(DONE, args)
    assert is_error is True
    assert structured["verdict"] == "BLOCKED"
    assert "PASS" not in text.splitlines()[0]


@pytest.mark.parametrize("args", [None, [], "x", 3])
def test_non_object_arguments(args):
    structured, _text, is_error = _call(DONE, args)
    if args is None:  # no arguments at all: missing repo_path
        assert is_error and structured["verdict"] == "BLOCKED"
    else:
        assert is_error and structured is None


def test_symlinked_repo_path_is_the_same_repository(on_path: Webapp, tmp_path: Path):
    w = on_path
    failing_backend(w)
    link = tmp_path / "link"
    link.symlink_to(w.root)
    structured, _text, _err = _call(DONE, {"repo_path": str(link / "backend")})
    assert structured["verdict"] == "FAIL"


def test_done_internal_base_exception_is_error(on_path: Webapp, monkeypatch):
    def interrupted(*_a, **_k):
        raise MemoryError("boom")

    monkeypatch.setattr(api, "done", interrupted)
    structured, _text, is_error = _call(DONE, {"repo_path": str(on_path.root)})
    assert is_error is True and structured["verdict"] == "UNKNOWN"


def test_explain_agrees_with_done_on_taint(on_path: Webapp):
    w = on_path
    src = w.read(TEST_REFRESH).replace(
        "def test_expired_after_lifetime", '@pytest.mark.skip(reason="x")\ndef test_expired_after'
    )
    w.repo.write(TEST_REFRESH, "import pytest\n" + src)
    explained, _t, err = _call(EXPLAIN, {"repo_path": str(w.root), "gate_id": "backend:unit"})
    assert err is False and explained["status"] == "REQUIRED" and explained["taint"]
    plan, _t, _e = _call(PLAN, {"repo_path": str(w.root)})
    unit = next(g for g in plan["gates"] if g.get("gate_id") == "backend:unit")
    assert unit["taint"]
    done, _t, _e = _call(DONE, {"repo_path": str(w.root)})
    assert done["verdict"] == "UNKNOWN"


def _serve(env: dict[str, str], body):
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
                with anyio.fail_after(60):
                    await session.discover()
                    return await body(session)

    return anyio.run(main)


def test_concurrent_calls_over_stdio_never_pass_a_failing_change(webapp: Webapp):
    """Several done/plan/explain calls in flight at once, a gate that writes JSON-RPC-looking
    noise to stdout and to the inherited stdin, and a malformed call: the stream survives, every
    done is FAIL without isError, and the bad call is an isError result."""
    import anyio

    failing_backend(webapp)
    webapp.script_stub(
        "python",
        'echo \'{"jsonrpc":"2.0","id":1,"result":{"isError":false}}\'\n'
        "cat >/dev/null 2>&1 &\nexit 1",
    )
    root = str(webapp.root)

    async def body(session):
        results: dict[str, object] = {}

        async def run(key, name, args):
            results[key] = await session.call_tool(name, args)

        async with anyio.create_task_group() as tg:
            for i in range(3):
                tg.start_soon(run, f"done{i}", DONE, {"repo_path": root})
            tg.start_soon(run, "plan", PLAN, {"repo_path": root})
            tg.start_soon(run, "explain", EXPLAIN, {"repo_path": root, "gate_id": "backend:unit"})
            tg.start_soon(run, "bad", DONE, {"repo_path": root, "base": 5})
        return results

    results = _serve(webapp.env, body)
    for i in range(3):
        r = results[f"done{i}"]
        assert r.structured_content["verdict"] == "FAIL"
    assert results["plan"].is_error is False
    assert results["explain"].is_error is False
    assert results["bad"].is_error is True
    assert os.path.isdir(root)
