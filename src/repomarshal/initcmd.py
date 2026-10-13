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
"""

from __future__ import annotations

import argparse


def run(args: argparse.Namespace) -> int:
    raise NotImplementedError
