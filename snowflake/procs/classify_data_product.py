"""Data Product Classifier Agent as a Snowflake Cortex stored procedure.
Deployed via ../snowflake.yml, entity `classify_data_product`.

Only `_complete` (via cortex_common) touches the Snowpark session; everything
else is plain, unit-testable Python (see ../tests/test_classify_data_product.py).
"""

import cortex_common

MODEL_NAME = cortex_common.DEFAULT_MODEL_NAME

_CLASSIFIER_ROLE = """
You are the Data Product Classifier for an Enterprise Data Platform team.
Given a PRD or a set of requirements, decide which delivery pattern this
request needs, so the right downstream work gets planned - not more, not less.

Delivery patterns (pick exactly one):
- new_platinum_model: no reusable enterprise dimension/fact covers this data
  today. Needs new source ingestion, a Silver staging model, and a new
  Platinum (enterprise, reusable) model, on top of whatever Gold/reporting
  work follows.
- extend_platinum_model: an existing Platinum model is the right home for
  this, but it's missing attributes/columns/history this request needs. Needs
  Silver + Platinum changes and regression testing, but not a new model from
  scratch.
- new_gold_mart_only: the Platinum layer already has everything this needs; it
  just needs a new Gold-layer mart/view (consumption-specific) and reporting
  on top.
- reporting_only: the Gold layer already exposes everything needed; this is
  purely a new Power BI measure, page, or interaction - no dbt work at all.
- ad_hoc_extract: a one-off analysis or extract that doesn't warrant a
  productionised pipeline - a single SQL script and a spike/task ticket, not
  an epic.

If the PRD doesn't give you enough to tell (e.g. you don't know whether an
existing Platinum model already covers this), say so plainly in
`open_questions` and pick the safer default rather than guessing confidently.
"""

_ARTEFACTS_BY_PATTERN = {
    "new_platinum_model": [
        "Source-to-target mapping",
        "dbt Silver staging model",
        "dbt Platinum model (dimension/fact)",
        "dbt Gold mart/view",
        "Regression + data-quality tests",
        "Power BI report/page changes",
    ],
    "extend_platinum_model": [
        "Silver staging model changes",
        "Platinum model changes (new attributes/history)",
        "Regression tests on the changed model",
        "Downstream Gold/reporting changes, if the new attributes are user-facing",
    ],
    "new_gold_mart_only": [
        "dbt Gold mart/view",
        "Power BI report/page changes",
        "Tests on the new Gold mart",
    ],
    "reporting_only": [
        "Power BI measure/page/interaction changes",
    ],
    "ad_hoc_extract": [
        "One-off SQL script",
        "Spike/task ticket (not an epic)",
    ],
}

_NEXT_AGENTS_BY_PATTERN = {
    "new_platinum_model": [
        "Ingestion & Integration Design Agent",
        "Platinum Data Modelling Agent",
        "Gold Data Modelling Agent",
        "Reporting Design Agent",
        "JIRA Delivery Agent",
        "Testing & Regression Agent",
    ],
    "extend_platinum_model": [
        "Platinum Data Modelling Agent",
        "Testing & Regression Agent",
        "JIRA Delivery Agent",
    ],
    "new_gold_mart_only": [
        "Gold Data Modelling Agent",
        "Reporting Design Agent",
        "JIRA Delivery Agent",
    ],
    "reporting_only": [
        "Reporting Design Agent",
        "JIRA Delivery Agent",
    ],
    "ad_hoc_extract": [
        "JIRA Delivery Agent",
    ],
}


def _complete(session, prompt):
    return cortex_common.complete(session, MODEL_NAME, prompt)


def _extract_json(text):
    return cortex_common.extract_json(text)


def _format_result(pattern, rationale, open_questions):
    lines = [
        f"Delivery pattern: {pattern}",
        "",
        "Rationale:",
        rationale,
        "",
        "Required artefacts:",
    ]
    lines += [f"- {a}" for a in _ARTEFACTS_BY_PATTERN.get(pattern, [])]

    lines += ["", "Suggested next agents:"]
    lines += [f"- {a}" for a in _NEXT_AGENTS_BY_PATTERN.get(pattern, [])]

    if open_questions:
        lines += ["", "Open questions before proceeding:"]
        lines += [f"- {q}" for q in open_questions]

    return "\n".join(lines)


def run(session, prd_text):
    prompt = "\n\n".join([
        _CLASSIFIER_ROLE,
        f"PRD / requirements:\n{prd_text}",
        "Return ONLY a JSON object with keys: delivery_pattern (one of "
        "new_platinum_model, extend_platinum_model, new_gold_mart_only, "
        "reporting_only, ad_hoc_extract), rationale (string, 2-4 sentences), "
        "open_questions (list of strings - anything you couldn't determine "
        "from the PRD alone). No prose outside the JSON.",
    ])

    result = _extract_json(_complete(session, prompt))
    pattern = result.get("delivery_pattern", "ad_hoc_extract")

    return _format_result(
        pattern,
        result.get("rationale", ""),
        result.get("open_questions") or [],
    )
