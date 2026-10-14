"""Reproduce the field evaluation in docs/evaluation.md (not a test module).

    python tests/field/field_eval.py run   FIELD_DIR OUT_DIR   # plan x5 (timed) + done --dry-run
    python tests/field/field_eval.py score OUT_DIR             # judge against EXPECTED, aggregate
    python tests/field/field_eval.py score OUT_DIR --adjusted  # ... plus the rule-adjusted score

FIELD_DIR holds the read-only clones (click, rich, express, zustand, zod). `run` checks out each
case commit detached, passes `--base <commit>^`, and restores each clone's HEAD afterwards. It
never runs repository code: only `repomarshal plan` and `repomarshal done --dry-run`.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
CASES: tuple[tuple[str, str], ...] = (
    ("click", "fa99fbd"), ("click", "fc518e4"), ("click", "b8a4486"), ("click", "0c9e836"),
    ("click", "3b16957"),
    ("rich", "ce51a2f"), ("rich", "7ef2d05"), ("rich", "905b397"), ("rich", "eccb594"),
    ("rich", "285d9e9"),
    ("express", "53d4a0d"), ("express", "9a34acf"), ("express", "b4ab7d6"), ("express", "3b7e39f"),
    ("express", "3ce6d0e"), ("express", "2eae22b"),
    ("zustand", "b57db4f"), ("zustand", "3febf8c"), ("zustand", "95d3f33"), ("zustand", "1f531ba"),
    ("zustand", "f094eeb"),
    ("zod", "34a626f"), ("zod", "680d19f"), ("zod", "07917f4"), ("zod", "f6e1701"),
    ("zod", "9c21983"), ("zod", "e54716c"),
)  # fmt: skip

# Hand judgment, written per case from the diff and the repository's CI: the gate decisions
# (gate id, or kind for a kind-level decision) that should be REQUIRED or UNAVAILABLE, given the
# inventory RepoMarshal found. Rationale per case: docs/evaluation.md §4.
D, L, T, U, F = "native:docs-links", "root:lint", "root:typecheck", "root:unit", "root:format"
SEC, DEP = "native:secrets-diff", "dep_audit"
EXPECTED: dict[str, set[str]] = {
    "fa99fbd": {D}, "fc518e4": {D, L, T, U}, "b8a4486": {L, T, U}, "0c9e836": {L, T, U, SEC},
    "3b16957": {D, L, T, U, DEP},
    "ce51a2f": {D}, "7ef2d05": {T, U}, "905b397": {T, U}, "eccb594": {T, U, SEC},
    "285d9e9": {D, T, U},
    "53d4a0d": {D}, "9a34acf": {L, U}, "b4ab7d6": {L, U}, "3b7e39f": {L, U, DEP},
    "3ce6d0e": {L, U, SEC}, "2eae22b": {L, U},
    "b57db4f": {D}, "3febf8c": {U}, "95d3f33": {U}, "1f531ba": {U, SEC, DEP}, "f094eeb": {U, SEC},
    "34a626f": {D, F, L, "runtime", "a11y"}, "680d19f": {F, L, U}, "07917f4": {F, L, U},
    "f6e1701": {F, L, U, DEP}, "9c21983": {F, L, U, SEC}, "e54716c": {F, L, "runtime", "a11y"},
}  # fmt: skip
EXPENSIVE = {"integration", "runtime", "a11y", "dep_audit", "security_scan", "skill_audit"}
SELECTED = ("REQUIRED", "UNAVAILABLE")

# Rule-adjusted score (docs/evaluation.md §9, §10). EXPECTED was written against the 9cf7d64
# inventory, which lacked some real checks (rich `make format-check`, zustand `test:lint`,
# `test:types`, `test:format`, ...). The §2 judgment rule says a code or test change in a scope
# needs "that scope's lint, format, typecheck and unit gates", so with `--adjusted` a selected
# gate that EXPECTED does not list counts as needed when, and only when:
#   - its kind is format, lint or typecheck (unit is not adjusted: every unit gate the old
#     inventory could see is in EXPECTED, and a new one, e.g. a workspace package's, is a
#     duplicate candidate that must be judged, not assumed), and
#   - a file of the case's code.changed or tests.changed signal lies in the gate's scope (the
#     root scope "" contains every file).
# Nothing else changes: EXPECTED itself stays frozen, and FN and the expensive-gate count are
# computed as without --adjusted. The gate scope comes from the plan's own inventory.
ADJUSTED_KINDS = {"format", "lint", "typecheck"}
ADJUSTING_SIGNALS = ("code.changed", "tests.changed")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _rm(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(SRC))
    cmd = [sys.executable, "-m", "repomarshal", *args]
    return subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, check=False)


def run(field: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    heads = {name: _git(field / name, "rev-parse", "HEAD") for name, _ in CASES}
    summary = []
    try:
        for name, short in CASES:
            repo = field / name
            sha = _git(repo, "rev-parse", short)
            _git(repo, "checkout", "-q", "--detach", sha)
            args = ["plan", "--json", "--base", sha + "^"]
            times = []
            for _ in range(5):
                t0 = time.perf_counter()
                p = _rm(repo, args)
                times.append(time.perf_counter() - t0)
                if p.returncode != 0:
                    raise RuntimeError(p.stderr)
            d = _rm(repo, ["done", "--dry-run", "--json", "--base", sha + "^"])
            (out / f"{name}-{short}.plan.json").write_text(p.stdout)
            (out / f"{name}-{short}.done.json").write_text(d.stdout)
            plan, done = json.loads(p.stdout), json.loads(d.stdout)
            summary.append(
                {
                    "repo": name,
                    "sha": sha,
                    "subject": _git(repo, "log", "-1", "--format=%s", sha),
                    "files": len(plan["change"]["files"]),
                    "signals": {s["id"]: s["files"] for s in plan["signals"]},
                    "risk": plan["risk"],
                    "gates": [(g["gate_id"] or g["kind"], g["status"]) for g in plan["gates"]],
                    "kinds": {g["id"]: g["kind"] for g in plan["inventory"]},
                    "done_exit": d.returncode,
                    "done_verdict": done["verdict"],
                    "would_run": done["would_run"],
                    "plan_s": [round(t, 3) for t in times],
                    "ctx_selected": plan["context"]["selected_tokens"],
                    "ctx_candidate": plan["context"]["candidate_tokens"],
                }
            )
            print(name, short, round(statistics.median(times), 3), flush=True)
    finally:
        for name, head in heads.items():
            _git(field / name, "checkout", "-q", head)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))


def _scopes(out: Path, c: dict) -> dict[str, str]:
    """Gate id -> scope, from the case's saved plan."""
    plan = json.loads((out / f"{c['repo']}-{c['sha'][:7]}.plan.json").read_text())
    return {g["id"]: g["scope"] for g in plan["inventory"]}


def rule_needed(c: dict, scopes: dict[str, str], selected: set[str]) -> set[str]:
    """Selected gates not in EXPECTED that the §2 rule requires (see ADJUSTED_KINDS)."""
    changed = {f for sid in ADJUSTING_SIGNALS for f in c["signals"].get(sid, ())}
    out = set()
    for gate in selected - EXPECTED[c["sha"][:7]]:
        scope = scopes.get(gate)
        if c["kinds"].get(gate) in ADJUSTED_KINDS and scope is not None:
            if any(f.startswith(scope) for f in changed):
                out.add(gate)
    return out


def score(out: Path, adjusted: bool = False) -> None:
    summary = json.loads((out / "summary.json").read_text())
    totals: Counter[str] = Counter()
    per_kind: dict[str, Counter[str]] = {}
    unnecessary_expensive = 0
    rule_adjusted = 0
    for c in summary:
        short, kinds = c["sha"][:7], c["kinds"]
        sel = {g for g, s in c["gates"] if s in SELECTED}
        exp = EXPECTED[short]
        buckets = {"tp": sel & exp, "fp": sel - exp, "fn": exp - sel}
        adj = rule_needed(c, _scopes(out, c), sel) if adjusted else set()
        rule_adjusted += len(adj)
        for name, items in buckets.items():
            totals[name] += len(items)
            for item in items:
                per_kind.setdefault(kinds.get(item, item), Counter())[name] += 1
        extra = sorted(i for i in buckets["fp"] if kinds.get(i, i) in EXPENSIVE)
        unnecessary_expensive += bool(extra)
        unavailable = sorted(g for g, s in c["gates"] if s == "UNAVAILABLE")
        print(
            f"{c['repo']} {short} risk={c['risk']} selected={sorted(sel)} "
            f"fp={sorted(buckets['fp'])} fn={sorted(buckets['fn'])} unavailable={unavailable} "
            + (f"rule_needed={sorted(adj)} " if adjusted else "")
            + f"plan_s={c['plan_s']} ctx={c['ctx_selected']}/{c['ctx_candidate']} "
            f"dry_run={c['done_verdict']}({c['done_exit']}) would_run={c['would_run']}"
        )
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    print(f"TP={tp} FP={fp} FN={fn} precision={tp / (tp + fp):.3f} recall={tp / (tp + fn):.3f}")
    if adjusted:
        atp, afp = tp + rule_adjusted, fp - rule_adjusted
        print(
            f"rule-adjusted: {rule_adjusted} selected format/lint/typecheck gates in a scope "
            f"with a code or test change counted as needed; TP={atp} FP={afp} "
            f"precision={atp / (atp + afp):.3f} recall={atp / (atp + fn):.3f}"
        )
    for kind in sorted(per_kind):
        print(f"  {kind}: {dict(per_kind[kind])}")
    print(f"cases selecting an unnecessary expensive gate: {unnecessary_expensive}/{len(summary)}")
    times = [t for c in summary for t in c["plan_s"]]
    medians = [statistics.median(c["plan_s"]) for c in summary]
    print(
        f"plan latency over {len(times)} runs: median {statistics.median(times):.3f}s, "
        f"max {max(times):.3f}s; per-case medians {min(medians):.3f}-{max(medians):.3f}s"
    )
    sel = sum(c["ctx_selected"] for c in summary)
    cand = sum(c["ctx_candidate"] for c in summary)
    print(f"context tokens (bytes/4): selected {sel} of candidate {cand} ({sel / cand:.1%})")


if __name__ == "__main__":
    if sys.argv[1:2] == ["run"] and len(sys.argv) == 4:
        run(Path(sys.argv[2]), Path(sys.argv[3]))
    elif sys.argv[1:2] == ["score"] and len(sys.argv) == 3:
        score(Path(sys.argv[2]))
    elif sys.argv[1:2] == ["score"] and len(sys.argv) == 4 and sys.argv[3] == "--adjusted":
        score(Path(sys.argv[2]), adjusted=True)
    else:
        sys.exit(__doc__)
