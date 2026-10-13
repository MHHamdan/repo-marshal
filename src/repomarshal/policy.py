"""Policy loading (owner: agent "inventory"; roadmap M4; architecture §7.2; ADR-005).

`repomarshal.toml` is read from BASE only (git.read_at), never from the working tree. Missing file
-> the default Policy (source "default"). Present but invalid -> raise PolicyInvalid listing every
error with its key path. Unknown keys are errors (strict). Parsed with stdlib tomllib.

Accepted format (version 1):

    version = 1                         # required when the file exists
    [context]
    token_budget = 12000                # int > 0
    [skills]
    max = 3                             # int 0..10
    [completion]
    waivers_permit_pass = false         # bool
    [gates.<id>]                        # id: [a-z0-9][a-z0-9._-]*
    kind = "security_scan"              # a model.GateKind value
    argv = ["bandit", "-r", "app"]      # non-empty array of strings; a string is an error
    scope = "backend/"                  # optional, default ""; "" or relative "dir/";
                                        #   no "..", no leading "/"
    cwd = "backend"                     # optional, default = scope without trailing slash
    cost = "expensive"                  # optional, default model.DEFAULT_COST[kind]
    timeout_s = 600                     # optional int 1..86400
    [[signals]]
    id = "custom.payments"              # must start with "custom."
    domain = "security"
    risk = "elevated"                   # low|standard|elevated
    paths = ["**/payments/**"]          # non-empty array of globs
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from repomarshal import git
from repomarshal.model import (
    DEFAULT_COST,
    Cost,
    Gate,
    GateKind,
    Policy,
    PolicyInvalid,
    Risk,
    SignalRuleSpec,
    sha256_bytes,
    sha256_json,
)

POLICY_FILE = "repomarshal.toml"

GATE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
TOP_KEYS = frozenset({"version", "context", "skills", "completion", "gates", "signals"})
SECTION_KEYS = {
    "context": frozenset({"token_budget"}),
    "skills": frozenset({"max"}),
    "completion": frozenset({"waivers_permit_pass"}),
}
GATE_KEYS = frozenset({"kind", "argv", "scope", "cwd", "cost", "timeout_s"})
SIGNAL_KEYS = frozenset({"id", "domain", "risk", "paths"})
MAX_TIMEOUT_S = 86_400


def load_policy(repo: Path, base: str | None) -> Policy:
    """Policy from `repomarshal.toml` at BASE (see module docstring).

    Policy.source = f"base:{base}:repomarshal.toml" (or "default"); Policy.digest = sha256 of the
    raw bytes (model.sha256_bytes), or sha256_bytes(b"default"). Policy gates are model.Gate
    objects with provider "command", source "repomarshal.toml#gates.<id>", and
    definition_digest = sha256_json of their table.
    """
    raw = git.read_at(repo, base, POLICY_FILE)
    if raw is None:
        return Policy(source="default", digest=sha256_bytes(b"default"))
    data = parse_policy_bytes(raw)
    return _build(data, source=f"base:{base}:{POLICY_FILE}", digest=sha256_bytes(raw))


def parse_policy_bytes(raw: bytes) -> dict[str, Any]:
    """Decode and parse the policy TOML; raise PolicyInvalid on any decode or syntax error."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PolicyInvalid([f"{POLICY_FILE}: not valid UTF-8 ({e.reason})"]) from e
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise PolicyInvalid([f"{POLICY_FILE}: invalid TOML: {e}"]) from e


def _build(data: dict[str, Any], source: str, digest: str) -> Policy:
    errors: list[str] = []
    for key in sorted(set(data) - TOP_KEYS):
        errors.append(f"{key}: unknown key")

    version = data.get("version")
    if "version" not in data:
        errors.append("version: required (expected 1)")
    elif not _is_int(version) or version != 1:
        errors.append(f"version: unsupported value {version!r} (expected 1)")

    sections: dict[str, dict[str, Any]] = {}
    for name, allowed in SECTION_KEYS.items():
        sections[name] = _table(data, name, allowed, errors)

    token_budget = sections["context"].get("token_budget", Policy.token_budget)
    if not _is_int(token_budget) or token_budget <= 0:
        errors.append(f"context.token_budget: must be an integer > 0, got {token_budget!r}")

    skills_max = sections["skills"].get("max", Policy.skills_max)
    if not _is_int(skills_max) or not 0 <= skills_max <= 10:
        errors.append(f"skills.max: must be an integer 0..10, got {skills_max!r}")

    waivers = sections["completion"].get("waivers_permit_pass", Policy.waivers_permit_pass)
    if not isinstance(waivers, bool):
        errors.append(f"completion.waivers_permit_pass: must be a boolean, got {waivers!r}")

    gates = _gates(data.get("gates"), errors) if "gates" in data else ()
    signals = _signals(data.get("signals"), errors) if "signals" in data else ()

    if errors:
        raise PolicyInvalid(errors)
    return Policy(
        version=1,
        token_budget=token_budget,
        skills_max=skills_max,
        waivers_permit_pass=waivers,
        gates=gates,
        signals=signals,
        source=source,
        digest=digest,
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _member(value: Any, enum_type: type) -> bool:
    return isinstance(value, str) and value in {e.value for e in enum_type}


def _table(data: dict[str, Any], name: str, allowed: frozenset[str], errors: list[str]) -> dict:
    if name not in data:
        return {}
    table = data[name]
    if not isinstance(table, dict):
        errors.append(f"{name}: must be a table")
        return {}
    for key in sorted(set(table) - allowed):
        errors.append(f"{name}.{key}: unknown key")
    return table


def valid_scope(scope: Any) -> bool:
    """ "" or a relative "dir/" path: no "..", ".", empty segments, backslashes or leading "/"."""
    if not isinstance(scope, str):
        return False
    if scope == "":
        return True
    if not scope.endswith("/") or scope.startswith("/") or "\\" in scope:
        return False
    return all(part not in ("", ".", "..") for part in scope[:-1].split("/"))


def _valid_cwd(cwd: Any) -> bool:
    return isinstance(cwd, str) and (cwd == "" or valid_scope(cwd + "/"))


def _gates(raw: Any, errors: list[str]) -> tuple[Gate, ...]:
    if not isinstance(raw, dict):
        errors.append("gates: must be a table of [gates.<id>] tables")
        return ()
    gates: list[Gate] = []
    for gate_id in sorted(raw):
        path = f"gates.{gate_id}"
        table = raw[gate_id]
        n_errors = len(errors)
        if not GATE_ID_RE.fullmatch(gate_id):
            errors.append(f"{path}: invalid gate id (expected [a-z0-9][a-z0-9._-]*)")
        if not isinstance(table, dict):
            errors.append(f"{path}: must be a table")
            continue
        for key in sorted(set(table) - GATE_KEYS):
            errors.append(f"{path}.{key}: unknown key")

        kind = None
        if "kind" not in table:
            errors.append(f"{path}.kind: required")
        elif not _member(table["kind"], GateKind):
            errors.append(f"{path}.kind: unknown kind {table['kind']!r}")
        else:
            kind = GateKind(table["kind"])

        argv = table.get("argv")
        if "argv" not in table:
            errors.append(f"{path}.argv: required")
        elif isinstance(argv, str):
            errors.append(f"{path}.argv: must be an array of strings, not a string")
        elif (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(a, str) for a in argv)
            or argv[0] == ""
        ):
            errors.append(f"{path}.argv: must be a non-empty array of strings")

        scope = table.get("scope", "")
        if not valid_scope(scope):
            errors.append(
                f'{path}.scope: invalid {scope!r} (expected "" or a relative "dir/" '
                'without ".." or a leading "/")'
            )
        cwd = table.get("cwd", scope[:-1] if isinstance(scope, str) and scope else "")
        if not _valid_cwd(cwd):
            errors.append(
                f"{path}.cwd: invalid {cwd!r} (expected a relative directory without "
                '"..", a leading "/" or a trailing "/")'
            )

        cost = None
        if "cost" in table:
            if not _member(table["cost"], Cost):
                errors.append(f"{path}.cost: unknown cost {table['cost']!r}")
            else:
                cost = Cost(table["cost"])

        timeout = table.get("timeout_s", Gate.timeout_s)
        if not _is_int(timeout) or not 1 <= timeout <= MAX_TIMEOUT_S:
            errors.append(f"{path}.timeout_s: must be an integer 1..{MAX_TIMEOUT_S}")

        if len(errors) != n_errors or kind is None:
            continue
        gates.append(
            Gate(
                id=gate_id,
                kind=kind,
                argv=tuple(argv),
                cwd=cwd,
                scope=scope,
                cost=cost or DEFAULT_COST[kind],
                source=f"{POLICY_FILE}#gates.{gate_id}",
                provider="command",
                timeout_s=timeout,
                definition_digest=sha256_json(table),
            )
        )
    return tuple(gates)


def _signals(raw: Any, errors: list[str]) -> tuple[SignalRuleSpec, ...]:
    if not isinstance(raw, list):
        errors.append("signals: must be an array of [[signals]] tables")
        return ()
    specs: list[SignalRuleSpec] = []
    seen: set[str] = set()
    for i, table in enumerate(raw):
        path = f"signals[{i}]"
        n_errors = len(errors)
        if not isinstance(table, dict):
            errors.append(f"{path}: must be a table")
            continue
        for key in sorted(set(table) - SIGNAL_KEYS):
            errors.append(f"{path}.{key}: unknown key")

        sid = table.get("id")
        if not isinstance(sid, str) or not sid.startswith("custom.") or sid == "custom.":
            errors.append(f'{path}.id: must be a string starting with "custom.", got {sid!r}')
        elif sid in seen:
            errors.append(f"{path}.id: duplicate signal id {sid!r}")
        else:
            seen.add(sid)

        domain = table.get("domain")
        if not isinstance(domain, str) or not domain:
            errors.append(f"{path}.domain: must be a non-empty string")

        risk = table.get("risk")
        if not _member(risk, Risk):
            errors.append(f"{path}.risk: must be one of low|standard|elevated, got {risk!r}")

        paths = table.get("paths")
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(p, str) and p for p in paths)
        ):
            errors.append(f"{path}.paths: must be a non-empty array of glob strings")

        if len(errors) == n_errors:
            specs.append(SignalRuleSpec(id=sid, domain=domain, risk=Risk(risk), paths=tuple(paths)))
    return tuple(specs)
