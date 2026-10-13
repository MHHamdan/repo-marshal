"""MCP stdio server (owner: agent "mcp"; roadmap SHOULD #7; ADR-003). Optional extra:
`pip install repomarshal[mcp]`. This is the ONLY module allowed to import the `mcp` SDK, and only
`repomarshal mcp` imports this module.

    main() -> int

Tools (all take an explicit `repo_path`; Roots is deprecated in MCP 2026-07-28):
- repomarshal_plan(repo_path, task?, base?)  readOnlyHint=true, openWorldHint=false
    -> structuredContent = model.to_dict(plan); outputSchema = schemas/plan.schema.json
- repomarshal_done(repo_path, task?, base?)  readOnlyHint=false (runs repository commands)
    -> {"verdict", "reasons", "evidence_path", "record"} (record per evidence.schema.json)
- repomarshal_explain(repo_path, gate_id, task?, base?)  readOnlyHint=true
    -> every reason for one gate decision in a freshly compiled plan
Without the extra installed, main() prints an install hint to stderr and returns 4.
"""

from __future__ import annotations


def main() -> int:
    raise NotImplementedError
