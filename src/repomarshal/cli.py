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

v0.4 split CI (threat T26; behaviour in repomarshal.ci):
    repomarshal ci plan    --base SHA [--out FILE] [--github-output]   exit 0 when written
    repomarshal ci gate    --base SHA --gate ID --expect JSON --out FILE  exit 0/1/2
    repomarshal ci verdict --base SHA --expect JSON --results DIR [--json]  exit = verdict
    repomarshal ci passed  --result FILE                                    exit 0 iff PASS
    repomarshal ci trusted-verdict --run-id N --run-attempt N --head-sha SHA --results DIR
        (v0.5) the default-branch workflow_run judge; exit = verdict
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
        "--waivers",
        choices=["none", "local", "github"],
        default="local",
        help="where human waivers may come from (default: local; CI: github). Hook mode: none",
    )
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

    wv = sub.add_parser("waive", help="record a human waiver for one gate (interactive only)")
    wv.add_argument("gate", help="gate id (e.g. backend:unit) or kind for a kind-level decision")
    wv.add_argument("--reason", required=True, help="why this gate may be accepted unverified")
    wv.add_argument("--repo", default=".", help="repository path (default: .)")

    dr = sub.add_parser("doctor", help="report the detected stack, gates, providers and config")
    dr.add_argument("--repo", default=".", help="repository path (default: .)")
    dr.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("mcp", help="serve plan/done/explain over MCP stdio (needs repomarshal[mcp])")

    ci = sub.add_parser("ci", help="split CI verification: plan, isolated gate jobs, verdict")
    cisub = ci.add_subparsers(dest="ci_command", required=True)
    cp = cisub.add_parser("plan", help="plan job: the plan, binding data and the gate matrix")
    cg = cisub.add_parser("gate", help="gate job: run one isolated gate, write its raw result")
    cv = cisub.add_parser("verdict", help="verdict job: bind the gate results and fold")
    ct = cisub.add_parser(
        "trusted-verdict",
        help="trusted workflow (workflow_run): verdict for the triggering ci run of a PR",
    )
    ct.add_argument("--repo", default=".", help="checkout of the PR head (data only)")
    ct.add_argument("--run-id", required=True, help="the triggering ci run id")
    ct.add_argument("--run-attempt", required=True, help="the triggering ci run attempt")
    ct.add_argument("--head-sha", required=True, help="the triggering run's head SHA")
    ct.add_argument("--results", required=True, help="directory with rm-gate-<i>/result.json")
    ct.add_argument("--waivers", choices=["none", "github"], default="github")
    ct.add_argument("--json", action="store_true", help="machine-readable output")
    ct.add_argument(
        "--github-output",
        action="store_true",
        help="write the commit status state/description to the file named by $GITHUB_OUTPUT",
    )
    cpass = cisub.add_parser("passed", help="exit 0 exactly when a gate result file says PASS")
    cpass.add_argument("--result", required=True, help="the gate job's result.json")
    for c in (cp, cg, cv):
        c.add_argument("--repo", default=".", help="repository path (default: .)")
        c.add_argument("--base", required=True, help="BASE commit (the PR base SHA)")
    for c in (cp, cv):
        c.add_argument(
            "--waivers", choices=["none", "github"], default="github", help="waiver source"
        )
    cp.add_argument("--out", default=None, help="write the plan job output JSON here")
    cp.add_argument(
        "--github-output",
        action="store_true",
        help="also write ok/expect/matrix/count to the file named by $GITHUB_OUTPUT",
    )
    cg.add_argument("--gate", required=True, help="gate id from the plan job's matrix")
    for c in (cg, cv):
        c.add_argument("--expect", default="", help="the plan job's `expect` output (JSON)")
    cg.add_argument("--out", required=True, help="result file to write")
    cv.add_argument("--results", required=True, help="directory with rm-gate-<i>/result.json")
    cv.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "done" and args.hook:
        from repomarshal import hooks  # lazy: hook mode handles every error itself (fail closed)

        return hooks.run(args.hook, None, repo=args.repo, base=args.base)  # reads stdin itself
    if args.command == "mcp":
        from repomarshal import mcp_server  # lazy: needs the optional `mcp` extra

        return mcp_server.main()
    if args.command == "init":
        from repomarshal import initcmd

        return initcmd.run(args)
    if args.command == "waive":
        from repomarshal import waivers

        return waivers.cli_waive(args)
    if args.command == "doctor":
        from repomarshal import doctor

        return doctor.run(args)
    if args.command == "ci":
        try:
            return _ci(args)
        except Exception:  # noqa: BLE001 - internal errors must never look like PASS
            traceback.print_exc()
            print("repomarshal ci: internal error (exit 4)", file=sys.stderr)
            return EXIT_INTERNAL_ERROR
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
    r = api.done(
        args.repo or ".",
        task=args.task,
        base=args.base,
        dry_run=args.dry_run,
        waivers=args.waivers,
    )
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


def _expect(text: str) -> dict | None:
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _ci(args: argparse.Namespace) -> int:
    import os
    from pathlib import Path

    from repomarshal import ci

    env = dict(os.environ)
    if args.ci_command == "passed":
        return ci.passed_step(Path(args.result))
    if args.ci_command == "trusted-verdict":
        r = ci.trusted_verdict(
            args.repo,
            args.run_id,
            args.run_attempt,
            args.head_sha,
            Path(args.results),
            env,
            waivers=args.waivers,
        )
        state, description = ci.commit_status(r)
        if args.github_output:
            target = env.get("GITHUB_OUTPUT")
            if not target:
                print("repomarshal ci: GITHUB_OUTPUT is not set", file=sys.stderr)
                return EXIT_INTERNAL_ERROR
            with open(target, "a", encoding="utf-8") as f:
                f.write(f"state={state}\ndescription={description}\n")
        _print_result(r, args.json)
        return r.verdict.exit_code
    if args.ci_command == "plan":
        out = ci.plan_step(args.repo, args.base, env, waivers=args.waivers)
        text = json.dumps(out, indent=2, sort_keys=True)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(text)
        if args.github_output:
            target = env.get("GITHUB_OUTPUT")
            if not target:
                print("repomarshal ci: GITHUB_OUTPUT is not set", file=sys.stderr)
                return EXIT_INTERNAL_ERROR
            with open(target, "a", encoding="utf-8") as f:
                f.write(f"ok={'true' if out['ok'] else 'false'}\n")
                f.write(f"expect={json.dumps(out['expect'], separators=(',', ':'))}\n")
                f.write(f"matrix={json.dumps(out['matrix'], separators=(',', ':'))}\n")
                f.write(f"count={len(out['matrix'])}\n")
        return 0
    if args.ci_command == "gate":
        expect = _expect(args.expect)
        if expect is None:
            print("repomarshal ci gate: --expect is not a JSON object", file=sys.stderr)
            return 2
        return ci.gate_step(args.repo, args.base, args.gate, expect, Path(args.out), env)
    r = ci.verdict_step(
        args.repo, args.base, _expect(args.expect), Path(args.results), env, waivers=args.waivers
    )
    _print_result(r, args.json)
    return r.verdict.exit_code


def _print_result(r: api.DoneResult, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "verdict": r.verdict.value,
                    "reasons": list(r.reasons),
                    "evidence_path": str(r.evidence_path) if r.evidence_path else None,
                    "record": to_dict(r.record) if r.record else None,
                },
                indent=2,
            )
        )
    else:
        print(render.done_text(r))
