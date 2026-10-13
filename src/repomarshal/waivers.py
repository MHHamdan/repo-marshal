"""Human override (owner: agent "override"; MISSION.md §54; roadmap SHOULD #12).

    collect(repo, plan, mode, env=None) -> tuple[Waiver, ...]
    cli_waive(args) -> int                         (`repomarshal waive GATE --reason TEXT`)

Waivers record a human decision to accept a gate that could not be verified (UNKNOWN or
UNAVAILABLE). verdict.fold decides whether they count (BASE policy waivers_permit_pass); this
module only decides WHICH waivers are genuine. An agent must not be able to grant one.

Modes (api.done(..., waivers=mode)):
- "none": never collect waivers. Used by the Stop hook and the MCP server (they run inside the
  agent's session) and is the api default.
- "local": read waivers written by `repomarshal waive` under `.repomarshal/waivers/`. They are
  bound to the current tree digest and plan (a waiver for another tree is ignored). Creating one
  requires an interactive terminal (stdin and stdout are TTYs) and an explicit typed
  confirmation; identity = git user.email + OS user. Local waivers are a convenience for a
  human at a terminal, never evidence for CI.
- "github": only in GitHub Actions on a pull_request event. Read the PR's labels and reviews via
  the GitHub REST API with the workflow token (GITHUB_TOKEN / GITHUB_REPOSITORY /
  GITHUB_EVENT_PATH). Local waiver files are ignored. A waiver is genuine when:
    * a label `repomarshal-waive:<target>` was applied by a human who is a CODEOWNER (CODEOWNERS
      read at BASE) or has admin/maintain permission, OR an APPROVED review by a CODEOWNER whose
      body contains a line `repomarshal-waive <target>: <reason>`;
    * the granter is not the PR author and not a bot;
    * the reason is non-empty (label waivers take the reason from the label description or a
      PR comment by the same human; if none, the waiver is rejected).
  Network or API errors yield NO waivers (fail closed) and a reason in the output.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

from repomarshal.model import Plan, Waiver

MODES = ("none", "local", "github")


def collect(
    repo: Path, plan: Plan, mode: str, env: Mapping[str, str] | None = None
) -> tuple[Waiver, ...]:
    """Genuine waivers for this plan. Mode "none" always returns ()."""
    # Contract stub: no waivers are genuine until the override agent implements the modes.
    # Returning () is the fail-closed answer (a waiver can only ever help a verdict).
    return ()


def cli_waive(args: argparse.Namespace) -> int:
    raise NotImplementedError
