"""Diagram Brief Agent as a Snowflake Cortex stored procedure.
Deployed via ../snowflake.yml, entity `draft_diagram_brief`.

This proc does NOT talk to Lucid. It only turns an approved PRD (and,
optionally, its Reporting Intent brief) into a plain-language diagram
description — the hand-off artifact a Cortex Agent passes as the
`description` argument to Lucid's own MCP `create_diagram` tool once a human
has confirmed it. Keeping the Lucid call out of this proc is deliberate: Cortex
here has no outbound network access of its own (see ../../CLAUDE.md), so the
actual external call has to happen through the Agent's native MCP connector,
not through Python `requests` in a stored proc. See
../LUCID_DIAGRAM_AGENT.md for how this proc and the Lucid MCP server are
wired together on an Agent.

Only `_complete` (via cortex_common) touches the Snowpark session; everything
else is plain, unit-testable Python (see ../tests/test_draft_diagram_brief.py).
"""

import cortex_common

MODEL_NAME = cortex_common.DEFAULT_MODEL_NAME

_DIAGRAM_BRIEF_ROLE = """
You are the Diagram Brief Agent. Given an approved PRD (and, if provided, its
Reporting Intent brief), decide what single diagram would best communicate
the design, and describe it precisely enough that a diagramming tool could
draw it without seeing the source documents.

Pick exactly one diagram_type:
- "flowchart" - a process or user flow.
- "entity_relationship" - data entities and how they relate.
- "architecture" - systems/services and how they connect.
- "sequence" - a time-ordered interaction between actors/systems.

Rules:
- Every node must come from something explicitly stated in the source text -
  do not invent systems, steps, or entities that aren't there.
- Every edge needs a label if the relationship isn't obvious from the node
  names alone (e.g. "writes to", "triggers", "1-to-many").
- The `description` field is the most important output: a few plain-English
  sentences naming every node and how they connect, written so someone with
  no other context could hand it to a diagramming tool and get the right
  picture. Do not reference "the PRD" or "the source" inside it.
"""


def _complete(session, prompt):
    return cortex_common.complete(session, MODEL_NAME, prompt)


def _extract_json(text):
    return cortex_common.extract_json(text)


def _build_prompt(prd_text, reporting_intent_text=""):
    parts = [_DIAGRAM_BRIEF_ROLE, f"PRD:\n{prd_text}"]
    if reporting_intent_text:
        parts.append(f"Reporting Intent brief:\n{reporting_intent_text}")
    parts.append(
        "Return ONLY a JSON object with keys: diagram_type (one of "
        "flowchart, entity_relationship, architecture, sequence), title, "
        "nodes (list of {id, label}), edges (list of {from, to, label}), "
        "description (string). No prose outside the JSON."
    )
    return "\n\n".join(parts)


def _render_brief(brief):
    lines = [
        f"Diagram type: {brief.get('diagram_type', '')}",
        f"Title: {brief.get('title', '')}",
        "",
        brief.get("description", ""),
        "",
        "Nodes:",
    ]
    for n in brief.get("nodes") or []:
        lines.append(f"- {n.get('id', '')}: {n.get('label', '')}")

    lines += ["", "Edges:"]
    for e in brief.get("edges") or []:
        label = f" ({e.get('label')})" if e.get("label") else ""
        lines.append(f"- {e.get('from', '')} -> {e.get('to', '')}{label}")

    return "\n".join(lines)


def run(session, prd_text, reporting_intent_text=""):
    prompt = _build_prompt(prd_text, reporting_intent_text)
    brief = _extract_json(_complete(session, prompt))
    return _render_brief(brief)
