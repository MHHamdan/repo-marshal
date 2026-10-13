"""Context selection (owner: agent "context"; roadmap M8; architecture §4.4).

Deterministic heuristic, labelled as such. Priorities:
  P1 changed files (not deleted)                         always included
  P2 tests paired by stem with changed files             always included (correctness floor)
  P3 docs/ADRs (*.md under docs/ or root) whose filename tokens match a changed file's stem or
     path segments, or keywords of the plan's domains   budget permitting
  P4 other files in the changed files' directories; AGENTS.md / README.md at the root
                                                         budget permitting
Sizes: est_tokens = working-tree bytes // 4 (or BASE bytes for deleted). P1+P2 are included even
if they exceed the budget. P3/P4 items that do not fit go to `excluded` with their size.
Forecast mode (change has no files): candidates are files whose path segments or stem match task
keywords (P3/P4 rules apply). candidate_tokens = sum over every candidate considered.
Never include paths under .git/ or .repomarshal/; never follow symlinks outside the repo.
"""

from __future__ import annotations

from pathlib import Path

from repomarshal.model import ChangeSet, ContextPlan, Signal


def select_context(
    repo: Path,
    change: ChangeSet,
    signals: tuple[Signal, ...],
    task: str | None,
    budget_tokens: int,
) -> ContextPlan:
    """Ranked context for the agent (see module docstring). Deterministic ordering:
    priority, then path."""
    raise NotImplementedError
