"""Agent host hook shims (owner: agent "hosts"; roadmap SHOULD #6; ADR-003).

    run(host, payload_text, repo=".", base=None) -> int   (the process exit code)

Claude Code `Stop` hook (host "claude-code"):
- Input: the hook payload JSON on stdin (fields include session_id, transcript_path, cwd,
  hook_event_name == "Stop", stop_hook_active). The repository is `cwd` from the payload unless
  --repo was given explicitly.
- Behaviour: run `api.done(...)`. On PASS, allow the stop: print nothing (or a JSON object without
  a block decision) and exit 0. On ANY other outcome (FAIL, UNKNOWN, BLOCKED, malformed payload,
  not a git repository, internal error) BLOCK the stop: print exactly one JSON object
  {"decision": "block", "reason": "<verdict + failing/unknown gates + what to do>"} to stdout and
  exit 0. Nothing else may be written to stdout.
- FAIL CLOSED: Claude Code treats hook exit codes other than 0 and 2 (and unparseable stdout) as
  non-blocking errors, which would let the agent stop. So this function must never raise and
  never return any exit code other than 0 (with a block decision) or 2 (stderr reason) on a
  non-PASS path.
"""

from __future__ import annotations


def run(host: str, payload_text: str, repo: str = ".", base: str | None = None) -> int:
    raise NotImplementedError
