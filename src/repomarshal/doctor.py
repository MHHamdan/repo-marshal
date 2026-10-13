"""`repomarshal doctor` (owner: agent "hosts"; roadmap SHOULD #5; MISSION.md §29).

    run(args) -> int

Report actionable facts, read-only: git availability and repository state (branch, BASE and how
it was resolved via anchor.effective_base, uncommitted changes), detected scopes and the gate
inventory with availability, policy status (default / valid at BASE / invalid -> errors), optional
providers (mcp extra importable, SkillSpector / Playwright / Reticle on PATH: report found or not,
never claim support), and Python version. --json prints a stable object. Exit 0 when the
repository can be planned, 3 when policy is invalid or not a git repo. Never executes gates.
"""

from __future__ import annotations

import argparse


def run(args: argparse.Namespace) -> int:
    raise NotImplementedError
