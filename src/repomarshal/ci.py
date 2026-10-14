"""Split CI verification (v0.4; threat T26). Owner: lead.

In one CI job, the gates run the pull request's code as the same user as the judge, so that code
can tamper with whatever the judge reads or writes afterwards. The split keeps them apart:

    plan job     `repomarshal ci plan`     BASE judge: compile the plan, check the checkout is
                                           the PR head (or GitHub's merge commit of it), read
                                           the waivers for the record, and list the gates that
                                           run PR code ("isolated gates"). No PR code runs.
    gate jobs    `repomarshal ci gate`     one fresh job per isolated gate (a matrix), with no
                                           token and no permissions: run that one gate, write
                                           its raw result, exit 0 PASS / 1 FAIL / 2 otherwise.
    verdict job  `repomarshal ci verdict`  BASE judge on a fresh runner where no PR code has
                                           run: recompile the plan, re-read the waivers, run
                                           the native gates itself (they never execute
                                           repository code), bind every isolated result, fold.

A result counts only when it binds. Otherwise it is UNKNOWN (fail closed). Binding means:
- the verdict job's own plan equals the plan job's: plan hash, HEAD, tree digest and the
  isolated gate list, all passed through job outputs (`--expect`);
- the checkout is the PR head SHA, or a merge commit whose second parent is the PR head SHA. The
  PR head SHA comes from the event payload, read before any PR code runs;
- the result file names the same gate, plan hash, HEAD and tree, and the gate did not change the
  tree;
- GitHub's own record of that gate's job agrees with the file. Read through the Actions API
  (`actions: read`). The STATUS comes from that record: step "run gate" fails exactly when the
  gate FAILed, and step "gate passed" succeeds exactly when it PASSed. The file adds details
  only if it agrees, and the "upload result" step must have succeeded for a PASS. PR code in
  one gate job can read the runner's artifact token and upload a forged file under another
  gate's name, but it cannot change another job's step conclusions. A forgery can at worst hide
  a PASS's details (UNKNOWN). It can never create a PASS, nor turn a real FAIL into a waivable
  UNKNOWN.

What a gate job reports about ITSELF is only as trustworthy as that gate, as in any CI: the PR's
code decides its own exit code. The split guarantees that no gate influences the plan, the
waivers, the other gates' results, or the verdict.
"""

from __future__ import annotations

import json
import os
import stat
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from repomarshal import api, git, runner
from repomarshal.api import DoneResult
from repomarshal.model import (
    GateKind,
    GateResult,
    ResultStatus,
    Verdict,
    to_dict,
)

SCHEMA = "repomarshal.ci/1"
JOB_PREFIX = "rm-gate-"
STEP_RUN = "run gate"  # fails exactly when the gate FAILed (exit 1)
STEP_PASSED = "gate passed"  # succeeds exactly when the gate PASSed
STEP_UPLOAD = "upload result"
RESULT_FILE = "result.json"
MAX_RESULT_BYTES = 1 << 20


# --------------------------------------------------------------------------- shared


def isolated_gates(prep: api.Prepared) -> list[str]:
    """Required gates that execute repository code, in run order. Native gates are run by the
    verdict job itself (they read only git objects)."""
    return [
        d.gate_id
        for d in prep.required
        if d.gate_id in prep.inventory and prep.inventory[d.gate_id].provider != "native"
    ]


def head_binding(root: Path, env: Mapping[str, str]) -> tuple[str, str | None, str | None]:
    """(HEAD sha, PR head sha, problem). Fine when HEAD is the PR head, or GitHub's merge commit
    whose second parent is the PR head. Read the event file only before PR code runs."""
    head = git._git(root, "rev-parse", "HEAD").stdout.strip()
    pr_head = None
    path = env.get("GITHUB_EVENT_PATH", "")
    payload = _read_json(Path(path)) if path else None
    pr = payload.get("pull_request") if isinstance(payload, dict) else None
    if isinstance(pr, dict) and isinstance(pr.get("head"), dict):
        sha = pr["head"].get("sha")
        pr_head = sha if isinstance(sha, str) and len(sha) == 40 else None
    if pr_head is None:
        return head, None, "no pull_request head SHA in the GitHub event; cannot bind results"
    if head == pr_head:
        return head, pr_head, None
    parents = git._git(root, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    if len(parents) == 3 and parents[2] == pr_head:
        return head, pr_head, None
    return (
        head,
        pr_head,
        f"checkout {head[:12]} is neither the PR head {pr_head[:12]} nor a merge commit of it",
    )


def _read_json(path: Path, limit: int = MAX_RESULT_BYTES) -> Any:
    """Bounded read of a regular file (no final symlink, no FIFO); None on any problem."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > limit:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _expectation(prep: api.Prepared, head: str) -> dict[str, Any]:
    return {
        "plan_hash": prep.plan.plan_hash,
        "head": head,
        "tree": prep.plan.change.tree_digest,
        "gates": isolated_gates(prep),
    }


# --------------------------------------------------------------------------- plan job


def plan_step(
    repo: str | Path, base: str | None, env: Mapping[str, str], waivers: str = "github"
) -> dict[str, Any]:
    """The plan job's output. `ok` false means the verdict job will not get a PASS; the matrix
    is then empty and the verdict job reports why."""
    prep = api.prepare(repo, base=base, env=env, remember=False)
    if isinstance(prep, DoneResult):
        return {
            "schema": SCHEMA,
            "ok": False,
            "verdict": prep.verdict.value,
            "reasons": list(prep.reasons),
            "expect": None,
            "matrix": [],
        }
    head, pr_head, problem = head_binding(prep.root, env)
    granted, notes = api.collect_waivers(prep, waivers, env)
    expect = _expectation(prep, head)
    return {
        "schema": SCHEMA,
        "ok": problem is None,
        "verdict": None,
        "reasons": [problem] if problem else [],
        "base": prep.plan.change.base,
        "pr_head": pr_head,
        "expect": expect,
        "matrix": [{"index": i, "gate": g} for i, g in enumerate(expect["gates"])],
        "waivers": [to_dict(w) for w in granted],
        "waiver_notes": list(notes),
    }


# --------------------------------------------------------------------------- gate job


def gate_step(
    repo: str | Path,
    base: str | None,
    gate_id: str,
    expect: Mapping[str, Any],
    out: Path,
    env: Mapping[str, str],
) -> int:
    """Run exactly one isolated gate and write its raw result. Exit 0 PASS, 1 FAIL, 2 other."""

    def write(result: GateResult, head: str, before: str, after: str) -> int:
        out.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "schema": SCHEMA,
            "gate_id": gate_id,
            "plan_hash": expect.get("plan_hash"),
            "head": head,
            "tree_before": before,
            "tree_after": after,
            "result": to_dict(result),
        }
        out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return {ResultStatus.PASS: 0, ResultStatus.FAIL: 1}.get(result.status, 2)

    def unknown(why: str, head: str = "", tree: str = "") -> int:
        result = GateResult(gate_id, GateKind.UNIT, ResultStatus.UNKNOWN, why)
        return write(result, head, tree, tree)

    prep = api.prepare(repo, base=base, env=env, remember=False)
    if isinstance(prep, DoneResult):
        return unknown("no runnable plan: " + "; ".join(prep.reasons))
    head = git._git(prep.root, "rev-parse", "HEAD").stdout.strip()
    tree = prep.plan.change.tree_digest
    mine = _expectation(prep, head)
    for key in ("plan_hash", "head", "tree", "gates"):
        if mine[key] != expect.get(key):
            return unknown(f"this job's {key} differs from the plan job's; not run", head, tree)
    if gate_id not in mine["gates"]:
        return unknown("not an isolated required gate of this plan; not run", head, tree)
    gate = prep.inventory[gate_id]
    decision = next(d for d in prep.required if d.gate_id == gate_id)
    before = git.tree_digest(prep.root, base=prep.base_sha)
    result = runner.run_gate(prep.root, gate, prep.plan.change, env)
    after = git.tree_digest(prep.root, base=prep.base_sha)
    return write(api.adjust(decision, result, before, after), head, before, after)


# --------------------------------------------------------------------------- verdict job


JobSteps = dict[str, dict[str, str]]  # job name -> step name -> conclusion


def fetch_job_steps(env: Mapping[str, str]) -> tuple[JobSteps | None, str | None]:
    """GitHub's record of this run attempt's jobs, via the Actions API. (None, why) on error."""
    from repomarshal import waivers  # lazy: urllib only on the CI path

    repo = env.get("GITHUB_REPOSITORY", "")
    # No default attempt: on a re-run, attempt 1's jobs are another run's evidence (v0.4 #4).
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    token = env.get("GITHUB_TOKEN", "")
    api_url = env.get("GITHUB_API_URL") or waivers.DEFAULT_API_URL
    if not (waivers._REPO_RE.fullmatch(repo) and run_id.isdigit() and attempt.isdigit()):
        return None, "GITHUB_REPOSITORY / GITHUB_RUN_ID / GITHUB_RUN_ATTEMPT missing or invalid"
    if not token:
        return None, "GITHUB_TOKEN is not set (the verdict job needs actions: read)"
    if urllib.parse.urlsplit(api_url).scheme not in ("https", "http"):
        return None, f"GITHUB_API_URL {api_url!r} is not http(s)"
    gh = waivers._GitHub(api_url, token)
    jobs: JobSteps = {}
    try:
        for page in range(1, waivers.MAX_PAGES + 1):
            data = gh.get(
                f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs",
                {"per_page": 100, "page": page},
            )
            batch = data.get("jobs") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                return None, "Actions jobs API: unexpected response"
            for job in batch:
                if not isinstance(job, dict) or not isinstance(job.get("name"), str):
                    continue
                if job["name"] in jobs:
                    return None, f"two jobs named {job['name']!r} in this run attempt"
                steps = job.get("steps") if isinstance(job.get("steps"), list) else []
                jobs[job["name"]] = {
                    str(s.get("name")): str(s.get("conclusion"))
                    for s in steps
                    if isinstance(s, dict)
                }
            if len(batch) < 100:
                return jobs, None
    except waivers._ApiError as e:
        return None, f"Actions jobs API error: {e}"
    return None, "Actions jobs API: too many pages"


def _github_status(steps: Mapping[str, str]) -> tuple[ResultStatus, str]:
    """The gate's outcome as GitHub recorded it for its own job. The workflow contract is:
    step "run gate" fails exactly when the gate FAILed, and step "gate passed" succeeds exactly
    when it PASSed. PR code in another job cannot change these conclusions, so a forged result
    file can never turn a real FAIL into a waivable UNKNOWN (v0.4 review #2)."""
    ran, passed = steps.get(STEP_RUN), steps.get(STEP_PASSED)
    if ran == "failure":
        return ResultStatus.FAIL, f"step {STEP_RUN!r} failed"
    if ran == "success" and passed == "success":
        return ResultStatus.PASS, f"steps {STEP_RUN!r} and {STEP_PASSED!r} succeeded"
    if ran == "success" and passed == "failure":
        return ResultStatus.UNKNOWN, f"step {STEP_PASSED!r} failed"
    return ResultStatus.UNKNOWN, f"steps {STEP_RUN!r}={ran!r}, {STEP_PASSED!r}={passed!r}"


def _bound_result(
    index: int,
    gate_id: str,
    kind: GateKind,
    results_dir: Path,
    expect: Mapping[str, Any],
    jobs: JobSteps | None,
    jobs_problem: str | None,
) -> tuple[GateResult, str]:
    """(result, tree digest after the gate) for one isolated gate.

    The STATUS comes from GitHub's record of the gate job (_github_status); the result file only
    adds details, and only when it binds (same gate, plan, HEAD, tree; uploaded by the job
    itself) and agrees with that record. A PASS needs a bound, agreeing file. A FAIL recorded by
    GitHub stays FAIL even without a usable file. Everything else is UNKNOWN."""
    name = f"{JOB_PREFIX}{index}"
    tree = expect["tree"]

    def plain(status: ResultStatus, why: str) -> tuple[GateResult, str]:
        prefix = "result not bound: " if status is ResultStatus.UNKNOWN else f"[{name}] "
        return GateResult(gate_id, kind, status, prefix + why), tree

    if jobs is None:
        return plain(ResultStatus.UNKNOWN, jobs_problem or "no GitHub record of the gate jobs")
    steps = jobs.get(name)
    if steps is None:
        return plain(ResultStatus.UNKNOWN, f"no job {name!r} in this run attempt")
    status, recorded = _github_status(steps)
    fallback = (
        (ResultStatus.FAIL, f"GitHub recorded the gate as failed ({recorded}); details: ")
        if status is ResultStatus.FAIL
        else (ResultStatus.UNKNOWN, f"job {name} ({recorded}): ")
    )

    def unusable(why: str) -> tuple[GateResult, str]:
        return plain(fallback[0], fallback[1] + why)

    if steps.get(STEP_UPLOAD) != "success":
        return unusable(f"step {STEP_UPLOAD!r} is {steps.get(STEP_UPLOAD)!r}")
    doc = _read_json(results_dir / name / RESULT_FILE)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return unusable(f"{name}/{RESULT_FILE} missing or not a {SCHEMA} result")
    for key, want in (
        ("gate_id", gate_id),
        ("plan_hash", expect["plan_hash"]),
        ("head", expect["head"]),
        ("tree_before", tree),
    ):
        if doc.get(key) != want:
            return unusable(f"{key} in the result file does not match this plan")
    raw = doc.get("result")
    if not isinstance(raw, dict) or raw.get("gate_id") != gate_id:
        return unusable("malformed result")
    if raw.get("status") != status.value:
        return unusable(f"the file says {raw.get('status')!r}, GitHub recorded {status.value}")

    def text(key: str) -> str | None:
        value = raw.get(key)
        return value if isinstance(value, str) else None

    argv = raw.get("argv")
    tail = raw.get("output_tail")
    code = raw.get("exit_code")
    duration = raw.get("duration_ms")
    result = GateResult(
        gate_id,
        kind,
        status,
        f"[{name}] {str(raw.get('reason') or '')[:2000]}",
        argv=tuple(str(a) for a in argv) if isinstance(argv, list) else (),
        exit_code=code if type(code) is int else None,
        duration_ms=duration if type(duration) is int and duration >= 0 else 0,
        stdout_sha256=text("stdout_sha256"),
        stderr_sha256=text("stderr_sha256"),
        output_tail=tuple(str(x) for x in tail[:40]) if isinstance(tail, list) else (),
        tool=text("tool"),
    )
    after = doc.get("tree_after")
    return result, after if isinstance(after, str) and after else "(missing)"


def passed_step(result_file: Path) -> int:
    """`repomarshal ci passed`: exit 0 exactly when the gate job's result file says PASS. The
    workflow's "gate passed" step, whose conclusion GitHub records (see _github_status)."""
    doc = _read_json(result_file)
    raw = doc.get("result") if isinstance(doc, dict) else None
    ok = isinstance(raw, dict) and raw.get("status") == ResultStatus.PASS.value
    return 0 if ok else 1


def verdict_step(
    repo: str | Path,
    base: str | None,
    expect: Mapping[str, Any] | None,
    results_dir: Path,
    env: Mapping[str, str],
    waivers: str = "github",
    jobs: JobSteps | None = None,
) -> DoneResult:
    """Fold the isolated gate results into a verdict, on a runner where no PR code ran.
    `jobs` is for tests; in CI it is read from the Actions API."""
    prep = api.prepare(repo, base=base, env=env, remember=False, record=True)
    if isinstance(prep, DoneResult):
        return prep
    root, p = prep.root, prep.plan
    head, _, problem = head_binding(root, env)
    mine = _expectation(prep, head)
    problems: list[str] = [problem] if problem else []
    if not isinstance(expect, Mapping):
        problems.append("no plan job output to bind to")
    else:
        problems += [
            f"{key} differs from the plan job's"
            for key in ("plan_hash", "head", "tree", "gates")
            if expect.get(key) != mine[key]
        ]
    granted, notes = api.collect_waivers(prep, waivers, env)
    jobs_problem = None
    if not problems and jobs is None and mine["gates"]:
        jobs, jobs_problem = fetch_job_steps(env)

    index = {g: i for i, g in enumerate(mine["gates"])}
    before = prep.before
    tree_changed = before != p.change.tree_digest
    results: list[GateResult] = []
    for d in prep.required:
        gate = prep.inventory.get(d.gate_id)
        if gate is None:
            continue
        now = before
        if gate.provider == "native":
            result = runner.run_gate(root, gate, p.change, env)  # reads git objects only
        elif problems:
            why = "result not bound: " + "; ".join(problems)
            result = GateResult(gate.id, gate.kind, ResultStatus.UNKNOWN, why)
        else:
            result, now = _bound_result(
                index[gate.id], gate.id, gate.kind, Path(results_dir), mine, jobs, jobs_problem
            )
            # Re-applied here: the gate job's own downgrade is not trusted to have happened.
            result = runner.apply_definition_changed(gate, result)
        tree_changed = tree_changed or now != before
        results.append(api.adjust(d, result, before, now))
    after = git.tree_digest(root, base=prep.base_sha)
    tree_changed = tree_changed or after != before
    extra = tuple(f"binding: {x}" for x in problems)
    # Binding problems are not waivable and hold even when only native gates ran (v0.4 #3).
    return api.finish(prep, results, after, tree_changed, granted, notes, extra, blockers=extra)


__all__ = [
    "SCHEMA",
    "Verdict",
    "fetch_job_steps",
    "gate_step",
    "head_binding",
    "isolated_gates",
    "plan_step",
    "verdict_step",
]
