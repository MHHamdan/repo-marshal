"""Agent host hook shims (owner: agent "hosts"; roadmap SHOULD #6; ADR-003).

    run(host, payload_text, repo=None, base=None) -> int   (the process exit code)

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

Implementation notes (agent "hosts"):
- Output format: PASS -> empty stdout, exit 0. Every other path -> one line of JSON
  `{"decision": "block", "reason": ...}` on stdout, exit 0. If writing stdout itself fails, the
  reason goes to stderr and the exit code is 2 (Claude Code's other blocking channel).
- cwd vs --repo: `done --repo` defaults to None, so None (or "") means "not given" and the
  payload's `cwd` is used; a payload without a usable `cwd` blocks. Any explicit value, including
  `--repo .`, wins over the payload.
- stop_hook_active: never used to allow a stop. When it is true the agent is already continuing
  because of an earlier block; a non-PASS verdict blocks again with the current reason (fail
  closed). This can loop until the change verifies: the user can always interrupt the agent (Esc)
  or remove the hook from settings.
- The reason is bounded (MAX_ITEMS lines of at most MAX_LINE chars, MAX_REASON chars in total),
  control characters are stripped, and every piece taken from gate output, error messages or
  verdict reasons passes through runner.redact.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import sys
from typing import Any

from repomarshal import api
from repomarshal.model import ResultStatus, Verdict
from repomarshal.runner import redact

HOSTS = ("claude-code",)
STOP_EVENTS = ("Stop", "SubagentStop")
# SessionStart anchors BASE before the agent can commit anything (v0.2 review): without it, an
# agent committing directly on a default branch with no remote moves the automatic BASE along with
# its own commits. Recording the BASE at session start makes later commits part of the diff.
START_EVENTS = ("SessionStart",)

MAX_ITEMS = 12  # gate lines listed in a reason
MAX_LINE = 300  # characters per listed line
MAX_REASON = 4000  # characters in the whole reason

_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]")

_FOOTER = (
    "Run `repomarshal plan --explain` to see why each gate is required, fix the problems, then "
    "try to stop again (the hook re-runs `repomarshal done`)."
)

_WHAT_TO_DO = {
    Verdict.FAIL: "Required checks failed. Fix the code (not the checks) so they pass.",
    Verdict.UNKNOWN: (
        "Some required checks could not be verified, and unverified never counts as PASS. "
        "Make the missing checks runnable, or tell the user exactly what could not be verified "
        "(gates are configured in repomarshal.toml on the base branch)."
    ),
    Verdict.BLOCKED: (
        "RepoMarshal could not evaluate this change. Fix the cause below, or tell the user "
        "(an invalid repomarshal.toml must be fixed on the base branch)."
    ),
}


def _clean(text: str, limit: int = MAX_LINE) -> str:
    text = redact(_CONTROL.sub(" ", str(text)))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _last_output_line(result: Any) -> str | None:
    for line in reversed(tuple(result.output_tail or ())):
        cleaned = _clean(line, 160)
        if cleaned:
            return cleaned
    return None


def _block_reason(verdict: str, items: list[str], what_to_do: str, active: bool) -> str:
    head = [
        f"RepoMarshal completion verdict: {verdict}. Do not stop yet: the change is not verified.",
        what_to_do,
    ]
    tail = [_FOOTER]
    if active:
        tail.append(
            "This stop was already blocked before (stop_hook_active); RepoMarshal blocks again "
            "because the verdict is still not PASS. If you cannot resolve it, explain the blocker "
            "to the user, who can interrupt."
        )
    more = "- ... and {n} more (run `repomarshal done` to see all)"
    budget = MAX_REASON - len("\n".join(head + tail)) - len(more) - 8
    shown: list[str] = []
    for item in items[:MAX_ITEMS]:
        line = f"- {item}"
        if len(line) + 1 > budget:
            break
        shown.append(line)
        budget -= len(line) + 1
    if len(items) > len(shown):
        shown.append(more.format(n=len(items) - len(shown)))
    return "\n".join(head + shown + tail)[:MAX_REASON]


def _items_from_done(result: api.DoneResult) -> list[str]:
    tails: dict[str, str] = {}
    if result.record is not None:
        for r in result.record.results:
            if r.status == ResultStatus.FAIL:
                last = _last_output_line(r)
                if last:
                    tails[r.gate_id] = last
    items: list[str] = []
    for reason in result.reasons:
        item = _clean(reason)
        gate_id = reason.split(": ", 1)[0]
        if gate_id in tails and reason.startswith(f"{gate_id}: FAIL"):
            item = _clean(f"{reason}; last output: {tails[gate_id]}")
        items.append(item)
    return items


def _emit_block(reason: str) -> int:
    payload = json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)
    try:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return 0
    except Exception:  # noqa: BLE001 - stdout unusable: fall back to Claude Code's exit-2 channel
        with contextlib.suppress(Exception):
            sys.stderr.write(reason + "\n")
            sys.stderr.flush()
        return 2


def _blocked(problem: str, active: bool = False) -> int:
    return _emit_block(
        _block_reason("BLOCKED", [_clean(problem)], _WHAT_TO_DO[Verdict.BLOCKED], active)
    )


def _repo_from(payload: dict[str, Any], repo: str | None) -> str | None:
    if repo not in (None, ""):
        return repo
    # The payload `cwd` follows the agent's shell (`cd`), so the project root that Claude Code
    # sets for hooks wins when present (v0.2 fail-open review #3).
    project = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project.strip():
        return project
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd
    return None


def _session_start(payload: dict[str, Any], repo: str | None, base: str | None) -> int:
    """Record the BASE for this session (first-wins, widen-only anchor). Never blocks: a session
    start that cannot be anchored just leaves automatic resolution in place, and says so on
    stderr. Nothing is written to stdout (SessionStart stdout becomes agent context)."""
    from repomarshal import anchor, git

    target = _repo_from(payload, repo)
    try:
        root = git.repo_root(target or ".")
        floor, how = anchor.effective_base(root, None)
        anchor.remember(root, floor, explicit=False)
        sys.stderr.write(f"repomarshal: session BASE anchored at {floor} ({how})\n")
    except Exception as e:  # noqa: BLE001 - anchoring is best effort; Stop still fails closed
        sys.stderr.write(f"repomarshal: could not anchor BASE at session start: {e}\n")
    return 0


def _claude_code(payload_text: str, repo: str | None, base: str | None) -> int:
    try:
        payload = json.loads(payload_text)
    except (ValueError, TypeError) as e:
        return _blocked(f"the Stop hook payload on stdin is not valid JSON ({e})")
    if not isinstance(payload, dict):
        return _blocked("the Stop hook payload on stdin is not a JSON object")
    active = payload.get("stop_hook_active") is True
    event = payload.get("hook_event_name")
    if event in START_EVENTS:
        return _session_start(payload, repo, base)
    if event is not None and event not in STOP_EVENTS:
        return _blocked(
            f"unexpected hook_event_name {event!r} (configure this hook under Stop)", active
        )
    target = _repo_from(payload, repo)
    if target is None:
        return _blocked(
            "the Stop hook payload has no usable `cwd`; pass --repo PATH in the hook command",
            active,
        )

    # Only our JSON may reach stdout: anything printed while verifying goes to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        result = api.done(target, base=base)

    # PASS allows the stop only with a PASS evidence record behind it.
    if (
        result.verdict == Verdict.PASS
        and result.record is not None
        and result.record.verdict == Verdict.PASS
    ):
        return 0
    verdict = result.verdict if result.verdict in _WHAT_TO_DO else Verdict.UNKNOWN
    if result.verdict == Verdict.PASS:  # a PASS without a matching record is not trusted
        verdict = Verdict.UNKNOWN
    items = _items_from_done(result) or ["no reason was recorded"]
    return _emit_block(_block_reason(verdict.value, items, _WHAT_TO_DO[verdict], active))


DEFAULT_DEADLINE_S = 1700  # below the 1800 s timeout in the `init --print` snippet


def _install_last_resort_handlers() -> dict[int, Any]:
    """SIGTERM/SIGHUP (the host's timeout or a kill) and SIGALRM (our own deadline) must still
    block the stop: Python's default dispositions exit with no stdout, which Claude Code treats
    as a non-blocking error (v0.2 fail-open review #4). The handler writes the block decision
    with os.write and leaves immediately with os._exit, so it works mid-gate."""

    def handler(signum: int, _frame: Any) -> None:
        name = signal.Signals(signum).name
        reason = (
            "BLOCKED: repomarshal done did not finish "
            f"({'deadline reached' if signum == signal.SIGALRM else name + ' received'}); "
            "nothing was verified, so the stop is not allowed. Make the gates faster or raise "
            "REPOMARSHAL_HOOK_DEADLINE and the hook timeout, then stop again."
        )
        data = (json.dumps({"decision": "block", "reason": reason}) + "\n").encode()
        with contextlib.suppress(OSError):
            os.write(1, data)
        os._exit(0)

    previous: dict[int, Any] = {}
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
        with contextlib.suppress(ValueError, OSError):  # not in the main thread: skip
            previous[sig] = signal.signal(sig, handler)
    try:
        deadline = int(os.environ.get("REPOMARSHAL_HOOK_DEADLINE", DEFAULT_DEADLINE_S))
    except ValueError:
        deadline = DEFAULT_DEADLINE_S
    with contextlib.suppress(ValueError, OSError):
        signal.alarm(max(1, deadline))
    return previous


def _restore_handlers(previous: dict[int, Any]) -> None:
    with contextlib.suppress(ValueError, OSError):
        signal.alarm(0)
    for sig, old in previous.items():
        with contextlib.suppress(ValueError, OSError, TypeError):
            signal.signal(sig, old)


def _payload_from(payload: str | bytes | None) -> str:
    if payload is None:
        payload = sys.stdin.buffer.read()  # read INSIDE the fail-closed wrapper (review #5)
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return payload


def run(
    host: str,
    payload_text: str | bytes | None = None,
    repo: str | None = None,
    base: str | None = None,
) -> int:
    previous: dict[int, Any] = {}
    try:
        previous = _install_last_resort_handlers()
        text = _payload_from(payload_text)
        if host not in HOSTS:
            return _blocked(f"unsupported hook host {host!r} (supported: {', '.join(HOSTS)})")
        return _claude_code(text, repo, base)
    except BaseException as e:  # noqa: BLE001 - fail closed: every error blocks the stop
        try:
            return _blocked(f"internal error: {type(e).__name__}: {e}")
        except BaseException:  # noqa: BLE001 - last resort: the exit-2 blocking channel
            return 2
    finally:
        _restore_handlers(previous)
