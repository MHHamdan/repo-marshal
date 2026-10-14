"""Human override (owner: agent "override"; MISSION.md §54; roadmap SHOULD #12).

    collect(repo, plan, mode, env=None) -> tuple[Waiver, ...]
    collect_report(repo, plan, mode, env=None) -> (waivers, notes)   (collect + why)
    cli_waive(args) -> int                         (`repomarshal waive GATE --reason TEXT`)

Waivers record a human decision to accept a gate that could not be verified (UNKNOWN or
UNAVAILABLE). verdict.fold decides whether they count (BASE policy waivers_permit_pass); this
module only decides WHICH waivers are genuine. An agent must not be able to grant one.

A waiver target is a gate id ("backend:unit") or, for a kind-level decision, a kind
("security_scan"). Only targets of REQUIRED or UNAVAILABLE decisions in the plan are accepted.

Modes (api.done(..., waivers=mode)):
- "none": never collect waivers. Used by the Stop hook and the MCP server (they run inside the
  agent's session) and is the api default.
- "local": read waivers written by `repomarshal waive` under `.repomarshal/waivers/`. They are
  bound to the current tree digest and plan (a waiver for another tree is ignored). Creating one
  requires an interactive terminal (stdin and stdout are TTYs) and an explicit typed
  confirmation; identity = git user.email + OS user. Local waivers are a convenience for a
  human at a terminal, never evidence for CI: the files are plain JSON that anything able to
  write the working tree (including an agent, or an agent driving a pty) can produce. Local mode
  therefore returns nothing when GITHUB_ACTIONS=true or CI=true.
- "github": only in GitHub Actions on a pull_request (or pull_request_review) event. Read the
  PR's label events, comments and reviews via the GitHub REST API with the workflow token
  (GITHUB_TOKEN / GITHUB_REPOSITORY / GITHUB_EVENT_PATH / GITHUB_API_URL). Local waiver files are
  ignored. A waiver is genuine when:
    * a label `repomarshal-waive:<target>` is currently on the PR and its most recent `labeled`
      event was by a human who is a CODEOWNER for the target (CODEOWNERS read at BASE) or has
      admin/maintain permission on the repository, OR the latest review of a CODEOWNER for the
      target is APPROVED, was submitted for the PR's current head commit, and its body contains a
      line `repomarshal-waive <target>: <reason>`;
    * the granter is not a bot (account type must be "User") and not the PR author, unless the
      BASE policy sets completion.allow_author_waivers (v0.4, for solo-maintainer repositories);
    * nothing was done through a GitHub App (v0.4): a coding agent acting through an app under
      the maintainer's own account has the maintainer's login, so the login proves nothing. The
      labeled event and the reason comment must carry `performed_via_github_app: null`; an app
      value, or a missing or unreadable field, rejects the waiver (fail closed). The issue
      events and issue comments endpoints expose the field. Pull request reviews do not, on any
      GitHub API (REST /pulls/{n}/reviews, the timeline `reviewed` event, GraphQL
      PullRequestReview), so a review can never be shown to be human and review waivers are
      always rejected;
    * the reason is non-empty. A label waiver takes its reason from the latest PR comment by the
      same human with a line `repomarshal-waive <target>: <reason>`; if none, the waiver is
      rejected. (Label descriptions are NOT used: they are repository-wide and any writer can
      edit them.)
  CODEOWNERS for a target = the users on the `*` rule, plus users who own every path the target
  covers (last matching rule wins per path, as on GitHub). Team (@org/team) and e-mail owners are
  not resolved and never count. Any network or API error yields NO waivers (fail closed); a
  failed permission lookup only means "no admin/maintain permission".

collect() never raises: an unexpected error yields no waivers and a note. Notes (why a waiver was
rejected or why none were collected) are written to stderr; collect_report() returns them.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from repomarshal import git
from repomarshal.model import (
    DecisionStatus,
    Plan,
    RepoMarshalError,
    Waiver,
    WaiverSource,
    sha256_json,
)

MODES = ("none", "local", "github")

WAIVERS_DIR = ".repomarshal/waivers"
LOCAL_SCHEMA = "repomarshal.waiver/1"
LABEL_PREFIX = "repomarshal-waive:"
LINE_KEYWORD = "repomarshal-waive"
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
DEFAULT_API_URL = "https://api.github.com"
PR_EVENTS = frozenset({"pull_request", "pull_request_review"})

TIMEOUT_S = 15.0  # per request
MAX_PAGES = 20  # per paginated listing (2000 items); more is an error, not a truncation
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_LOCAL_FILE_BYTES = 64 * 1024
MAX_LOCAL_FILES = 1000

_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")


# --------------------------------------------------------------------------- public entry points


def collect(
    repo: Path, plan: Plan, mode: str, env: Mapping[str, str] | None = None
) -> tuple[Waiver, ...]:
    """Genuine waivers for this plan. Mode "none" always returns ()."""
    waivers, notes = collect_report(repo, plan, mode, env)
    for note in notes:
        print(f"repomarshal waivers: {note}", file=sys.stderr)
    return waivers


def collect_report(
    repo: Path,
    plan: Plan,
    mode: str,
    env: Mapping[str, str] | None = None,
    extra_targets: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[tuple[Waiver, ...], tuple[str, ...]]:
    """(genuine waivers, notes). Never raises; any unexpected error yields no waivers."""
    if mode == "none":
        return (), ()
    environ: Mapping[str, str] = os.environ if env is None else env
    notes: list[str] = []
    try:
        if mode == "local":
            found = _collect_local(Path(repo), plan, environ, notes)
        elif mode == "github":
            found = _collect_github(Path(repo), plan, environ, notes, extra_targets)
        else:
            return (), (f"unknown waiver mode {mode!r}; no waivers used",)
    except _ApiError as e:
        return (), (*notes, f"GitHub API error, no waivers used (fail closed): {e}")
    except Exception as e:  # noqa: BLE001 - a waiver can only help a verdict: fail closed
        return (), (*notes, f"could not collect waivers, none used: {type(e).__name__}: {e}")
    ordered = sorted(found, key=lambda w: (w.target, w.source.value, w.identity, w.granted_at))
    return tuple(ordered), tuple(notes)


# --------------------------------------------------------------------------- plan targets


def _targets(plan: Plan) -> dict[str, tuple[str, ...]]:
    """Waivable targets of the plan -> the repository paths each one covers."""
    gates = {g.id: g for g in plan.inventory}
    signals = {s.id: s for s in plan.signals}
    changed: list[str] = []
    for f in plan.change.files:
        changed.append(f.path)
        if f.old_path:
            changed.append(f.old_path)
    out: dict[str, tuple[str, ...]] = {}
    for d in plan.gates:
        if d.status not in (DecisionStatus.REQUIRED, DecisionStatus.UNAVAILABLE):
            continue
        if d.gate_id is not None:
            gate = gates.get(d.gate_id)
            paths = [p for p in changed if gate is None or gate.covers(p)]
            target = d.gate_id
        else:
            paths = []
            for r in d.reasons:
                sig = signals.get(r.signal) if r.signal else None
                if sig is not None:
                    paths.extend(sig.files)
            paths = paths or list(changed)
            target = d.kind.value
        out[target] = tuple(sorted(set(out.get(target, ()) + tuple(paths))))
    return out


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _prose_lines(body: str) -> list[str]:
    """The lines of a Markdown comment that a person wrote as text: no fenced or indented code,
    no block quotes, no HTML comments. A waiver line quoted as an example, or quoted from
    someone else, is not a statement (v0.4 review #7)."""
    body = re.sub(r"<!--.*?(?:-->|$)", "", body, flags=re.DOTALL)
    out: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        m = _FENCE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        if line.startswith(("    ", "\t")) or line.lstrip().startswith(">"):
            continue
        out.append(line)
    return out


def _reason_from(body: str | None, target: str) -> str:
    """The reason on the first prose line `repomarshal-waive <target>: <reason>` in `body`, or
    ""."""
    if not isinstance(body, str):
        return ""
    pattern = re.compile(
        rf"^ {{0,3}}{re.escape(LINE_KEYWORD)}[ \t]+{re.escape(target)}[ \t]*:[ \t]*(\S.*?)\s*$"
    )
    for line in _prose_lines(body):
        m = pattern.match(line)
        if m:
            return m.group(1)
    return ""


# --------------------------------------------------------------------------- local mode


def _in_ci(environ: Mapping[str, str]) -> bool:
    return environ.get("GITHUB_ACTIONS", "").lower() == "true" or environ.get("CI", "").lower() in (
        "true",
        "1",
    )


def _waivers_dir(root: Path) -> Path | None:
    d = root / WAIVERS_DIR
    if not d.is_dir() or d.is_symlink():
        return None
    if not d.resolve().is_relative_to(root.resolve()):
        return None
    return d


def _collect_local(
    root: Path, plan: Plan, environ: Mapping[str, str], notes: list[str]
) -> list[Waiver]:
    if _in_ci(environ):
        notes.append("local waivers are never used in CI (use --waivers github); none used")
        return []
    d = _waivers_dir(root)
    if d is None:
        return []
    targets = _targets(plan)
    out: list[Waiver] = []
    files = sorted(d.glob("*.json"))[:MAX_LOCAL_FILES]
    for path in files:
        rel = path.relative_to(root).as_posix()
        w = _load_local(path, rel)
        if w is None:
            notes.append(f"ignored malformed local waiver {rel}")
            continue
        if w.tree_digest != plan.change.tree_digest:
            notes.append(f"ignored local waiver {rel}: granted for another tree")
            continue
        if w.target not in targets:
            notes.append(f"ignored local waiver {rel}: {w.target!r} is not a waivable target")
            continue
        out.append(w)
    return out


def _load_local(path: Path, rel: str) -> Waiver | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        raw = path.read_bytes()
        if len(raw) > MAX_LOCAL_FILE_BYTES:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != LOCAL_SCHEMA:
        return None
    if data.get("source") != WaiverSource.LOCAL_CLI.value:
        return None
    fields = {}
    for key in ("target", "reason", "identity", "granted_at", "tree_digest"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        fields[key] = value.strip()
    return Waiver(
        target=fields["target"],
        reason=fields["reason"],
        identity=fields["identity"],
        source=WaiverSource.LOCAL_CLI,
        granted_at=fields["granted_at"],
        reference=rel,
        tree_digest=fields["tree_digest"],
    )


def cli_waive(args: argparse.Namespace) -> int:
    """`repomarshal waive GATE --reason TEXT [--repo PATH]`: interactive only. Exit 0 or 1."""

    def refuse(msg: str) -> int:
        print(f"repomarshal waive: {msg}", file=sys.stderr)
        return 1

    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return refuse(
            "refused: a waiver must be granted by a human at an interactive terminal "
            "(stdin and stdout must be TTYs). In CI, waivers come from GitHub labels or "
            "CODEOWNER reviews (see docs/guides/waivers.md)."
        )
    if _in_ci(os.environ):
        return refuse("refused: local waivers are never used in CI")
    target = str(args.gate).strip()
    reason = " ".join(str(args.reason).split())
    if not target or not reason:
        return refuse("refused: a gate id and a non-empty --reason are required")
    try:
        from repomarshal import api  # lazy: api imports this module

        root = git.repo_root(args.repo)
        plan = api.plan(root)
        r = git._git(root, "config", "--get", "user.email", check=False)
        email = r.stdout.strip() if r.returncode == 0 else ""
        os_user = getpass.getuser()
        digest = git.tree_digest(root, base=plan.change.base)
    except (RepoMarshalError, OSError, KeyError) as e:
        return refuse(f"refused: {e}")
    if not email:
        return refuse("refused: set `git config user.email` so the waiver names who granted it")
    targets = _targets(plan)
    if target not in targets:
        listed = ", ".join(sorted(targets)) or "(none)"
        return refuse(
            f"refused: {target!r} is not a waivable target of the plan. Targets: {listed}"
        )
    identity = f"{email} (os user {os_user})"
    print(f"Waive {target} for tree {digest}")
    print(f"  reason:   {reason}")
    print(f"  identity: {identity}")
    print("Only an UNKNOWN or UNAVAILABLE outcome can be waived; a FAIL stays FAIL.")
    try:
        typed = input("Type the gate id again to confirm: ").strip()
    except (EOFError, KeyboardInterrupt):
        typed = ""
    if typed != target:
        return refuse("refused: confirmation did not match the gate id; nothing written")
    record = {
        "schema": LOCAL_SCHEMA,
        "target": target,
        "reason": reason,
        "identity": identity,
        "source": WaiverSource.LOCAL_CLI.value,
        "granted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tree_digest": digest,
    }
    d = root / WAIVERS_DIR
    try:
        d.mkdir(parents=True, exist_ok=True)
        if d.is_symlink() or not d.resolve().is_relative_to(root.resolve()):
            return refuse(f"refused: {WAIVERS_DIR} resolves outside the repository")
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        name = f"{stamp}-{sha256_json(record).split(':', 1)[1][:12]}.json"
        out = d / name
        out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as e:
        return refuse(f"could not write the waiver: {e}")
    print(f"Wrote {out.relative_to(root).as_posix()}")
    print("Local waivers are a convenience for this terminal; CI ignores them.")
    return 0


# --------------------------------------------------------------------------- CODEOWNERS


def _glob_to_regex(pattern: str) -> re.Pattern[str] | None:
    """A CODEOWNERS (gitignore-style) pattern as a regex over repo-relative POSIX paths."""
    if not pattern or pattern.startswith("!") or "\\" in pattern:
        return None
    dir_only = pattern.endswith("/")
    body = pattern.strip("/")
    if not body:
        return None
    anchored = pattern.startswith("/") or "/" in body
    out = []
    i = 0
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        elif body[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    prefix = "^" if anchored else "^(?:.*/)?"
    suffix = "/.*$" if dir_only else "(?:/.*)?$"
    return re.compile(prefix + "".join(out) + suffix)


class CodeOwners:
    """CODEOWNERS at BASE. Only individual @user owners are kept (lowercased)."""

    def __init__(self, text: str = ""):
        self.rules: list[tuple[re.Pattern[str], frozenset[str]]] = []
        self.everyone: set[str] = set()  # owners on the `*` rule
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            owners: set[str] = set()
            for tok in tokens[1:]:
                if tok.startswith("#"):
                    break
                if tok.startswith("@") and "/" not in tok and _LOGIN_RE.fullmatch(tok[1:]):
                    owners.add(tok[1:].lower())
            regex = _glob_to_regex(tokens[0])
            if regex is None:
                continue
            self.rules.append((regex, frozenset(owners)))
            if tokens[0] in ("*", "**", "/**"):
                self.everyone = set(owners)  # a later `*` rule replaces an earlier one

    @classmethod
    def at_base(cls, repo: Path, base: str | None) -> CodeOwners:
        for path in CODEOWNERS_PATHS:
            raw = git.read_at(repo, base, path)
            if raw is not None:
                return cls(raw.decode("utf-8", "replace"))
        return cls()

    def owners_of(self, path: str) -> frozenset[str]:
        found: frozenset[str] = frozenset()
        for regex, owners in self.rules:
            if regex.match(path):
                found = owners  # last match wins
        return found

    def owns(self, login: str, paths: tuple[str, ...]) -> bool:
        who = login.lower()
        if who in self.everyone:
            return True
        return bool(paths) and all(who in self.owners_of(p) for p in paths)


# --------------------------------------------------------------------------- GitHub API


class _ApiError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a redirect becomes an HTTPError: never forward the token elsewhere


class _GitHub:
    def __init__(self, api_url: str, token: str):
        self.api = api_url.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(_NoRedirect())

    def get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        url = self.api + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(  # noqa: S310 - scheme checked in _collect_github
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "repomarshal-waivers",
            },
        )
        try:
            with self.opener.open(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as e:
            raise _ApiError(f"GET {path}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise _ApiError(f"GET {path}: {getattr(e, 'reason', e)}") from e
        if len(raw) > MAX_RESPONSE_BYTES:
            raise _ApiError(f"GET {path}: response too large")
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as e:
            raise _ApiError(f"GET {path}: invalid JSON") from e

    def paged(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            data = self.get(path, {"per_page": 100, "page": page})
            if not isinstance(data, list):
                raise _ApiError(f"GET {path}: expected a list")
            items.extend(x for x in data if isinstance(x, dict))
            if len(data) < 100:
                return items
        raise _ApiError(f"GET {path}: more than {MAX_PAGES} pages")

    def has_admin_or_maintain(self, repo: str, login: str) -> bool:
        """Repository permission check; any error (403 included) means no permission."""
        try:
            data = self.get(f"/repos/{repo}/collaborators/{urllib.parse.quote(login)}/permission")
        except _ApiError:
            return False
        if not isinstance(data, dict):
            return False
        role = str(data.get("role_name") or "").lower()
        perm = str(data.get("permission") or "").lower()
        return role in ("admin", "maintain") or perm == "admin"


def _human(user: Any) -> str | None:
    """The login of a human account, or None (bots, apps, missing or odd data)."""
    if not isinstance(user, dict) or user.get("type") != "User":
        return None
    login = user.get("login")
    if not isinstance(login, str) or not _LOGIN_RE.fullmatch(login) or login.endswith("[bot]"):
        return None
    return login


def _not_via_app(obj: Any) -> bool:
    """True only when the API states the action was NOT made through a GitHub App: the field
    `performed_via_github_app` is present and null. An app object, or a missing field (e.g. the
    reviews API, which never sends it), is a rejection (v0.4, fail closed)."""
    return (
        isinstance(obj, dict)
        and "performed_via_github_app" in obj
        and (obj["performed_via_github_app"] is None)
    )


def _app_name(obj: Any) -> str:
    app = obj.get("performed_via_github_app") if isinstance(obj, dict) else None
    if isinstance(app, dict):
        return str(app.get("slug") or app.get("name") or "a GitHub App")
    return "a GitHub App"


def _collect_github(
    root: Path,
    plan: Plan,
    environ: Mapping[str, str],
    notes: list[str],
    extra_targets: Mapping[str, tuple[str, ...]] | None = None,
) -> list[Waiver]:
    if environ.get("GITHUB_ACTIONS", "").lower() != "true":
        notes.append("github waivers are only read inside GitHub Actions; none used")
        return []
    event_name = environ.get("GITHUB_EVENT_NAME", "")
    if event_name not in PR_EVENTS:
        notes.append(f"github waivers need a pull_request event, not {event_name!r}; none used")
        return []
    event_path = environ.get("GITHUB_EVENT_PATH", "")
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else None
    except (OSError, ValueError):
        payload = None
    pr = payload.get("pull_request") if isinstance(payload, dict) else None
    if not isinstance(pr, dict):
        notes.append("no pull_request in the GitHub event payload; none used")
        return []
    number = pr.get("number")
    author = (pr.get("user") or {}).get("login") if isinstance(pr.get("user"), dict) else None
    head_sha = (pr.get("head") or {}).get("sha") if isinstance(pr.get("head"), dict) else None
    repo_name = environ.get("GITHUB_REPOSITORY", "")
    if not repo_name and isinstance(payload.get("repository"), dict):
        repo_name = str(payload["repository"].get("full_name") or "")
    if (
        not isinstance(number, int)
        or not isinstance(author, str)
        or not author
        or not _REPO_RE.fullmatch(repo_name)
    ):
        notes.append("incomplete pull_request data (number, author, repository); none used")
        return []
    token = environ.get("GITHUB_TOKEN", "")
    if not token:
        notes.append("GITHUB_TOKEN is not set; none used")
        return []
    api_url = environ.get("GITHUB_API_URL") or DEFAULT_API_URL
    if urllib.parse.urlsplit(api_url).scheme not in ("https", "http"):
        notes.append(f"GITHUB_API_URL {api_url!r} is not http(s); none used")
        return []

    # extra_targets (v0.5): binding exceptions the trusted CI judge may accept, e.g. ci-workflow.
    targets = {**_targets(plan), **(extra_targets or {})}
    owners = CodeOwners.at_base(root, plan.change.base)
    gh = _GitHub(api_url, token)
    out: list[Waiver] = []
    allow_author = bool(getattr(plan.policy, "allow_author_waivers", False))
    out += _label_waivers(gh, repo_name, number, author, targets, owners, notes, allow_author)
    out += _review_waivers(
        gh, repo_name, number, author, head_sha, targets, owners, notes, allow_author
    )
    return out


def _label_waivers(
    gh: _GitHub,
    repo: str,
    number: int,
    author: str,
    targets: dict[str, tuple[str, ...]],
    owners: CodeOwners,
    notes: list[str],
    allow_author: bool = False,
) -> list[Waiver]:
    events = gh.paged(f"/repos/{repo}/issues/{number}/events")
    # GitHub label names are case-insensitive: key everything by the lower-cased name.
    last: dict[str, dict[str, Any]] = {}  # label name -> latest labeled/unlabeled event
    for ev in events:
        if ev.get("event") not in ("labeled", "unlabeled"):
            continue
        label = ev.get("label")
        name = label.get("name") if isinstance(label, dict) else None
        if isinstance(name, str) and name.lower().startswith(LABEL_PREFIX):
            last[name.lower()] = ev
    applied = {n: ev for n, ev in last.items() if ev.get("event") == "labeled"}
    if not applied:
        return []
    # The events alone can miss a removal (a renamed or deleted label): the label must also be on
    # the PR right now (v0.4 review #6).
    current = {
        str(lb.get("name")).lower()
        for lb in gh.paged(f"/repos/{repo}/issues/{number}/labels")
        if isinstance(lb.get("name"), str)
    }
    for name in sorted(set(applied) - current):
        notes.append(f"label {name!r}: not on the pull request now; ignored")
        del applied[name]
    comments = gh.paged(f"/repos/{repo}/issues/{number}/comments")
    out: list[Waiver] = []
    for name in sorted(applied):
        ev = applied[name]
        target = name[len(LABEL_PREFIX) :].strip()
        if target not in targets:
            notes.append(f"label {name!r}: not a waivable target of this plan")
            continue
        login = _human(ev.get("actor"))
        if login is None:
            notes.append(f"label {name!r}: applied by a bot or unknown account; rejected")
            continue
        if not _not_via_app(ev):
            how = (
                f"through {_app_name(ev)}"
                if ev.get("performed_via_github_app")
                else "with no performed_via_github_app field"
            )
            notes.append(f"label {name!r}: applied {how}, not by a person in GitHub; rejected")
            continue
        if login.lower() == author.lower() and not allow_author:
            notes.append(
                f"label {name!r}: applied by the PR author {login}, and the BASE policy does "
                "not set completion.allow_author_waivers; rejected"
            )
            continue
        if not owners.owns(login, targets[target]) and not gh.has_admin_or_maintain(repo, login):
            notes.append(
                f"label {name!r}: {login} is neither a CODEOWNER for it nor an admin/maintainer"
            )
            continue
        reason, ref, app_reason, edited = "", "", False, False
        for c in comments:  # chronological: keep the latest matching comment
            if (_human(c.get("user")) or "").lower() != login.lower():
                continue
            found = _reason_from(c.get("body"), target)
            if not found:
                continue
            if not _not_via_app(c):
                app_reason = True  # an app-made comment never supplies a reason
                continue
            if c.get("updated_at") != c.get("created_at") or not c.get("created_at"):
                # performed_via_github_app describes who CREATED the comment. An edit can come
                # from an app acting as the same user, so an edited comment is not a reason
                # (v0.4 review #5).
                edited = True
                continue
            reason, ref = found, str(c.get("html_url") or "")
        if not reason and edited:
            notes.append(
                f"label {name!r}: the reason comment by {login} was edited after it was posted; "
                "post a new comment instead; rejected"
            )
            continue
        if not reason and app_reason:
            notes.append(
                f"label {name!r}: the only reason comment(s) by {login} were made through a "
                "GitHub App or lack performed_via_github_app; rejected"
            )
            continue
        if not reason:
            notes.append(
                f"label {name!r}: no PR comment by {login} with "
                f"'{LINE_KEYWORD} {target}: <reason>'; rejected"
            )
            continue
        out.append(
            Waiver(
                target=target,
                reason=reason,
                identity=login,
                source=WaiverSource.GITHUB_LABEL,
                granted_at=str(ev.get("created_at") or ""),
                reference=ref or f"{repo}#{number} label {name}",
            )
        )
    return out


def _review_waivers(
    gh: _GitHub,
    repo: str,
    number: int,
    author: str,
    head_sha: Any,
    targets: dict[str, tuple[str, ...]],
    owners: CodeOwners,
    notes: list[str],
    allow_author: bool = False,
) -> list[Waiver]:
    reviews = gh.paged(f"/repos/{repo}/pulls/{number}/reviews")
    latest: dict[str, dict[str, Any]] = {}
    for rv in reviews:  # chronological
        login = _human(rv.get("user"))
        if login is None or rv.get("state") == "PENDING":
            continue
        latest[login.lower()] = rv
    out: list[Waiver] = []
    for key in sorted(latest):
        rv = latest[key]
        login = _human(rv.get("user")) or key
        asked = [t for t in sorted(targets) if _reason_from(rv.get("body"), t)]
        if not asked:
            continue
        if rv.get("state") != "APPROVED":
            notes.append(f"review by {login}: not an approval; its waiver lines are ignored")
            continue
        if not _not_via_app(rv):
            notes.append(
                f"review by {login}: the reviews API does not say whether it was made through a "
                "GitHub App, so it cannot be shown to be a person's; review waivers are rejected "
                f"(use a '{LABEL_PREFIX}<gate>' label and a reason comment)"
            )
            continue
        if login.lower() == author.lower() and not allow_author:
            continue  # GitHub forbids self-approval anyway
        if not isinstance(head_sha, str) or rv.get("commit_id") != head_sha:
            notes.append(f"review by {login}: approved an older commit; waiver lines ignored")
            continue
        for target in asked:
            if not owners.owns(login, targets[target]):
                notes.append(f"review by {login}: not a CODEOWNER for {target}; rejected")
                continue
            out.append(
                Waiver(
                    target=target,
                    reason=_reason_from(rv.get("body"), target),
                    identity=login,
                    source=WaiverSource.GITHUB_REVIEW,
                    granted_at=str(rv.get("submitted_at") or ""),
                    reference=str(rv.get("html_url") or f"{repo}#{number} review"),
                )
            )
    return out
