"""Root gates that cover workspace packages (owner: agent "inventory"; architecture §3.5).

A monorepo often has a root script and per-package scripts of the same kind, and the root one
already runs the packages' checks. Requiring both runs the same checks twice. This module decides,
from explicit evidence only, when a root package.json script gate covers the script gate of the
same kind in a workspace package. No evidence means no coverage: both gates stay (fail closed).

`root_coverage(tree, kind_is_unit, root, pkg, pkg_dir)` returns a one-line evidence text, or None.
It is called once per tree (BASE and the working tree; inventory.py requires both to agree), and
only for a package directory that is a workspace package in that tree. The evidence is one of:

1. Recursive runner. A `&&`-separated segment of the root script is a workspace-wide run of the
   very script name the package gate runs:
     pnpm|nub  -r|--recursive [run] <name>          (also `pnpm run -r <name>`)
     npm       run <name> --workspaces|-ws          (also `npm test --workspaces`)
     yarn      workspaces run <name>                (yarn 1)
     yarn      workspaces foreach -A|--all run <name>
     turbo     run <name...>  /  turbo <name>       (optionally launched by npx/pnpm/yarn/...)
     lerna     run <name>                           (and the package matches lerna.json packages,
                                                     when lerna.json lists them)
   Only allow-listed flags may appear (RECURSIVE_FLAGS); any filter (`--filter`, `-F`,
   `--workspace`, `--scope`, `--since`, `--include`, ...), extra argument or unknown flag means no
   evidence. `nx run-many` is not accepted: nx targets can be redefined by project.json and by
   plugins, so the package script is not necessarily what runs.
2. Test runner projects (unit only). A segment of the root script runs `vitest` (`run`/`--run`,
   optionally `--config <file>`) or `jest` (optionally `--ci`, `--config <file>`), and nothing
   else; the root config that runner reads lists the package among its projects, as string
   literals: vitest `projects:` (or the older inline `workspace:`) in vitest.config.* /
   vite.config.*, or a vitest.workspace.* file when the config has none; jest `projects` in
   jest.config.* or package.json "jest". The package gate's script must itself be the bare runner
   (`vitest run`, `jest`) with no pre/post hook, so the root run executes exactly the package's
   project. The projects array may contain only string literals and inline object literals
   (which only add projects); a variable, spread or call could hide an exclusion, so it voids
   the evidence. `!` patterns exclude. More than one `projects` key also voids it.

The root script must chain its segments only with `&&` (a `;`, `||`, pipe, background job,
subshell or redirection could hide a failure), and no segment may start with a variable
assignment. Commands are split with shlex; nothing is executed.
"""

from __future__ import annotations

import fnmatch
import posixpath
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class Tree(Protocol):
    def read(self, path: str) -> bytes | None: ...

    def parsed(self, path: str, fmt: str) -> Any: ...


@dataclass(frozen=True)
class Script:
    """One package.json script as a gate runs it."""

    name: str
    body: str
    hooks: bool = False  # a pre<name> or post<name> script exists


# Tokens that only launch a tool from node_modules/.bin (npx vitest, pnpm exec vitest,
# nub exec --node vitest, yarn turbo, ...).
LAUNCHER_WORDS = frozenset(
    {"npx", "pnpx", "bunx", "pnpm", "yarn", "npm", "nub", "bun", "exec", "dlx", "x", "--", "--node"}
)
RECURSIVE_PMS = frozenset({"pnpm", "nub"})
# Flags that do not narrow a recursive run, per runner. Anything else voids the evidence.
RECURSIVE_FLAGS: dict[str, frozenset[str]] = {
    "pnpm": frozenset(
        {"-r", "--recursive", "run", "--parallel", "--stream", "--if-present", "--sequential"}
    ),
    "npm": frozenset({"run", "run-script", "--workspaces", "-ws", "--if-present"}),
    "yarn": frozenset(
        {
            "workspaces",
            "foreach",
            "run",
            "-A",
            "--all",
            "-p",
            "--parallel",
            "-t",
            "--topological",
            "-v",
            "--verbose",
            "-i",
            "--interlaced",
            "--topological-dev",
        }
    ),
    "turbo": frozenset({"run", "--parallel"}),
    "lerna": frozenset({"run", "--stream", "--parallel", "--no-prefix"}),
}
VITEST_CONFIGS = tuple(
    f"{stem}.config.{ext}"
    for stem in ("vitest", "vite")
    for ext in ("ts", "mts", "cts", "js", "mjs", "cjs")
)
# vitest < 4 reads a workspace file when the config has no projects.
VITEST_WORKSPACES = tuple(
    f"vitest.workspace.{ext}" for ext in ("ts", "mts", "cts", "js", "mjs", "cjs", "json")
)
JEST_CONFIGS = tuple(f"jest.config.{ext}" for ext in ("js", "ts", "mjs", "cjs", "json"))
_OPERATORS = frozenset({"&&", ";", "||", "|", "&", "|&", "(", ")", "<", ">", ">>", ";;"})


# --------------------------------------------------------------------------- shell and globs


def shell_words(command: str) -> list[str]:
    """Words of a shell command (operators split off); whitespace split if it does not lex."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()


def glob_match(pattern: list[str], parts: list[str]) -> bool:
    """fnmatch per path segment; `**` matches any number of segments."""
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return any(glob_match(pattern[1:], parts[i:]) for i in range(len(parts) + 1))
    return (
        bool(parts)
        and fnmatch.fnmatchcase(parts[0], pattern[0])
        and glob_match(pattern[1:], parts[1:])
    )


def _segments(body: str) -> list[list[str]] | None:
    """The `&&`-separated commands of a script body; None when any other operator appears or a
    command starts with a variable assignment (both could hide a failure or narrow a run)."""
    out: list[list[str]] = [[]]
    for word in shell_words(body):
        if word == "&&":
            out.append([])
        elif word in _OPERATORS or (set(word) <= set("();<>|&") and word):
            return None
        else:
            out[-1].append(word)
    if any(not seg for seg in out):
        return None
    if any("=" in seg[0] and not seg[0].startswith("-") for seg in out):
        return None
    return out


def _tool(segment: list[str]) -> tuple[str, list[str]] | None:
    """(tool, its arguments) after stripping launcher words (`nub exec --node vitest run`)."""
    i = 0
    while i < len(segment) and segment[i] in LAUNCHER_WORDS:
        i += 1
    if i >= len(segment):
        return None
    return segment[i], segment[i + 1 :]


# --------------------------------------------------------------------------- recursive runners


def _recursive_names(segment: list[str]) -> tuple[str, ...]:
    """The script names a segment runs in every workspace package (empty if it is not such a
    run, or if anything in it could narrow the run)."""
    head = segment[0]
    if head in RECURSIVE_PMS and (names := _pnpm_like(segment[1:])):
        return names
    if head == "npm" and (names := _npm(segment[1:])):
        return names
    if head == "yarn" and segment[1:2] == ["workspaces"]:
        return _yarn(segment[1:])
    found = _tool(segment)  # `npx turbo run test`, `pnpm turbo test`, `yarn lerna run test`
    if found is None:
        return ()
    tool, args = found
    if tool == "turbo":
        return _turbo(args)
    if tool == "lerna":
        return _plain(args, "lerna", need_run=True)
    return ()


def _plain(args: list[str], runner: str, need_run: bool) -> tuple[str, ...]:
    allowed = RECURSIVE_FLAGS[runner]
    names = [a for a in args if a not in allowed]
    if need_run and "run" not in args:
        return ()
    if len(names) != 1 or names[0].startswith("-"):
        return ()
    return (names[0],)


def _pnpm_like(args: list[str]) -> tuple[str, ...]:
    if not ({"-r", "--recursive"} & set(args)):
        return ()
    names = _plain(args, "pnpm", need_run=False)
    # `pnpm -r exec ...` / `pnpm -r install` are not script runs.
    if names and names[0] in {"exec", "install", "add", "update", "publish", "dlx", "x"}:
        return ()
    return names


def _npm(args: list[str]) -> tuple[str, ...]:
    if not ({"--workspaces", "-ws"} & set(args)):
        return ()
    if args[:1] in (["test"], ["t"]):
        rest = args[1:]
        return ("test",) if all(a in RECURSIVE_FLAGS["npm"] for a in rest) else ()
    if not ({"run", "run-script"} & set(args)):
        return ()
    return _plain(args, "npm", need_run=False)


def _yarn(args: list[str]) -> tuple[str, ...]:
    # args[0] == "workspaces"
    if args[1:2] == ["foreach"] and not ({"-A", "--all"} & set(args)):
        return ()
    if args[1:2] not in (["run"], ["foreach"]):
        return ()
    return _plain(args, "yarn", need_run=True)


def _turbo(args: list[str]) -> tuple[str, ...]:
    allowed = RECURSIVE_FLAGS["turbo"]
    tasks = [a for a in args if a not in allowed and not a.startswith("--concurrency=")]
    if not tasks or any(t.startswith("-") for t in tasks):
        return ()
    if "run" not in args and len(tasks) != 1:
        return ()
    return tuple(tasks)


def _lerna_includes(tree: Tree, pkg_dir: str) -> bool:
    data = tree.parsed("lerna.json", "json")
    if data is None:
        return True  # no lerna.json: lerna uses the package manager's workspaces
    if not isinstance(data, dict):
        return False
    packages = data.get("packages")
    if packages is None:
        return True
    if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
        return False
    return _matches(packages, pkg_dir, lambda _name: False)


# --------------------------------------------------------------------------- runner projects


@dataclass(frozen=True)
class _Runner:
    tool: str
    config: str | None  # --config value, if given


def _runner(segment: list[str], tool_name: str, allowed: frozenset[str]) -> _Runner | None:
    """The segment runs `tool_name` with only allow-listed flags (plus --config/-c <file>)."""
    found = _tool(segment)
    if found is None or found[0] != tool_name:
        return None
    args, config = found[1], None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--config", "-c") and i + 1 < len(args):
            config, i = args[i + 1], i + 2
            continue
        if a.startswith("--config="):
            config, i = a.split("=", 1)[1], i + 1
            continue
        if a not in allowed:
            return None
        i += 1
    return _Runner(tool_name, config)


VITEST_ARGS = frozenset({"run", "--run"})
JEST_ARGS = frozenset({"--ci"})


def _bare_runner(script: Script, tool_name: str, allowed: frozenset[str]) -> bool:
    """The package script is exactly the runner (no config override, no hooks, one command)."""
    if script.hooks:
        return False
    segments = _segments(script.body)
    if segments is None or len(segments) != 1:
        return False
    found = _runner(segments[0], tool_name, allowed)
    return found is not None and found.config is None


def _js_scan(text: str) -> list[tuple[str, str, int]]:
    """Tokens of JS/TS source: ("str", value, pos) for string literals (template literals with
    `${` become ("expr", ...)), ("word", ident, pos), ("punct", ch, pos). Comments dropped."""
    out: list[tuple[str, str, int]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "'\"`":
            j, buf = i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            value = "".join(buf)
            kind = "expr" if c == "`" and "${" in value else "str"
            out.append((kind, value, i))
            i = j + 1
        elif c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            out.append(("word", text[i:j], i))
            i = j
        else:
            out.append(("punct", c, i))
            i += 1
    return out


def _array_after(tokens: list[tuple[str, str, int]], start: int) -> list[str] | None:
    """String elements of the array literal opening at tokens[start] ("["). None when an element
    is anything but a string literal or an object literal."""
    if start >= len(tokens) or tokens[start][:2] != ("punct", "["):
        return None
    out: list[str] = []
    i, expect_value = start + 1, True
    while i < len(tokens):
        kind, value, _ = tokens[i]
        if (kind, value) == ("punct", "]"):
            return out
        if (kind, value) == ("punct", ","):
            expect_value, i = True, i + 1
            continue
        if not expect_value:
            return None
        if kind == "str":
            out.append(value)
            i += 1
        elif (kind, value) == ("punct", "{"):
            depth = 0
            while i < len(tokens):
                if tokens[i][:2] == ("punct", "{"):
                    depth += 1
                elif tokens[i][:2] == ("punct", "}"):
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            i += 1
        else:
            return None
        expect_value = False
    return None


_ABSENT: list[str] = []  # sentinel: the module has no such key


def _js_key_array(text: str, keys: tuple[str, ...]) -> list[str] | None:
    """The string elements of the single `<key>: [...]` among `keys` in a JS/TS module; _ABSENT
    when no such key exists; None when it is ambiguous (several) or not a usable array."""
    tokens = _js_scan(text)
    hits = [
        i
        for i, (kind, value, _) in enumerate(tokens[:-1])
        if kind in ("word", "str") and value in keys and tokens[i + 1][:2] == ("punct", ":")
    ]
    if not hits:
        return _ABSENT
    if len(hits) != 1:
        return None
    return _array_after(tokens, hits[0] + 2)


def _js_default_array(text: str) -> list[str] | None:
    """The array a vitest.workspace module exports (`export default [...]`,
    `defineWorkspace([...])`) or a JSON array."""
    tokens = _js_scan(text)
    for i, (kind, value, _) in enumerate(tokens):
        called = i + 1 < len(tokens) and tokens[i + 1][:2] == ("punct", "(")
        if (kind, value) == ("word", "defineWorkspace") and called:
            return _array_after(tokens, i + 2)
        if (
            (kind, value) == ("word", "default")
            and tokens[i + 1 : i + 2]
            and (tokens[i + 1][:2] == ("punct", "["))
        ):
            return _array_after(tokens, i + 1)
    if tokens and tokens[0][:2] == ("punct", "["):
        return _array_after(tokens, 0)
    return None


def _matches(patterns: list[str], pkg_dir: str, has_file: Callable[[str], bool]) -> bool:
    """Whether a project/workspace pattern list includes the package directory. A pattern that
    names a file (a config file) includes the package when that file exists in it."""
    included = excluded = False
    parts = pkg_dir.split("/")
    for raw in patterns:
        negated = raw.startswith("!")
        pattern = raw[1:] if negated else raw
        pattern = pattern.replace("<rootDir>/", "")
        while pattern.startswith("./"):
            pattern = pattern[2:]
        pattern = pattern.strip("/")
        if not pattern or pattern.startswith(".."):
            continue
        segs = pattern.split("/")
        hit = glob_match(segs, parts) or (
            len(segs) > 1 and glob_match(segs[:-1], parts) and has_file(segs[-1])
        )
        if hit:
            if negated:
                excluded = True
            else:
                included = True
    return included and not excluded


def _text(tree: Tree, path: str) -> str | None:
    raw = tree.read(path)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _config_file_in(tree: Tree, pkg_dir: str, names: tuple[str, ...]) -> Callable[[str], bool]:
    def has(pattern: str) -> bool:
        return any(
            fnmatch.fnmatchcase(name, pattern) and tree.read(f"{pkg_dir}/{name}") is not None
            for name in names
        )

    return has


def _repo_path(path: str) -> str | None:
    """A --config value as a repo-relative path; None if it leaves the repository."""
    norm = posixpath.normpath(path)
    if not path or path.startswith("/") or norm in (".", "..") or norm.startswith("../"):
        return None
    return norm


def _vitest_projects(tree: Tree, config: str | None) -> tuple[str, list[str]] | None:
    """(source, patterns) of the root vitest projects; None without a usable list."""
    if config is not None:
        path = _repo_path(config)
        if path is None:
            return None
        candidates: tuple[str, ...] = (path,)
    else:
        candidates = tuple(n for n in VITEST_CONFIGS if tree.read(n) is not None)[:1]
    for path in candidates:
        text = _text(tree, path)
        if text is None:
            return None
        found = _js_key_array(text, ("projects", "workspace"))
        if found is None:
            return None
        if found is not _ABSENT:
            return f"{path} projects", found
    if config is not None:
        return None
    for path in VITEST_WORKSPACES:
        text = _text(tree, path)
        if text is None:
            continue
        found = _js_default_array(text)
        return None if found is None else (path, found)
    return None


def _jest_projects(tree: Tree, config: str | None) -> tuple[str, list[str]] | None:
    if config is not None:
        path = _repo_path(config)
        if path is None:
            return None
        paths: tuple[str, ...] = (path,)
    else:
        paths = tuple(n for n in JEST_CONFIGS if tree.read(n) is not None)
        if len(paths) > 1:
            return None  # jest refuses several configs
    for path in paths:
        if path.endswith(".json"):
            data = tree.parsed(path, "json")
            projects = data.get("projects") if isinstance(data, dict) else None
            return _string_list(f"{path} projects", projects)
        text = _text(tree, path)
        found = None if text is None else _js_key_array(text, ("projects",))
        return None if found is None or found is _ABSENT else (f"{path} projects", found)
    data = tree.parsed("package.json", "json")
    jest = data.get("jest") if isinstance(data, dict) else None
    projects = jest.get("projects") if isinstance(jest, dict) else None
    return _string_list('package.json "jest.projects"', projects)


def _string_list(source: str, value: Any) -> tuple[str, list[str]] | None:
    if not isinstance(value, list):
        return None
    strings = [v for v in value if isinstance(v, str)]
    if any(not isinstance(v, (str, dict)) for v in value):
        return None
    return source, strings


# --------------------------------------------------------------------------- entry point


def root_coverage(
    tree: Tree, kind_is_unit: bool, root: Script, pkg: Script, pkg_dir: str
) -> str | None:
    """Evidence that the root script runs the package script's checks in `tree`, or None."""
    segments = _segments(root.body)
    if segments is None:
        return None
    for seg in segments:
        if pkg.name in _recursive_names(seg):
            tool = _tool(seg)
            if tool and tool[0] == "lerna" and not _lerna_includes(tree, pkg_dir):
                continue
            return (
                f"root script {root.name!r} runs {pkg.name!r} in every workspace package "
                f"({' '.join(seg)})"
            )
    if not kind_is_unit:
        return None
    for tool_name, allowed, projects_of, names in (
        ("vitest", VITEST_ARGS, _vitest_projects, VITEST_CONFIGS),
        ("jest", JEST_ARGS, _jest_projects, JEST_CONFIGS),
    ):
        if not _bare_runner(pkg, tool_name, allowed):
            continue
        for seg in segments:
            run = _runner(seg, tool_name, allowed)
            if run is None:
                continue
            found = projects_of(tree, run.config)
            if found is None:
                continue
            source, patterns = found
            if _matches(patterns, pkg_dir, _config_file_in(tree, pkg_dir, names)):
                return f"root {tool_name} reads {source}, which include {pkg_dir}"
    return None
