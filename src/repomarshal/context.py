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
Generated or huge files are never items, at any priority (P1 included): lockfiles, minified
bundles (*.min.js, *.min.css), source maps (*.map), files under a dist/ or build/ segment, and
files over MAX_FILE_BYTES. They go to `excluded` with the reason (e.g. "lockfile (generated)") and
still count in candidate_tokens, so selected_tokens stays an honest estimate of what is read.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

from repomarshal.git import list_files_at, read_at
from repomarshal.model import ChangeSet, ContextItem, ContextPlan, FileStatus, Signal
from repomarshal.signals import is_lockfile
from repomarshal.skills import DOMAIN_KEYWORDS

_FORBIDDEN_TOP = frozenset({".git", ".repomarshal"})
_PY = frozenset({".py"})
_JS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
_TEST_DIRS = frozenset({"tests", "test", "__tests__"})
_ROOT_INSTRUCTIONS = ("AGENTS.md", "README.md")
# Generated or huge files: never read in full.
MAX_FILE_BYTES = 200 * 1024
_GENERATED_SUFFIXES = ((".min.js", "minified bundle"), (".min.css", "minified bundle"),
                       (".map", "source map"))  # fmt: skip
_BUILD_OUTPUT_DIRS = frozenset({"dist", "build"})

# Generic path tokens that say nothing about what a change is about.
_PATH_STOP = frozenset(
    {
        "src", "lib", "app", "apps", "pkg", "packages", "internal", "index", "main", "init",
        "test", "tests", "spec", "docs", "doc", "md", "py", "js", "ts", "tsx", "jsx",
        "utils", "util", "common", "core",
    }
)  # fmt: skip
# Words in task text that do not narrow the search.
_TASK_STOP = _PATH_STOP | frozenset(
    {
        "add", "adds", "fix", "fixes", "update", "updates", "modify", "change", "changes",
        "remove", "delete", "make", "use", "new", "handling", "handle", "support", "implement",
        "improve", "refactor", "bug", "feature", "the", "and", "for", "with", "into", "from",
        "when", "this", "that", "should", "all", "not",
    }
)  # fmt: skip

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _tokens(text: str) -> list[str]:
    """Split on non-alphanumerics (covers - _ . / and spaces) and camelCase; lowercased."""
    out: list[str] = []
    for part in re.split(r"[^A-Za-z0-9]+", text):
        out.extend(t.lower() for t in _CAMEL.findall(part))
    return out


def _meaningful(tokens: list[str], stop: frozenset[str], min_len: int = 2) -> set[str]:
    return {t for t in tokens if len(t) >= min_len and not t.isdigit() and t not in stop}


def _forbidden(path: str) -> bool:
    parts = path.split("/")
    return parts[0] in _FORBIDDEN_TOP or ".git" in parts


def _dir(path: str) -> str:
    """Scope-style directory of `path`: "" for the root, else "dir/"."""
    head, _, _ = path.rpartition("/")
    return head + "/" if head else ""


def _est_tokens(repo: Path, path: str) -> int | None:
    """bytes // 4 of the working-tree file, or None when it is missing, not a regular file, or
    resolves outside the repository (symlink escape) or into .git/ / .repomarshal/."""
    try:
        root = repo.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        rel = target.relative_to(root).as_posix()
        if _forbidden(rel):
            return None
        return target.stat().st_size // 4
    except (OSError, ValueError):
        return None


def _generated(path: str, est_tokens: int) -> str | None:
    """Why `path` is not full-text context (generated or huge), or None."""
    lower = path.lower()
    if is_lockfile(path):
        return "lockfile (generated)"
    for suffix, what in _GENERATED_SUFFIXES:
        if lower.endswith(suffix):
            return f"{what} (generated)"
    if _BUILD_OUTPUT_DIRS.intersection(lower.split("/")[:-1]):
        return "build output (generated)"
    if est_tokens * 4 > MAX_FILE_BYTES:
        return f"file over {MAX_FILE_BYTES // 1024} KB"
    return None


def _family(path: str) -> frozenset[str] | None:
    suffix = PurePosixPath(path).suffix
    if suffix in _PY:
        return _PY
    if suffix in _JS:
        return _JS
    return None


def _test_stem(path: str) -> tuple[str, bool] | None:
    """(source stem, is_test) for Python and JS/TS files; None for other languages."""
    fam = _family(path)
    if fam is None:
        return None
    name = PurePosixPath(path).name
    stem = name[: -len(PurePosixPath(path).suffix)]
    if fam is _PY:
        if stem.startswith("test_") and len(stem) > 5:
            return stem[5:], True
        if stem.endswith("_test") and len(stem) > 5:
            return stem[:-5], True
        return stem, False
    for marker in (".test", ".spec"):
        if stem.endswith(marker) and len(stem) > len(marker):
            return stem[: -len(marker)], True
    if "__tests__" in path.split("/")[:-1]:
        return stem, True
    return stem, False


def _test_root(path: str) -> str:
    """Prefix under which a test's source is looked for: the directory up to the first
    tests-like segment, or the test's own directory."""
    parts = path.split("/")[:-1]
    for i, seg in enumerate(parts):
        if seg in _TEST_DIRS:
            return "/".join(parts[:i]) + "/" if i else ""
    return _dir(path)


def _paired(changed: list[str], universe: list[str]) -> dict[str, str]:
    """Paths paired by stem with changed files -> why."""
    index: dict[tuple[frozenset[str], str], list[tuple[str, bool]]] = defaultdict(list)
    for p in universe:
        ts = _test_stem(p)
        if ts is not None:
            index[(_family(p), ts[0])].append((p, ts[1]))  # type: ignore[index]
    out: dict[str, str] = {}
    for c in changed:
        ts = _test_stem(c)
        if ts is None:
            continue
        stem, is_test = ts
        for p, p_is_test in index.get((_family(c), stem), []):  # type: ignore[arg-type]
            if p == c or p_is_test == is_test or p in out:
                continue
            if not is_test:
                dir_parts = p.split("/")[:-1]
                if _dir(p) == _dir(c) or _TEST_DIRS.intersection(dir_parts):
                    out[p] = f"test paired by stem with changed {c}"
            elif p.startswith(_test_root(c)):
                out[p] = f"source paired by stem with changed test {c}"
    return out


def _is_doc(path: str) -> bool:
    if not path.endswith(".md"):
        return False
    return "/" not in path or path.startswith("docs/")


def _worktree_files(repo: Path, change: ChangeSet) -> list[str]:
    """Files of the working tree as the change set describes it: BASE files plus added and
    modified paths, minus deleted and renamed-away paths. Ignored files are never included."""
    files = set(list_files_at(repo, change.base))
    for f in change.files:
        if f.status == FileStatus.DELETED:
            files.discard(f.path)
        else:
            files.add(f.path)
        if f.status == FileStatus.RENAMED and f.old_path:
            files.discard(f.old_path)
    return sorted(p for p in files if not _forbidden(p))


def _domain_keywords(signals: tuple[Signal, ...]) -> set[str]:
    kws: set[str] = set()
    for s in signals:
        kws.update(DOMAIN_KEYWORDS.get(s.domain, ()))
    return kws


def select_context(
    repo: Path,
    change: ChangeSet,
    signals: tuple[Signal, ...],
    task: str | None,
    budget_tokens: int,
) -> ContextPlan:
    """Ranked context for the agent (see module docstring). Deterministic ordering:
    priority, then path."""
    repo = Path(repo)
    budget = max(0, int(budget_tokens))
    universe = _worktree_files(repo, change)
    universe_set = set(universe)
    domain_kws = _domain_keywords(signals)

    chosen: dict[str, tuple[int, str]] = {}  # path -> (priority, why)
    candidate_tokens = 0

    def offer(path: str, priority: int, why: str) -> None:
        if path not in chosen and not _forbidden(path):
            chosen[path] = (priority, why)

    changed_live: list[str] = []
    match_terms: dict[str, set[str]] = {}  # term -> changed paths it came from (for reasons)
    for f in change.files:
        if _forbidden(f.path):
            continue
        for p in (f.path, f.old_path) if f.old_path else (f.path,):
            parts = p.split("/")
            toks = _tokens(PurePosixPath(p).stem)
            for seg in parts[:-1]:
                toks.extend(_tokens(seg))
            for t in _meaningful(toks, _PATH_STOP):
                match_terms.setdefault(t, set()).add(f.path)
        if f.status == FileStatus.DELETED:
            data = read_at(repo, change.base, f.path)
            candidate_tokens += len(data) // 4 if data is not None else 0
            continue
        changed_live.append(f.path)
        offer(f.path, 1, f"changed file ({FileStatus(f.status).name.lower()})")

    forecast = not change.files
    if not forecast:
        for p, why in sorted(_paired(changed_live, universe).items()):
            offer(p, 2, why)

    # P3: docs matching changed-file tokens or domain keywords (or task keywords in forecast).
    task_kws = _meaningful(_tokens(task or ""), _TASK_STOP, min_len=3) if forecast else set()
    for p in universe:
        if not _is_doc(p) or p in chosen:
            continue
        name_toks = set(_tokens(PurePosixPath(p).stem))
        hits = sorted(name_toks & (set(match_terms) | domain_kws | task_kws))
        if hits:
            offer(p, 3, "doc filename matches " + ", ".join(repr(h) for h in hits))

    # P4: siblings of changed files, forecast keyword matches, root instructions.
    if forecast:
        for p in universe:
            toks = set(_tokens(PurePosixPath(p).stem))
            for seg in p.split("/")[:-1]:
                toks.update(_tokens(seg))
            hits = sorted(toks & task_kws)
            if hits:
                offer(p, 4, "path matches task keyword " + ", ".join(repr(h) for h in hits))
    else:
        dirs = sorted({_dir(f.path) for f in change.files if not _forbidden(f.path)})
        dir_set = set(dirs)
        for p in universe:
            d = _dir(p)
            if d in dir_set and d != "":
                offer(p, 4, f"in changed directory {d}")
    for p in _ROOT_INSTRUCTIONS:
        if p in universe_set:
            offer(p, 4, "root agent instructions" if p == "AGENTS.md" else "root README")

    items: list[ContextItem] = []
    excluded: list[ContextItem] = []
    selected = 0
    for path, (priority, why) in sorted(chosen.items(), key=lambda kv: (kv[1][0], kv[0])):
        est = _est_tokens(repo, path)
        if est is None:
            if priority > 1:
                continue  # missing, or refused (symlink escape): not a candidate
            est = 0  # changed path we will not read (e.g. a symlink pointing outside)
        candidate_tokens += est
        generated = _generated(path, est)
        if generated:
            reason = f"{why}; excluded: {generated} ({est} est. tokens)"
            excluded.append(ContextItem(path, reason, priority, est))
        elif priority <= 2 or selected + est <= budget:
            items.append(ContextItem(path, why, priority, est))
            selected += est
        else:
            remaining = max(0, budget - selected)
            reason = f"{why}; excluded: over budget ({est} est. tokens, {remaining} remaining)"
            excluded.append(ContextItem(path, reason, priority, est))

    return ContextPlan(
        items=tuple(items),
        excluded=tuple(excluded),
        budget_tokens=budget,
        selected_tokens=selected,
        candidate_tokens=candidate_tokens,
    )
