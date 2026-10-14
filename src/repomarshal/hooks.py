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
- Loop termination (v0.3). Claude Code sets stop_hook_active on a Stop that follows a block.
  Blocking every non-PASS stop loops forever when the agent cannot fix the change (a gate is
  UNAVAILABLE). The ONLY allow on a non-PASS verdict is an exact repeat, and it is never silent:

      allow  <=>  stop_hook_active is True (the JSON boolean)
             and the payload has a non-empty string session_id
             and api.done produced an evidence record whose verdict equals the result verdict,
                 which is FAIL, UNKNOWN or BLOCKED
             and the state file holds a valid entry for (session_id, hook_event_name) with the
                 same verdict, the same evidence digest and count >= MIN_IDENTICAL_BLOCKS (1)

  i.e. the stop is allowed on the SECOND identical stop: the first one always blocked. The
  evidence digest is sha256 over plan_hash, tree_digest_after, the verdict, the sorted
  (gate_id, status) pairs of the gate results and the sorted gate/kind labels of the verdict
  reasons (never record_digest: it covers created_at). A changed tree, plan, verdict or gate set
  gives a different digest and blocks again (and restarts the count). An allowed stop prints
  {"systemMessage": "... UNVERIFIED ..."} (shown to the USER) built from the CURRENT run's
  failing/unknown gates, with no decision, exit 0. A malformed payload, a missing evidence
  record, an unreadable or invalid state entry, or any error blocks as before.
- State (`.repomarshal/state/hook-blocks.json`, written after every non-PASS block):
  {"version": 1, "sessions": {"<session_id>": {"event": "Stop", "verdict": "UNKNOWN",
  "digest": "<64 hex>", "count": 2, "updated_at": "<UTC ISO-8601>"}}}, at most MAX_SESSIONS
  entries (oldest dropped). The agent can write this file. The design makes that harmless: the
  state is consulted only when stop_hook_active is True (set by Claude Code, not the agent); it
  can only turn a block into an allowed stop for the digest api.done computes NOW, so the most a
  forged entry achieves is what a second, unchanged stop achieves anyway (one block earlier): an
  allowed stop whose systemMessage tells the user the change is UNVERIFIED and lists the real
  current gates. It can never yield a silent stop or a PASS-looking one, and never let a FAIL
  through unless the entry says FAIL with the current digest. No HMAC is used: any key the hook
  can read, the agent (same user) can read too, so it would add no real protection.
- The reason is bounded (MAX_ITEMS lines of at most MAX_LINE chars, MAX_REASON chars in total),
  control characters are stripped, and every piece taken from gate output, error messages or
  verdict reasons passes through runner.redact.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from typing import Any

from repomarshal import anchor, api
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
MAX_MESSAGE = 4000  # characters in an UNVERIFIED systemMessage

STATE_FILE = ".repomarshal/state/hook-blocks.json"
STATE_VERSION = 1
MAX_SESSIONS = 64  # entries kept in the state file
MIN_IDENTICAL_BLOCKS = 1  # identical blocks required before the repeat stop is allowed
_LOOP_VERDICTS = (Verdict.FAIL, Verdict.UNKNOWN, Verdict.BLOCKED)
_DIGEST = re.compile(r"[0-9a-f]{64}")

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
            "because the verdict is still not PASS and something changed since the last block "
            "(or no earlier block is recorded). If you cannot resolve it, explain the blocker "
            "to the user."
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


def _emit_unverified(message: str) -> int:
    """Allow the stop, but tell the USER (systemMessage) that the change is UNVERIFIED. If stdout
    is unusable the stop is blocked instead (exit 2): an allowed stop is never silent."""
    payload = json.dumps({"systemMessage": message}, ensure_ascii=False)
    try:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return 0
    except Exception:  # noqa: BLE001 - could not tell the user: do not allow the stop
        with contextlib.suppress(Exception):
            sys.stderr.write(message + "\n")
            sys.stderr.flush()
        return 2


def _unverified_message(verdict: Verdict, items: list[str], count: int) -> str:
    head = [
        f"RepoMarshal: this change is UNVERIFIED (verdict {verdict.value}). The agent stopped "
        f"again with no change after {count} identical blocked stop(s), so RepoMarshal let it "
        "stop. Do not treat the work as verified.",
        "Failing and unknown gates:",
    ]
    tail = [
        "Run `repomarshal plan --explain` to see why each gate is required, and "
        "`repomarshal done` to re-check."
    ]
    budget = MAX_MESSAGE - len("\n".join(head + tail)) - 64
    shown: list[str] = []
    for item in items[:MAX_ITEMS]:
        line = f"- {item}"
        if len(line) + 1 > budget:
            break
        shown.append(line)
        budget -= len(line) + 1
    if len(items) > len(shown):
        shown.append(f"- ... and {len(items) - len(shown)} more")
    return "\n".join(head + shown + tail)[:MAX_MESSAGE]


# ---------------------------------------------------------------------- block-loop state


def evidence_digest(record: Any) -> str:
    """Digest of what the verdict rests on: plan, tree, verdict, gate statuses and the labels of
    the unverified gates. Excludes created_at (so not record_digest)."""
    labels = sorted({str(r).split(": ", 1)[0] for r in record.verdict_reasons})
    body = {
        "plan_hash": record.plan_hash,
        "tree_digest_after": record.tree_digest_after,
        "verdict": Verdict(record.verdict).value,
        "results": sorted([str(r.gate_id), ResultStatus(r.status).value] for r in record.results),
        "unverified": labels,
    }
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_state(root: Path) -> dict[str, Any]:
    """The `sessions` map, or {} when the file is missing, unreadable or invalid."""
    data = anchor.read_small_json(root / STATE_FILE)  # bounded, no symlink (review_v03 #4)
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return {}
    sessions = data.get("sessions")
    return dict(sessions) if isinstance(sessions, dict) else {}


def _valid_entry(entry: Any, event: str) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    verdict, digest, count = entry.get("verdict"), entry.get("digest"), entry.get("count")
    if (
        entry.get("event") != event
        or verdict not in {v.value for v in _LOOP_VERDICTS}
        or not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
        or type(count) is not int
        or count < 1
    ):
        return None
    return entry


def _save_state(root: Path, sessions: dict[str, Any]) -> None:
    if len(sessions) > MAX_SESSIONS:
        keep = sorted(
            sessions.items(),
            key=lambda kv: str(kv[1].get("updated_at", "")) if isinstance(kv[1], dict) else "",
        )[-MAX_SESSIONS:]
        sessions = dict(keep)
    path = root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"version": STATE_VERSION, "sessions": sessions}, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, path)


class _Loop:
    """Previous-block bookkeeping for one Stop. Every failure degrades to "no previous block"
    (which blocks); nothing here can turn a block into a silent stop."""

    def __init__(self, target: str, payload: dict[str, Any], result: api.DoneResult):
        self.root: Path | None = None
        self.session: str | None = None
        self.event = str(payload.get("hook_event_name") or "Stop")
        self.digest: str | None = None
        self.verdict = result.verdict
        sid = payload.get("session_id")
        record = result.record
        if not (isinstance(sid, str) and sid.strip()) or record is None:
            return
        if result.verdict not in _LOOP_VERDICTS or record.verdict != result.verdict:
            return
        from repomarshal import git

        try:
            self.root = git.repo_root(target)
            self.digest = evidence_digest(record)
        except Exception:  # noqa: BLE001 - no digest: never allows, still blocks
            self.root = self.digest = None
            return
        self.session = sid

    def previous(self) -> dict[str, Any] | None:
        if self.root is None or self.session is None:
            return None
        try:
            return _valid_entry(_load_state(self.root).get(self.session), self.event)
        except Exception:  # noqa: BLE001 - unreadable state: no previous block
            return None

    def repeat_count(self) -> int:
        """Identical blocks recorded before this stop (0 when anything does not match)."""
        prev = self.previous()
        if prev is None or prev["verdict"] != self.verdict.value or prev["digest"] != self.digest:
            return 0
        return int(prev["count"])

    def record_block(self) -> None:
        if self.root is None or self.session is None or self.digest is None:
            return
        with contextlib.suppress(Exception):
            sessions = _load_state(self.root)
            count = self.repeat_count() + 1
            sessions[self.session] = {
                "event": self.event,
                "verdict": self.verdict.value,
                "digest": self.digest,
                "count": count,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _save_state(self.root, sessions)

    def forget(self) -> None:
        if self.root is None or self.session is None:
            return
        with contextlib.suppress(Exception):
            sessions = _load_state(self.root)
            if sessions.pop(self.session, None) is not None:
                _save_state(self.root, sessions)


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

    loop = _Loop(target, payload, result)
    if active and verdict == result.verdict and loop.digest is not None:
        count = loop.repeat_count()
        if count >= MIN_IDENTICAL_BLOCKS:
            loop.forget()  # the next non-PASS stop starts a new cycle with a block
            return _emit_unverified(_unverified_message(verdict, items, count))
    loop.record_block()
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
