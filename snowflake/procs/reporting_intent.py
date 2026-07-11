"""Reporting Intent Agent as a Snowflake Cortex stored procedure.
Deployed via ../snowflake.yml, entity `capture_reporting_intent`.

Captures high-level reporting/analytics needs from an approved PRD, before any
Silver/Platinum/Gold modelling starts. It deliberately does NOT touch source
tables, column names, or SQL — that's the Ingestion/Platinum/Gold agents'
job. This agent only records what stakeholders need to see and answer.

`context_text` is a grounding seam: existing metrics/data-object definitions
(e.g. pulled from a Cortex Search service over a data-object metadata table)
so the model reuses known names instead of coining near-duplicates. Pass ""
until that retrieval step exists.

Only `_complete` (via cortex_common) touches the Snowpark session; everything
else is plain, unit-testable Python (see ../tests/test_reporting_intent.py).
"""

import cortex_common

MODEL_NAME = cortex_common.DEFAULT_MODEL_NAME

_REPORTING_INTENT_ROLE = """
You are the Reporting Intent Agent for an Enterprise Data Platform team.
Given an approved PRD or requirements text, capture the high-level reporting
and analytics needs it implies — BEFORE any data modelling happens.

Stay at the business/reporting level only:
- Do NOT name source tables, columns, or write any SQL.
- Do NOT decide dimensional modelling details (that's the Platinum/Gold
  agents' job downstream).
- DO capture who needs to see this, what questions they're trying to answer,
  what they need to measure, how they need to slice it, how fresh it needs to
  be, and where they expect to see it (e.g. a Power BI dashboard vs. an ad hoc
  extract).

If you are given company context (existing metrics, data objects, or
terminology), reuse the exact same names and definitions instead of coining
new ones, and flag any conflict between the PRD and existing definitions as
an open question rather than silently picking one.

If the source text doesn't give you enough to answer one of these confidently
(e.g. refresh frequency isn't mentioned), say so plainly as an open question
and pick the safer/more conservative default rather than guessing.
"""


def _complete(session, prompt):
    return cortex_common.complete(session, MODEL_NAME, prompt)


def _extract_json(text):
    return cortex_common.extract_json(text)


def _build_prompt(prd_text, context_text=""):
    parts = [_REPORTING_INTENT_ROLE]
    if context_text:
        parts.append(f"Company context (existing metrics/data objects/terms):\n{context_text}")
    parts.append(f"PRD / requirements:\n{prd_text}")
    parts.append(
        "Return ONLY a JSON object with keys: primary_audience (string), "
        "business_questions (list of strings), key_metrics (list of "
        "{name, definition}), dimensions (list of strings), grain (string), "
        "refresh_frequency (string), delivery_channel (string), constraints "
        "(list of strings), open_questions (list of strings — anything you "
        "couldn't determine from the PRD alone). No prose outside the JSON."
    )
    return "\n\n".join(parts)


def _format_result(intent):
    lines = ["Reporting Intent Brief", ""]

    lines.append(f"Primary audience: {intent.get('primary_audience') or '—'}")
    lines.append(f"Grain: {intent.get('grain') or '—'}")
    lines.append(f"Refresh frequency: {intent.get('refresh_frequency') or '—'}")
    lines.append(f"Delivery channel: {intent.get('delivery_channel') or '—'}")

    lines += ["", "Business questions:"]
    lines += [f"- {q}" for q in intent.get("business_questions") or []]

    lines += ["", "Key metrics:"]
    for m in intent.get("key_metrics") or []:
        lines.append(f"- {m.get('name', '')} — {m.get('definition', '')}")

    lines += ["", "Dimensions / breakdowns:"]
    lines += [f"- {d}" for d in intent.get("dimensions") or []]

    constraints = intent.get("constraints") or []
    if constraints:
        lines += ["", "Constraints:"]
        lines += [f"- {c}" for c in constraints]

    open_questions = intent.get("open_questions") or []
    if open_questions:
        lines += ["", "Open questions before modelling starts:"]
        lines += [f"- {q}" for q in open_questions]

    return "\n".join(lines)


def run(session, prd_text, context_text=""):
    prompt = _build_prompt(prd_text, context_text)
    intent = _extract_json(_complete(session, prompt))
    return _format_result(intent)


