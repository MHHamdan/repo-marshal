"""Skill discovery and recommendation (owner: agent "context"; roadmap M9; architecture §4.5).

Discovery at BASE only (git.list_files_at / git.read_at): SKILL.md files directly under a skill
directory in `.agents/skills/<name>/`, `.claude/skills/<name>/`, `skills/<name>/`. Frontmatter:
the leading `---` ... `---` block; parse only single-line `key: value` pairs (strip quotes);
use `name` (fallback: directory name) and `description`. Malformed files are skipped.

Selection: for each plan domain, a fixed keyword set (e.g. security: auth, oauth, token, security,
crypto, session; frontend: ui, form, css, component, accessibility, responsive, react; docs:
docs, documentation, readme, writing; backend: api, server, database; dependencies: dependency,
package, supply-chain). score = number of plan domains whose keywords occur (whole-word,
case-insensitive) in name + description. Score 0 is never picked. Skills whose SKILL.md path is
in the change set (added/modified/deleted) are NEVER picked. At most `max_n`, ordered by
(-score, name). trust = "repo@base".
"""

from __future__ import annotations

from pathlib import Path

from repomarshal.model import ChangeSet, SkillInfo, SkillPick


def discover_skills(repo: Path, base: str | None) -> tuple[SkillInfo, ...]:
    """Skills present at BASE, sorted by path."""
    raise NotImplementedError


def select_skills(
    skills: tuple[SkillInfo, ...],
    domains: tuple[str, ...],
    change: ChangeSet,
    max_n: int,
) -> tuple[SkillPick, ...]:
    """Deterministic recommendation (see module docstring)."""
    raise NotImplementedError
