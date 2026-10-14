"""Plan assembly (owner: lead). Wires git -> policy -> inventory -> signals -> decisions ->
context -> skills into a hashed Plan (architecture §4.2)."""

from __future__ import annotations

from pathlib import Path

from repomarshal import __version__, context, decisions, git, inventory, policy, signals, skills
from repomarshal.model import Plan, PlanMode, PolicyRef, Risk


def compile_plan(
    repo: Path, task: str | None = None, base: str | None = None, path_env: str | None = None
) -> Plan:
    """Compile the Change Plan for the current working tree.

    mode = BINDING when the change set has files, FORECAST otherwise. Raises PolicyInvalid,
    GitError, NotAGitRepository (callers map these to BLOCKED).
    """
    root = git.repo_root(repo)
    base_sha = git.resolve_base(root, base)
    pol = policy.load_policy(root, base_sha)
    change = git.collect_changes(root, base_sha)
    inv = inventory.build_inventory(root, base_sha, pol, path_env=path_env, change=change)
    # A binding plan is compiled from the diff alone. Task text only shapes a forecast (no diff
    # yet); in a binding plan it is recorded in `task` but never decides gates, risk or skills.
    # It therefore cannot remove a requirement in either mode (ADR-005).
    diff_signals = signals.evaluate_signals(root, change, pol)
    chosen = diff_signals if change.files else signals.task_signals(task)
    sigs = tuple(sorted(chosen, key=lambda s: s.id))
    risk = decisions.assess_risk(sigs)
    gate_decisions = decisions.decide_gates(sigs, inv, change)
    doms = decisions.domains_of(sigs)
    ctx = context.select_context(root, change, sigs, task, pol.token_budget)
    picks = skills.select_skills(
        skills.discover_skills(root, base_sha), doms, change, pol.skills_max
    )
    return Plan(
        mode=PlanMode.BINDING if change.files else PlanMode.FORECAST,
        task=task,
        change=change,
        signals=sigs,
        domains=doms,
        risk=risk,
        inventory=inv.gates,
        gates=gate_decisions,
        context=ctx,
        skills=picks,
        evidence_level="elevated" if risk == Risk.ELEVATED else "standard",
        policy=PolicyRef(
            source=pol.source,
            digest=pol.digest,
            waivers_permit_pass=pol.waivers_permit_pass,
            allow_author_waivers=pol.allow_author_waivers,
            waiver_signers=pol.waiver_signers,
        ),
        generator={"name": "repomarshal", "version": __version__},
    ).with_hash()
