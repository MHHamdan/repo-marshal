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

from pathlib import Path

from repomarshal.model import Policy

POLICY_FILE = "repomarshal.toml"


def load_policy(repo: Path, base: str | None) -> Policy:
    """Policy from `repomarshal.toml` at BASE (see module docstring).

    Policy.source = f"base:{base}:repomarshal.toml" (or "default"); Policy.digest = sha256 of the
    raw bytes (model.sha256_bytes), or sha256_bytes(b"default"). Policy gates are model.Gate
    objects with provider "command", source "repomarshal.toml#gates.<id>", and
    definition_digest = sha256_json of their table.
    """
    raise NotImplementedError
