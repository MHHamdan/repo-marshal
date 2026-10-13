"""Command line (owner: lead; roadmap M10, M11; ADR-003).

    repomarshal plan [TASK] [--repo PATH] [--base REF] [--json] [--explain]
        exit 0 when a plan was produced; 3 BLOCKED (invalid policy, not a git repo); 4 internal.
        --json prints model.to_dict(plan) (validates against schemas/plan.schema.json).
    repomarshal done [--task TEXT] [--repo PATH] [--base REF] [--json] [--dry-run]
        exit = verdict code: 0 PASS, 1 FAIL, 2 UNKNOWN, 3 BLOCKED, 4 internal error.
        --json prints {"verdict", "reasons", "evidence_path", "record"} where record validates
        against schemas/evidence.schema.json (null on dry-run / blocked-before-plan).
Human output ends `done` with the advisory line from EvidenceRecord.advisory.

v0.2 (dispatch only; behaviour lives in the named modules):
    repomarshal init [--repo PATH] [--print HOST]      -> repomarshal.initcmd.run(args)
    repomarshal doctor [--repo PATH] [--json]          -> repomarshal.doctor.run(args)
    repomarshal done --hook claude-code [--repo PATH]  -> repomarshal.hooks.run(...) (stdin)
    repomarshal mcp                                    -> repomarshal.mcp_server.main()  (extra)
The hook and MCP paths are imported lazily so `plan` and `done` never load them.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from repomarshal import __version__, api, render
from repomarshal.model import (
    EXIT_INTERNAL_ERROR,
    EvidenceRecord,
    GitError,
    NotAGitRepository,
    PolicyInvalid,
    Verdict,
    to_dict,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="repomarshal",
        description="Compile a repository change into an explained plan, then verify it.",
    )
    p.add_argument("--version", action="version", version=f"repomarshal {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("plan", help="show the Change Plan for the current change")
    pl.add_argument("task", nargs="?", default=None, help="task description (untrusted; add-only)")
    pl.add_argument("--repo", default=".", help="repository path (default: .)")
    pl.add_argument("--base", default=None, help="BASE revision (default: auto)")
    pl.add_argument("--json", action="store_true", help="machine-readable output")
    pl.add_argument("--explain", action="store_true", help="every reason for every gate")

    dn = sub.add_parser("done", help="run the required gates and return a completion verdict")
    dn.add_argument("--task", default=None, help="task description (untrusted; add-only)")
    dn.add_argument(
        "--repo", default=None, help="repository path (default: .; with --hook: payload cwd)"
    )
    dn.add_argument("--base", default=None, help="BASE revision (default: auto)")
    dn.add_argument("--json", action="store_true", help="machine-readable output")
    dn.add_argument("--dry-run", action="store_true", help="list the gates; run nothing")
    dn.add_argument(
        "--hook",
        choices=["claude-code"],
        default=None,
        help="run as an agent host hook: read the hook payload on stdin, answer in its format",
    )

    it = sub.add_parser("init", help="write a commented repomarshal.toml; touch nothing else")
    it.add_argument("--repo", default=".", help="repository path (default: .)")
    it.add_argument(
        "--print",
        dest="print_host",
        choices=["claude-code", "codex", "cursor", "copilot", "gemini"],
        default=None,
        help="print the hook/MCP config snippet for an agent host instead of writing files",
    )

    dr = sub.add_parser("doctor", help="report the detected stack, gates, providers and config")
    dr.add_argument("--repo", default=".", help="repository path (default: .)")
    dr.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("mcp", help="serve plan/done/explain over MCP stdio (needs repomarshal[mcp])")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "done" and args.hook:
        from repomarshal import hooks  # lazy: hook mode handles every error itself (fail closed)

        return hooks.run(args.hook, sys.stdin.read(), repo=args.repo, base=args.base)
    if args.command == "mcp":
        from repomarshal import mcp_server  # lazy: needs the optional `mcp` extra

        return mcp_server.main()
    if args.command == "init":
        from repomarshal import initcmd

        return initcmd.run(args)
    if args.command == "doctor":
        from repomarshal import doctor

        return doctor.run(args)
    try:
        if args.command == "plan":
            return _plan(args)
        return _done(args)
    except (PolicyInvalid, GitError, NotAGitRepository) as e:
        errors = e.errors if isinstance(e, PolicyInvalid) else [str(e)]
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "verdict": Verdict.BLOCKED.value,
                        "reasons": errors,
                        "evidence_path": None,
                        "record": None,
                    },
                    indent=2,
                )
            )
        else:
            print("BLOCKED", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
        return Verdict.BLOCKED.exit_code
    except Exception:  # noqa: BLE001 - internal errors must never look like PASS
        traceback.print_exc()
        print("repomarshal: internal error (exit 4)", file=sys.stderr)
        return EXIT_INTERNAL_ERROR


def _plan(args: argparse.Namespace) -> int:
    p = api.plan(args.repo, task=args.task, base=args.base)
    if args.json:
        print(json.dumps(to_dict(p), indent=2))
    else:
        print(render.plan_text(p, explain=args.explain))
    return 0


def _done(args: argparse.Namespace) -> int:
    r = api.done(args.repo or ".", task=args.task, base=args.base, dry_run=args.dry_run)
    if args.json:
        print(
            json.dumps(
                {
                    "verdict": r.verdict.value,
                    "reasons": list(r.reasons),
                    "evidence_path": str(r.evidence_path) if r.evidence_path else None,
                    "record": to_dict(r.record) if r.record else None,
                    "would_run": list(r.would_run),
                },
                indent=2,
            )
        )
    else:
        print(render.done_text(r))
        print(f"\n{EvidenceRecord.advisory}")
    return r.verdict.exit_code
