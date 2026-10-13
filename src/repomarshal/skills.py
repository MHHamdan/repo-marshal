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

Implementation notes (robustness, T9/T10):
- A file without a closed leading `---` fence still yields a skill named after its directory
  with an empty description. Files that are not valid UTF-8 text (or contain NUL) are skipped.
- A folded/literal description (`description: >` or `|`, then indented lines) is joined into one
  line. Names and descriptions are stripped of control characters and length-capped.
- A skill is also never picked when any other file under its directory is in the change set
  (`agent.skill_changed` covers SKILL.md siblings too).
"""

from __future__ import annotations

import re
from pathlib import Path

from repomarshal.git import list_files_at, read_at
from repomarshal.model import ChangeSet, SkillInfo, SkillPick

SKILL_ROOTS: tuple[str, ...] = (".agents/skills/", ".claude/skills/", "skills/")
TRUST = "repo@base"
MAX_NAME = 128
MAX_DESCRIPTION = 1024

# Plan domain -> keywords (whole-word, case-insensitive). Domains not listed ("code", "unknown")
# never contribute to a score. Also used by context.py for P3 doc matching.
DOMAIN_KEYWORDS: dict[str, frozenset[str]] = {
    "security": frozenset(
        {"auth", "oauth", "token", "tokens", "security", "crypto", "session", "sessions", "jwt"}
    ),
    "frontend": frozenset(
        {
            "ui",
            "form",
            "forms",
            "css",
            "component",
            "components",
            "accessibility",
            "a11y",
            "responsive",
            "react",
        }
    ),  # fmt: skip
    "docs": frozenset({"docs", "documentation", "readme", "writing"}),
    "backend": frozenset({"api", "server", "database"}),
    "dependencies": frozenset({"dependency", "dependencies", "package", "supply-chain"}),
    "tests": frozenset({"test", "tests", "testing"}),
    "data": frozenset({"database", "migration", "migrations", "sql"}),
    "ci": frozenset({"ci", "workflow", "workflows", "pipeline"}),
}

_CTRL = re.compile(r"[\x00-\x1f\x7f]+")
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:(.*)$")


def _clean(text: str, cap: int) -> str:
    return " ".join(_CTRL.sub(" ", text).split())[:cap]


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal frontmatter parser: single-line `key: value` pairs between a leading `---` and the
    next `---`, plus folded/literal block values. Returns {} when there is no closed block.
    Never raises."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
    except StopIteration:
        return {}
    out: dict[str, str] = {}
    body = lines[1:end]
    i = 0
    while i < len(body):
        line = body[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#") or line[:1].isspace():
            continue
        m = _KEY.match(line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if value[:1] in (">", "|") or value == "":
            parts: list[str] = []
            while i < len(body) and (body[i][:1].isspace() or not body[i].strip()):
                if body[i].strip():
                    parts.append(body[i].strip())
                i += 1
            value = " ".join(parts) if parts else ""
        else:
            value = _unquote(value)
        out.setdefault(key, value)
    return out


def _skill_dir(path: str) -> str | None:
    """`<root><name>/` when `path` is `<root><name>/SKILL.md` for a known root, else None."""
    for root in SKILL_ROOTS:
        if path.startswith(root):
            rest = path[len(root) :].split("/")
            if len(rest) == 2 and rest[1] == "SKILL.md" and rest[0]:
                return root + rest[0] + "/"
    return None


def discover_skills(repo: Path, base: str | None) -> tuple[SkillInfo, ...]:
    """Skills present at BASE, sorted by path."""
    found: list[SkillInfo] = []
    for path in list_files_at(Path(repo), base):
        sdir = _skill_dir(path)
        if sdir is None:
            continue
        raw = read_at(Path(repo), base, path)
        if raw is None or b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        meta = parse_frontmatter(text)
        dir_name = sdir.rstrip("/").rsplit("/", 1)[-1]
        name = _clean(meta.get("name", ""), MAX_NAME) or _clean(dir_name, MAX_NAME)
        if not name:
            continue
        description = _clean(meta.get("description", ""), MAX_DESCRIPTION)
        found.append(SkillInfo(name=name, description=description, path=path))
    return tuple(sorted(found, key=lambda s: s.path))


def _matches(text: str, keyword: str) -> bool:
    return (
        re.search(r"(?<![A-Za-z0-9])" + re.escape(keyword) + r"(?![A-Za-z0-9])", text) is not None
    )


def select_skills(
    skills: tuple[SkillInfo, ...],
    domains: tuple[str, ...],
    change: ChangeSet,
    max_n: int,
) -> tuple[SkillPick, ...]:
    """Deterministic recommendation (see module docstring)."""
    if max_n <= 0:
        return ()
    touched: set[str] = set()
    for f in change.files:
        touched.add(f.path)
        if f.old_path:
            touched.add(f.old_path)
    picks: list[SkillPick] = []
    for skill in skills:
        sdir = skill.path.rpartition("/")[0] + "/"
        if skill.path in touched or any(p.startswith(sdir) for p in touched):
            continue  # T9: a skill changed in the diff is never recommended
        text = f"{skill.name} {skill.description}".lower()
        matched = tuple(
            sorted(
                d
                for d in set(domains)
                if any(_matches(text, kw) for kw in DOMAIN_KEYWORDS.get(d, ()))
            )
        )
        if matched:
            picks.append(SkillPick(skill.name, skill.path, matched, len(matched), TRUST))
    picks.sort(key=lambda p: (-p.score, p.name, p.path))
    return tuple(picks[:max_n])
