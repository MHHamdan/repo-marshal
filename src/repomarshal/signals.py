"""Signal catalogue and evaluation (owner: agent "planner"; roadmap M5; architecture §4.1; ADR-005).

The catalogue is data (a tuple of rules with stable ids). v0.1 signals:
  docs.changed, code.changed, frontend.ui, tests.changed, security.auth_path,
  security.sensitive_identifier, build.config_changed, policy.self_modified,
  coverage.unclassified
plus policy [[signals]] ("custom.*", path globs, add-only). One Signal per (id) aggregating all
matching files (files sorted). A file that matches no rule (built-in or custom) produces
coverage.unclassified. Deleted files count (their path still matters).
"""

from __future__ import annotations

from pathlib import Path

from repomarshal.model import ChangeSet, Policy, Signal

# Signals that widen cheap/standard gates to every scope (architecture §4.2).
WIDENING: frozenset[str] = frozenset(
    {
        "build.config_changed",
        "coverage.unclassified",
        "policy.self_modified",
        # reserved for SHOULD signals, accepted if present:
        "ci.changed",
        "agent.config_changed",
        "tests.deleted",
    }
)


def evaluate_signals(repo: Path, change: ChangeSet, policy: Policy) -> tuple[Signal, ...]:
    """Evaluate the catalogue plus policy rules over `change`. Sorted by id. Deterministic.

    build.config_changed needs BASE vs working-tree content of manifests (git.read_at with
    change.base, git.read_worktree); everything else uses paths, status and added lines.
    """
    raise NotImplementedError


def task_signals(task: str | None) -> tuple[Signal, ...]:
    """Add-only signals from untrusted task text: keyword map to "task.<domain>" signals
    (task.docs, task.frontend, task.security, task.dependencies, ...), risk per domain
    (task.security -> elevated). Never produces anything that removes a requirement.
    Empty/None task -> ().
    """
    raise NotImplementedError
