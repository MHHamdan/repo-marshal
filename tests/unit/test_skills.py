"""Unit tests for skill discovery and selection (roadmap M9; architecture §4.5)."""

from __future__ import annotations

import pytest

from helpers import GitRepo
from repomarshal.model import ChangedFile, ChangeSet, FileStatus, SkillInfo
from repomarshal.skills import discover_skills, parse_frontmatter, select_skills

UI_FORMS = """---
name: ui-forms
description: Build accessible, responsive React form components with clear validation \
messages and CSS that follows the design system.
---

# UI forms

Use the shared `<Field>` component.
"""

AUTH_SECURITY = """---
name: auth-security
description: "Review authentication changes: OAuth flows, session handling, token refresh and \
rotation, and crypto primitives. Flags common security mistakes."
---

# Auth security checklist
"""

DOCS_WRITING = """---
name: docs-writing
description: >
  Write and maintain project documentation: README files, guides and ADRs,
  in a plain and consistent writing style.
---

# Docs writing
"""


def change_of(base, *files):
    return ChangeSet(base, base, "0" * 40, tuple(files), True)


@pytest.fixture
def skills_repo(tmp_path):
    repo = GitRepo(tmp_path / "repo")
    repo.write(".agents/skills/ui-forms/SKILL.md", UI_FORMS)
    repo.write(".agents/skills/auth-security/SKILL.md", AUTH_SECURITY)
    repo.write(".agents/skills/docs-writing/SKILL.md", DOCS_WRITING)
    repo.write("README.md", "# webapp\n")
    return repo, repo.commit("base")


def names(picks):
    return [p.name for p in picks]


def test_discover_at_base(skills_repo):
    repo, base = skills_repo
    found = discover_skills(repo.root, base)
    assert [s.path for s in found] == [
        ".agents/skills/auth-security/SKILL.md",
        ".agents/skills/docs-writing/SKILL.md",
        ".agents/skills/ui-forms/SKILL.md",
    ]
    by_name = {s.name: s for s in found}
    assert by_name["auth-security"].description.startswith("Review authentication changes")
    assert not by_name["auth-security"].description.endswith('"')
    assert by_name["docs-writing"].description == (
        "Write and maintain project documentation: README files, guides and ADRs, "
        "in a plain and consistent writing style."
    )


def test_discover_ignores_worktree_and_unborn(skills_repo):
    repo, base = skills_repo
    repo.write(".claude/skills/evil/SKILL.md", "---\nname: evil\ndescription: auth token\n---\n")
    assert all(s.name != "evil" for s in discover_skills(repo.root, base))
    assert discover_skills(repo.root, None) == ()


def test_discover_roots_and_depth(tmp_path):
    repo = GitRepo(tmp_path / "r")
    repo.write(".claude/skills/a/SKILL.md", "---\nname: a\n---\n")
    repo.write("skills/b/SKILL.md", "---\nname: b\n---\n")
    repo.write("skills/SKILL.md", "---\nname: too-shallow\n---\n")
    repo.write("skills/c/nested/SKILL.md", "---\nname: too-deep\n---\n")
    repo.write("vendor/skills/d/SKILL.md", "---\nname: not-a-root\n---\n")
    repo.write(".agents/skills/e/skill.md", "---\nname: wrong-case\n---\n")
    base = repo.commit()
    assert [s.name for s in discover_skills(repo.root, base)] == ["a", "b"]


def test_frontmatter_robustness(tmp_path):
    repo = GitRepo(tmp_path / "r")
    repo.write("skills/no-fence/SKILL.md", "name: ignored\n# just markdown\n")
    repo.write("skills/unclosed/SKILL.md", "---\nname: x\ndescription: y\n")
    repo.write("skills/quoted/SKILL.md", "---\nname: 'Quoted Name'\ndescription: \"d\"\n---\n")
    repo.write("skills/crlf/SKILL.md", "﻿---\r\nname: crlf\r\ndescription: ok\r\n---\r\n")
    repo.write("skills/empty/SKILL.md", "")
    repo.write("skills/binary/SKILL.md", b"\x00\x01\x02")
    repo.write("skills/latin1/SKILL.md", b"---\nname: caf\xe9\n---\n")
    repo.write("skills/ctrl/SKILL.md", "---\nname: a\x1b[31mb\ndescription: x\x07y\n---\n")
    repo.write("skills/blank-name/SKILL.md", "---\nname:   \ndescription: d\n---\n")
    repo.write("skills/literal/SKILL.md", "---\ndescription: |-\n  line one\n  line two\n---\n")
    base = repo.commit()
    got = {s.path.split("/")[1]: (s.name, s.description) for s in discover_skills(repo.root, base)}
    assert got == {
        "blank-name": ("blank-name", "d"),
        "crlf": ("crlf", "ok"),
        "ctrl": ("a [31mb", "x y"),
        "empty": ("empty", ""),
        "literal": ("literal", "line one line two"),
        "no-fence": ("no-fence", ""),
        "quoted": ("Quoted Name", "d"),
        "unclosed": ("unclosed", ""),
    }


def test_parse_frontmatter_never_raises():
    for text in ["", "---", "---\n---", "---\n: x\n---", "---\nkey\n---", "---\n  - a\n---"]:
        assert isinstance(parse_frontmatter(text), dict)
    assert parse_frontmatter("---\nName: n\nname: m\n---\n") == {"name": "n"}


@pytest.mark.parametrize(
    ("domains", "expected"),
    [
        (("code", "security"), ["auth-security"]),
        (("code", "frontend"), ["ui-forms"]),
        (("docs",), ["docs-writing"]),
        (("code",), []),
        ((), []),
    ],
)
def test_scenario_picks(skills_repo, domains, expected):
    repo, base = skills_repo
    picks = select_skills(discover_skills(repo.root, base), domains, change_of(base), 3)
    assert names(picks) == expected
    for p in picks:
        assert p.trust == "repo@base" and p.score == len(p.matched_domains) > 0


def test_score_order_tiebreak_and_cap():
    skills = (
        SkillInfo("zeta", "security and ui", "skills/zeta/SKILL.md"),
        SkillInfo("beta", "auth only", "skills/beta/SKILL.md"),
        SkillInfo("alpha", "token stuff", "skills/alpha/SKILL.md"),
        SkillInfo("none", "nothing relevant", "skills/none/SKILL.md"),
    )
    picks = select_skills(skills, ("security", "frontend"), change_of("a" * 40), 3)
    assert [(p.name, p.score, p.matched_domains) for p in picks] == [
        ("zeta", 2, ("frontend", "security")),
        ("alpha", 1, ("security",)),
        ("beta", 1, ("security",)),
    ]
    assert names(select_skills(skills, ("security", "frontend"), change_of("a" * 40), 2)) == [
        "zeta",
        "alpha",
    ]
    assert select_skills(skills, ("security",), change_of("a" * 40), 0) == ()


def test_whole_word_matching():
    skills = (
        SkillInfo("s1", "authentication helpers", "skills/s1/SKILL.md"),
        SkillInfo("s2", "Handles OAuth.", "skills/s2/SKILL.md"),
        SkillInfo("s3", "uniform guidance", "skills/s3/SKILL.md"),
    )
    assert names(select_skills(skills, ("security", "frontend"), change_of(None), 5)) == ["s2"]


@pytest.mark.parametrize("status", [FileStatus.ADDED, FileStatus.MODIFIED, FileStatus.DELETED])
def test_changed_skill_never_picked(skills_repo, status):
    repo, base = skills_repo
    skills = discover_skills(repo.root, base)
    change = change_of(base, ChangedFile(".agents/skills/auth-security/SKILL.md", status))
    assert select_skills(skills, ("security",), change, 3) == ()


def test_changed_skill_sibling_or_rename_blocks_pick(skills_repo):
    repo, base = skills_repo
    skills = discover_skills(repo.root, base)
    sibling = change_of(base, ChangedFile(".agents/skills/auth-security/run.sh", FileStatus.ADDED))
    assert select_skills(skills, ("security",), sibling, 3) == ()
    renamed = change_of(
        base,
        ChangedFile(
            "elsewhere/SKILL.md",
            FileStatus.RENAMED,
            old_path=".agents/skills/auth-security/SKILL.md",
        ),
    )
    assert select_skills(skills, ("security",), renamed, 3) == ()
    unrelated = change_of(
        base, ChangedFile(".agents/skills/auth-securityX/SKILL.md", FileStatus.ADDED)
    )
    assert names(select_skills(skills, ("security",), unrelated, 3)) == ["auth-security"]
