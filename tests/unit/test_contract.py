"""Contract tests (owner: lead): the dataclasses serialise to JSON that matches the schemas."""

import json
from importlib import resources

import jsonschema

from repomarshal import model as m


def _schema(name):
    path = resources.files("repomarshal").joinpath(f"schemas/{name}.schema.json")
    return json.loads(path.read_text())


def _sample_plan() -> m.Plan:
    change = m.ChangeSet(
        base="a" * 40,
        head="a" * 40,
        tree_digest="b" * 40,
        files=(m.ChangedFile("docs/x.md", m.FileStatus.MODIFIED, added=("hi",)),),
        uncommitted=True,
    )
    gate = m.Gate(
        id="native:docs-links",
        kind=m.GateKind.DOCS,
        argv=(),
        cwd="",
        scope="",
        cost=m.Cost.CHEAP,
        source="builtin",
        provider="native",
    )
    return m.Plan(
        mode=m.PlanMode.BINDING,
        task="t",
        change=change,
        signals=(m.Signal("docs.changed", "docs", m.Risk.LOW, ("docs/x.md",)),),
        domains=("docs",),
        risk=m.Risk.LOW,
        inventory=(gate,),
        gates=(
            m.GateDecision(
                m.GateKind.DOCS,
                m.DecisionStatus.REQUIRED,
                (m.Reason("docs.changed fired", "docs.changed"),),
                gate.id,
            ),
        ),
        context=m.ContextPlan(
            items=(m.ContextItem("docs/x.md", "changed", 1, 3),),
            excluded=(),
            budget_tokens=12000,
            selected_tokens=3,
            candidate_tokens=3,
        ),
        skills=(),
        evidence_level="standard",
        policy=m.PolicyRef("default", m.sha256_bytes(b"default")),
        generator={"name": "repomarshal", "version": "0"},
    ).with_hash()


def test_plan_matches_schema_and_hash_is_stable():
    p = _sample_plan()
    jsonschema.validate(m.to_dict(p), _schema("plan"))
    assert p.plan_hash == p.with_hash().plan_hash


def test_evidence_matches_schema():
    rec = m.EvidenceRecord(
        plan_hash=_sample_plan().plan_hash,
        base=None,
        head=None,
        tree_digest_before="c" * 40,
        tree_digest_after="c" * 40,
        policy_digest=m.sha256_bytes(b"default"),
        results=(m.GateResult("native:docs-links", m.GateKind.DOCS, m.ResultStatus.PASS, "ok"),),
        verdict=m.Verdict.PASS,
        verdict_reasons=("all required gates passed",),
        repomarshal_version="0",
        created_at="2026-10-13T00:00:00Z",
    ).with_digest()
    jsonschema.validate(m.to_dict(rec), _schema("evidence"))


def test_verdict_exit_codes_and_risk_order():
    assert [v.exit_code for v in m.Verdict] == [0, 1, 2, 3]
    assert m.max_risk([m.Risk.LOW, m.Risk.ELEVATED, m.Risk.STANDARD]) is m.Risk.ELEVATED
    assert m.max_risk([]) is m.Risk.LOW


def test_core_package_imports_only_stdlib():
    import ast
    import sys
    from pathlib import Path

    pkg = Path(m.__file__).parent
    allowed = set(sys.stdlib_module_names) | {"repomarshal", "__future__"}
    for py in pkg.rglob("*.py"):
        if py.name == "mcp_server.py":  # the optional extra's module; checked separately below
            continue
        for node in ast.walk(ast.parse(py.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for n in names:
                assert n.split(".")[0] in allowed, f"{py.name} imports non-stdlib {n}"


def test_plan_and_done_paths_never_import_optional_extras():
    """`repomarshal plan`/`done` must not load the MCP SDK or the hook/MCP modules (cold start)."""
    import subprocess
    import sys

    code = (
        "import sys, repomarshal.cli, repomarshal.api, repomarshal.planner\n"
        "bad = [m for m in sys.modules if m == 'mcp' or m.startswith('mcp.')\n"
        "       or m in ('repomarshal.mcp_server', 'repomarshal.hooks',\n"
        "                'repomarshal.waivers', 'urllib.request')]\n"
        "print(','.join(bad))"
    )
    import os
    from pathlib import Path

    env = dict(os.environ)
    src = str(Path(m.__file__).resolve().parents[1])  # works installed or from a source tree
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH", "")) if p)
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    assert out.stdout.strip() == ""
