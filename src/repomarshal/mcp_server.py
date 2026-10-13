"""MCP stdio server (owner: agent "mcp"; roadmap SHOULD #7; ADR-003). Optional extra:
`pip install repomarshal[mcp]`. This is the ONLY module allowed to import the `mcp` SDK, and only
`repomarshal mcp` imports this module.

    main() -> int

Tools (all take an explicit `repo_path`; Roots is deprecated in MCP 2026-07-28):
- repomarshal_plan(repo_path, task?, base?)  readOnlyHint=true, openWorldHint=false
    -> structuredContent = model.to_dict(plan); outputSchema = schemas/plan.schema.json
- repomarshal_done(repo_path, task?, base?)  readOnlyHint=false (runs repository commands)
    -> {"verdict", "reasons", "evidence_path", "record"} (record per evidence.schema.json)
- repomarshal_explain(repo_path, gate_id, task?, base?)  readOnlyHint=true
    -> every reason for one gate decision in a freshly compiled plan
Without the extra installed, main() prints an install hint to stderr and returns 4.

Implementation notes:
- The SDK is imported inside main() only. Everything else here is plain data and functions over
  the stdlib core, so the tool logic is testable (and importable) without the extra.
- `repo_path` is untrusted: it must be an absolute path to an existing directory inside a git
  work tree (git.repo_root). Bad arguments give an isError result, never a crash.
- `done` never reports PASS for an error: an invalid policy or an unusable repository is BLOCKED
  (as in the CLI); any other exception is an isError result whose verdict is UNKNOWN.
- While serving, the SDK points fd 0 at /dev/null and fd 1 at stderr, so gate and git children
  can neither read nor corrupt the JSON-RPC stream.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
import traceback
from importlib import resources
from pathlib import Path
from typing import Any

from repomarshal import __version__, api, git
from repomarshal.model import (
    EXIT_INTERNAL_ERROR,
    GitError,
    NotAGitRepository,
    PolicyInvalid,
    Verdict,
    to_dict,
)

SERVER_NAME = "repomarshal"
INSTALL_HINT = (
    "repomarshal mcp needs the optional MCP extra, which is not installed.\n"
    "Install it with:  pip install 'repomarshal[mcp]'   (or: uv tool install 'repomarshal[mcp]')"
)
_MAX_TEXT = 4096  # cap on task/base/gate_id lengths (untrusted client input)
_MAX_PATH = 4096

PLAN, DONE, EXPLAIN = "repomarshal_plan", "repomarshal_done", "repomarshal_explain"


class ToolInputError(Exception):
    """A tool argument is missing, of the wrong type, or names an unusable repository."""


# --------------------------------------------------------------------------- schemas


def _load_schema(name: str) -> dict[str, Any]:
    path = resources.files("repomarshal").joinpath(f"schemas/{name}.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _done_schema() -> dict[str, Any]:
    """{"verdict", "reasons", "evidence_path", "record"}; record embeds evidence.schema.json.

    The evidence schema's $defs are hoisted to the root so its "#/$defs/..." refs still resolve.
    """
    evidence = _load_schema("evidence")
    defs = evidence.pop("$defs", {})
    for key in ("$schema", "$id", "title"):
        evidence.pop(key, None)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RepoMarshal done result",
        "$defs": defs,
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "reasons", "evidence_path", "record"],
        "properties": {
            "verdict": {"enum": [v.value for v in Verdict]},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "evidence_path": {"type": ["string", "null"]},
            "record": {"anyOf": [{"type": "null"}, evidence]},
        },
    }


_REASON = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "signal"],
    "properties": {"text": {"type": "string"}, "signal": {"type": ["string", "null"]}},
}

EXPLAIN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "RepoMarshal gate explanation",
    "type": "object",
    "additionalProperties": False,
    "required": ["gate_id", "kind", "status", "reasons", "taint", "plan_hash", "mode"],
    "properties": {
        "gate_id": {"type": "string"},
        "kind": {"type": "string"},
        "status": {"enum": ["REQUIRED", "SKIPPED", "NOT_APPLICABLE", "UNAVAILABLE"]},
        "reasons": {"type": "array", "items": _REASON},
        "taint": {"type": "array", "items": _REASON},
        "plan_hash": {"type": "string"},
        "mode": {"enum": ["forecast", "binding"]},
    },
}


def _input_schema(*, gate: bool = False) -> dict[str, Any]:
    props: dict[str, Any] = {
        "repo_path": {
            "type": "string",
            "description": "Absolute path to a directory inside the git work tree to check.",
        },
        "task": {
            "type": "string",
            "description": "Task description (untrusted; can only add obligations).",
        },
        "base": {
            "type": "string",
            "description": "BASE revision to diff against (default: automatic).",
        },
    }
    required = ["repo_path"]
    if gate:
        props["gate_id"] = {
            "type": "string",
            "description": 'Gate id from the plan, e.g. "frontend:lint" or "native:secrets-diff".',
        }
        required.append("gate_id")
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def tool_specs() -> list[dict[str, Any]]:
    """The three tools as MCP `Tool` dicts (wire field names)."""
    return [
        {
            "name": PLAN,
            "title": "Plan the required checks",
            "description": (
                "Compile the current git change into a Change Plan: signals, risk, the gates "
                "(checks) that are REQUIRED/SKIPPED/NOT_APPLICABLE/UNAVAILABLE and why, context "
                "files and skills. Read-only: never executes repository code. Call it before "
                "editing and again before claiming completion."
            ),
            "inputSchema": _input_schema(),
            "outputSchema": _load_schema("plan"),
            "annotations": {
                "title": "Plan the required checks",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
        {
            "name": DONE,
            "title": "Verify the change",
            "description": (
                "Run every REQUIRED gate for the current change and return an evidence-backed "
                "verdict: PASS, FAIL, UNKNOWN or BLOCKED. Only PASS means done. Runs repository "
                "commands and writes an evidence record under .repomarshal/evidence/."
            ),
            "inputSchema": _input_schema(),
            "outputSchema": _done_schema(),
            "annotations": {
                "title": "Verify the change",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        },
        {
            "name": EXPLAIN,
            "title": "Explain one gate decision",
            "description": (
                "Every reason behind one gate's decision in a freshly compiled plan. Read-only."
            ),
            "inputSchema": _input_schema(gate=True),
            "outputSchema": EXPLAIN_SCHEMA,
            "annotations": {
                "title": "Explain one gate decision",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        },
    ]


# --------------------------------------------------------------------------- arguments


def _opt_text(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"{key} must be a string")
    if "\x00" in value or len(value) > _MAX_TEXT:
        raise ToolInputError(f"{key} must not contain NUL and must be at most {_MAX_TEXT} chars")
    return value


def _repo(args: dict[str, Any]) -> Path:
    """Validate the untrusted `repo_path`: absolute, existing directory, inside a git work tree."""
    raw = args.get("repo_path")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolInputError("repo_path is required and must be a non-empty string")
    if "\x00" in raw or len(raw) > _MAX_PATH:
        raise ToolInputError("repo_path is not a valid path")
    path = Path(raw)  # no expanduser(): "~nouser" raises, and clients must send absolute paths
    if not path.is_absolute():
        raise ToolInputError(
            f"repo_path must be absolute (got {raw!r}); the server's working directory is not "
            "the client's"
        )
    try:
        is_dir = path.is_dir()
    except OSError as e:
        raise ToolInputError(f"repo_path {raw!r} cannot be accessed: {e}") from e
    if not is_dir:
        raise ToolInputError(f"repo_path {raw!r} is not an existing directory")
    try:
        git.repo_root(path)
    except (NotAGitRepository, GitError) as e:
        raise ToolInputError(f"repo_path {raw!r}: {e}") from e
    return path


def _common(args: dict[str, Any]) -> tuple[Path, str | None, str | None]:
    unknown = sorted(set(args) - {"repo_path", "task", "base", "gate_id"})
    if unknown:
        raise ToolInputError(f"unknown arguments: {', '.join(unknown)}")
    return _repo(args), _opt_text(args, "task"), _opt_text(args, "base")


# --------------------------------------------------------------------------- tools
#
# Each tool returns (structured, text, is_error). `structured` is None only for an error result
# of a tool whose output has no meaningful error shape (plan, explain).

Outcome = tuple[dict[str, Any] | None, str, bool]


def _error(message: str) -> Outcome:
    return None, f"error: {message}", True


def tool_plan(args: dict[str, Any]) -> Outcome:
    try:
        repo, task, base = _common(args)
        if "gate_id" in args:
            raise ToolInputError("unknown arguments: gate_id")
        p = api.plan(repo, task=task, base=base)
    except ToolInputError as e:
        return _error(str(e))
    except PolicyInvalid as e:
        return _error("BLOCKED: invalid repomarshal.toml at BASE: " + "; ".join(e.errors))
    except (GitError, NotAGitRepository) as e:
        return _error(f"BLOCKED: {e}")
    except Exception as e:  # noqa: BLE001 - report every failure as an isError result
        traceback.print_exc(file=sys.stderr)
        return _error(f"internal error: {type(e).__name__}: {e}")
    data = to_dict(p)
    required = [d.gate_id or d.kind.value for d in p.required()]
    text = (
        f"{p.mode.value} plan {p.plan_hash}: {len(p.change.files)} changed file(s), "
        f"risk {p.risk.value}, required gates: {', '.join(required) or 'none'}"
    )
    return data, text, False


def _verdict(verdict: Verdict, reasons: list[str], path: str | None, record: Any) -> dict:
    return {"verdict": verdict.value, "reasons": reasons, "evidence_path": path, "record": record}


def _done_text(data: dict[str, Any]) -> str:
    lines = [f"verdict: {data['verdict']}"] + [f"  - {r}" for r in data["reasons"]]
    if data["evidence_path"]:
        lines.append(f"evidence: {data['evidence_path']}")
    if data["verdict"] != Verdict.PASS.value:
        lines.append("Only PASS means the change is verified.")
    return "\n".join(lines)


_DONE_LOCK = threading.Lock()  # gates mutate caches/evidence; never run two `done`s at once


def tool_done(args: dict[str, Any]) -> Outcome:
    try:
        repo, task, base = _common(args)
        if "gate_id" in args:
            raise ToolInputError("unknown arguments: gate_id")
    except ToolInputError as e:
        data = _verdict(Verdict.BLOCKED, [f"invalid arguments: {e}"], None, None)
        return data, _done_text(data), True
    try:
        with _DONE_LOCK:
            r = api.done(repo, task=task, base=base)
        data = _verdict(
            r.verdict,
            list(r.reasons),
            str(r.evidence_path) if r.evidence_path else None,
            to_dict(r.record) if r.record else None,
        )
        is_error = False
    except PolicyInvalid as e:
        data = _verdict(
            Verdict.BLOCKED,
            [f"invalid repomarshal.toml at BASE: {x}" for x in e.errors],
            None,
            None,
        )
        is_error = False
    except (GitError, NotAGitRepository) as e:
        data = _verdict(Verdict.BLOCKED, [str(e)], None, None)
        is_error = False
    except Exception as e:  # noqa: BLE001 - an internal error must never look like PASS
        traceback.print_exc(file=sys.stderr)
        data = _verdict(Verdict.UNKNOWN, [f"internal error: {type(e).__name__}: {e}"], None, None)
        is_error = True
    return data, _done_text(data), is_error


def tool_explain(args: dict[str, Any]) -> Outcome:
    try:
        repo, task, base = _common(args)
        gate_id = _opt_text(args, "gate_id")
        if not gate_id:
            raise ToolInputError("gate_id is required and must be a non-empty string")
        p = api.plan(repo, task=task, base=base)
    except ToolInputError as e:
        return _error(str(e))
    except PolicyInvalid as e:
        return _error("BLOCKED: invalid repomarshal.toml at BASE: " + "; ".join(e.errors))
    except (GitError, NotAGitRepository) as e:
        return _error(f"BLOCKED: {e}")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        return _error(f"internal error: {type(e).__name__}: {e}")
    found = [d for d in p.gates if d.gate_id == gate_id]
    if len(found) != 1:
        known = sorted({d.gate_id for d in p.gates if d.gate_id})
        why = "unknown gate_id" if not found else "ambiguous gate_id (several decisions)"
        return _error(f"{why} {gate_id!r}; gates in this plan: {', '.join(known) or 'none'}")
    d = found[0]
    data = {
        "gate_id": gate_id,
        "kind": d.kind.value,
        "status": d.status.value,
        "reasons": to_dict(d.reasons),
        "taint": to_dict(d.taint),
        "plan_hash": p.plan_hash,
        "mode": p.mode.value,
    }
    lines = [f"{gate_id} ({d.kind.value}): {d.status.value}"]
    lines += [f"  - {r.text}" + (f" [{r.signal}]" if r.signal else "") for r in d.reasons]
    lines += [f"  ! untrusted result: {t.text}" for t in d.taint]
    return data, "\n".join(lines), False


TOOLS = {PLAN: tool_plan, DONE: tool_done, EXPLAIN: tool_explain}


def call(name: str, args: dict[str, Any] | None) -> Outcome:
    fn = TOOLS.get(name)
    if fn is None:
        return _error(f"unknown tool {name!r}; available: {', '.join(TOOLS)}")
    if args is not None and not isinstance(args, dict):
        return _error("arguments must be an object")
    return fn(dict(args or {}))


# --------------------------------------------------------------------------- server


def main() -> int:
    try:
        import anyio
        import anyio.to_thread
        import mcp.types as types
        from mcp.server.lowlevel import Server
        from mcp.server.stdio import stdio_server
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    tools = [types.Tool.model_validate(copy.deepcopy(spec)) for spec in tool_specs()]

    async def on_list_tools(ctx: Any, params: Any) -> Any:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx: Any, params: Any) -> Any:
        # The core is synchronous and runs subprocesses: keep it off the event loop.
        structured, text, is_error = await anyio.to_thread.run_sync(
            call, params.name, params.arguments
        )
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }
        if structured is not None:
            result["structuredContent"] = structured
        return types.CallToolResult.model_validate(result)

    server = Server(
        SERVER_NAME,
        version=__version__,
        title="RepoMarshal",
        instructions=(
            "Call repomarshal_plan with the repository's absolute path before editing, and "
            "repomarshal_done before claiming completion. Only a PASS verdict means done; FAIL, "
            "UNKNOWN and BLOCKED mean the change is not verified."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )

    async def serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    try:
        anyio.run(serve)
    except KeyboardInterrupt:
        return 0
    return 0
