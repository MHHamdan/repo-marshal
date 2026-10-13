"""`repomarshal init` (owner: agent "hosts"; roadmap SHOULD #5).

    run(args) -> int

Without --print: create `repomarshal.toml` at the repository root with `version = 1` and commented
examples of [context], [skills], [completion], [gates.<id>] and [[signals]] (it must load cleanly
with policy.load_policy). Refuse (exit 1, message) if the file already exists; never overwrite.
Touch NOTHING else (no .gitignore, no agent config). Print the next steps, including that the
policy only takes effect once committed on the base branch (policy is read from BASE).
With --print HOST: print the snippet for that host (Claude Code Stop hook settings JSON using
`repomarshal done --hook claude-code`; MCP server config for hosts that take one) and write
nothing.

Exit codes: 0 written / printed; 1 the file already exists (or cannot be created); 3 not a git
repository. The file is created with O_EXCL, so a file (or symlink) that appears concurrently is
never overwritten either.
"""

from __future__ import annotations

import argparse
import json
import sys

from repomarshal import git
from repomarshal.model import GitError, NotAGitRepository
from repomarshal.policy import POLICY_FILE

TEMPLATE = """\
# RepoMarshal policy. Reference: `repomarshal plan --explain`, `repomarshal doctor`.
#
# RepoMarshal reads this file from the BASE commit only, never from the working tree, so a
# change cannot rewrite the rules that judge it. Edits take effect once they are committed on
# the base branch. Unknown keys are errors (the verdict becomes BLOCKED), so typos never pass
# silently. Uncomment and edit the examples you need.

version = 1

# Context plan budget, in estimated tokens (bytes / 4). Integer > 0.
# [context]
# token_budget = 12000

# Maximum number of repository skills recommended per plan. Integer 0..10.
# [skills]
# max = 3

# Whether a WAIVED gate result may count as PASS. Default false: a waiver stays UNKNOWN.
# [completion]
# waivers_permit_pass = false

# Extra gates. Each runs an argv list (never a shell) from cwd inside the repository. Ids use
# [a-z0-9][a-z0-9._-]*. A policy gate replaces a detected gate of the same scope and kind.
# kind: docs, format, lint, typecheck, unit, integration, secrets, dep_audit, security_scan,
#       skill_audit, runtime or a11y.
# scope: "" for the whole repository, or a relative "dir/" (no "..", no leading "/").
# cost: cheap, standard or expensive (cheap gates run first). timeout_s: 1..86400.
# [gates.security]
# kind = "security_scan"
# argv = ["bandit", "-r", "app"]
# scope = "backend/"
# cwd = "backend"
# cost = "expensive"
# timeout_s = 600

# Extra signal rules. They can only add work: a matching path raises risk and requires gates.
# id must start with "custom."; risk is low, standard or elevated; paths are globs ("**" allowed).
# [[signals]]
# id = "custom.payments"
# domain = "security"
# risk = "elevated"
# paths = ["**/payments/**"]
"""

NEXT_STEPS = """\
Next steps:
  1. Review {path} and uncomment what you need.
  2. Commit it on the base branch (e.g. main). Policy is read from BASE, never from the working
     tree, so it has no effect until it is committed there.
  3. Run `repomarshal doctor` to check the policy, the detected gates and their tools.
  4. Hook an agent up: `repomarshal init --print claude-code` (or codex, cursor, copilot, gemini).
"""

_CLAUDE_HOOK = {
    "hooks": {
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "repomarshal done --hook claude-code",
                        "timeout": 1800,
                    }
                ]
            }
        ]
    }
}

_MCP_NOTE = (
    "# The MCP server needs the optional extra: pip install 'repomarshal[mcp]'. It exposes\n"
    "# repomarshal_plan, repomarshal_done and repomarshal_explain over stdio.\n"
)

_AGENTS_LINE = (
    "# Without a hook, add to your agent instructions (AGENTS.md or similar):\n"
    "#   Before claiming a task is complete, run `repomarshal done`. Only exit code 0 (PASS)\n"
    "#   means done; on FAIL fix the code, on UNKNOWN or BLOCKED report what is unverified.\n"
)


def _json(obj: object) -> str:
    return json.dumps(obj, indent=2) + "\n"


def _snippet(host: str) -> str:
    mcp_servers = {"repomarshal": {"command": "repomarshal", "args": ["mcp"]}}
    if host == "claude-code":
        return (
            "# Claude Code: Stop hook. Add to .claude/settings.json (project) or\n"
            "# ~/.claude/settings.json (user). Tested against recorded Stop-hook payloads in\n"
            "# RepoMarshal's test suite. A non-PASS verdict blocks the stop with the failing and\n"
            "# unknown gates; interrupt the agent (Esc) to stop anyway. Gates can be slow, so the\n"
            "# timeout (seconds) is generous; raise it if your gates take longer.\n"
            + _json(_CLAUDE_HOOK)
            + "\n# Optional, UNTESTED: MCP server in .mcp.json (project scope).\n"
            + _MCP_NOTE
            + _json(
                {"mcpServers": {"repomarshal": {"type": "stdio", **mcp_servers["repomarshal"]}}}
            )
        )
    if host == "codex":
        return (
            "# Codex CLI: MCP server in ~/.codex/config.toml. UNTESTED: RepoMarshal has no Codex\n"
            "# hook shim; this config is not exercised by its test suite.\n"
            + _MCP_NOTE
            + '[mcp_servers.repomarshal]\ncommand = "repomarshal"\nargs = ["mcp"]\n\n'
            + _AGENTS_LINE
        )
    if host == "cursor":
        return (
            "# Cursor: MCP server in .cursor/mcp.json (project) or ~/.cursor/mcp.json. UNTESTED:\n"
            "# RepoMarshal has no Cursor hook shim; this config is not exercised by its tests.\n"
            + _MCP_NOTE
            + _json({"mcpServers": mcp_servers})
            + "\n"
            + _AGENTS_LINE
        )
    if host == "copilot":
        return (
            "# GitHub Copilot (VS Code agent mode): MCP server in .vscode/mcp.json. UNTESTED:\n"
            "# RepoMarshal has no Copilot hook shim; this config is not exercised by its tests.\n"
            + _MCP_NOTE
            + _json({"servers": {"repomarshal": {"type": "stdio", **mcp_servers["repomarshal"]}}})
            + "\n"
            + _AGENTS_LINE
        )
    if host == "gemini":
        return (
            "# Gemini CLI: MCP server in .gemini/settings.json (project) or\n"
            "# ~/.gemini/settings.json. UNTESTED: RepoMarshal has no Gemini hook shim; this\n"
            "# config is not exercised by its tests.\n"
            + _MCP_NOTE
            + _json({"mcpServers": mcp_servers})
            + "\n"
            + _AGENTS_LINE
        )
    raise ValueError(f"unknown host {host!r}")


def run(args: argparse.Namespace) -> int:
    host = getattr(args, "print_host", None)
    if host:
        try:
            sys.stdout.write(_snippet(host))
        except ValueError as e:
            print(f"repomarshal init: {e}", file=sys.stderr)
            return 1
        return 0

    try:
        root = git.repo_root(getattr(args, "repo", "."))
    except (NotAGitRepository, GitError) as e:
        print(f"repomarshal init: {e}", file=sys.stderr)
        return 3
    path = root / POLICY_FILE
    try:
        with open(path, "x", encoding="utf-8") as fh:  # O_EXCL: never overwrite, never follow
            fh.write(TEMPLATE)
    except FileExistsError:
        print(
            f"repomarshal init: {path} already exists; not overwriting it. "
            "Edit it, or remove it first.",
            file=sys.stderr,
        )
        return 1
    except OSError as e:
        print(f"repomarshal init: cannot create {path}: {e.strerror or e}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    print(NEXT_STEPS.format(path=POLICY_FILE), end="")
    return 0
