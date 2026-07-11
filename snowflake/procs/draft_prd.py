"""Requirements Agent (Writer <-> Reviewer reflection loop) as a Snowflake
Cortex stored procedure. Deployed via ../snowflake.yml, entity `draft_prd`.

Only `_complete` (via cortex_common) touches the Snowpark session; everything
else is plain, unit-testable Python (see ../tests/test_draft_prd.py).
"""

import json

import cortex_common

MODEL_NAME = cortex_common.DEFAULT_MODEL_NAME
MAX_PASSES = 3


_PRD_SKILL = """
PRD sections
1. Overview - title, target release, owner, stakeholders, status.
2. Objective - 2-4 sentences: what we're building and why it matters.
3. Background / context - the problem, prior art, relevant systems.
4. Success metrics - each a Goal paired with a measurable Metric.
5. Assumptions - what must hold true for this to work.
6. Requirements - each a user story ("As a <role>, I want <capability>, so
   that <benefit>") with importance (High/Medium/Low) and notes. Link a Jira
   key if one exists.
7. User interaction & design - UX notes or links (optional).
8. Open questions - unresolved points, with answers when known.
9. Out of scope - what we are explicitly NOT doing (prevents scope creep).

Writing principles:
- Capture every requirement in the source. A throwaway line like "finance
  also needs the prior-period rates fixed" is a requirement - don't drop it.
- User stories, not feature lists. State the role and the benefit.
- Metrics must be measurable. "Works well" is not a metric.
- Surface ambiguity as an Open Question rather than inventing an answer.
- Prefer the source's own terms (system names, table names, acronyms).

Reviewer checklist (approve only if ALL hold):
1. Coverage - every distinct requirement implied by the source notes appears
   in the Requirements section. List any that are missing.
2. Testability - every success metric is measurable.
3. Clarity - user stories name a role and a benefit; no vague verbs.
4. No invention - nothing material asserted that the source didn't support
   (genuine gaps belong in Open Questions, not guessed answers).
5. Scope - anything the source excluded is reflected in Out of Scope.
"""

_WRITER_ROLE = """
You are the Writer half of a Requirements Agent. You turn raw notes into a
structured Product Requirements Document. On a revision pass you'll be given
your previous draft plus the Reviewer's feedback - address every point in
missing_requirements and revision_notes without dropping anything that was
already correct.
"""

_REVIEWER_ROLE = """
You are the Reviewer half of a Requirements Agent. Your only job is to check
the PRD draft against the source notes and the checklist below. The most
important check is coverage: re-read the source, enumerate every distinct
requirement, and verify each appears in the draft's Requirements section.
Approve only when nothing is missing and the quality checks pass - when in
doubt, do not approve. Critique the draft; do not rewrite it yourself.
"""


def _complete(session, prompt):
    return cortex_common.complete(session, MODEL_NAME, prompt)


def _extract_json(text):
    return cortex_common.extract_json(text)


def _writer_prompt(notes, prd=None, review=None):
    parts = [_WRITER_ROLE, _PRD_SKILL, f"Source notes:\n{notes}"]
    if review is None:
        parts.append(
            "Return ONLY a JSON object with keys: title, objective, "
            "target_release, owner, stakeholders (list of strings), "
            "background, success_metrics (list of {goal, metric}), "
            "assumptions (list of strings), requirements (list of "
            "{user_story, importance, notes, jira_issue}), "
            "user_interaction_design, open_questions (list of "
            "{question, answer}), out_of_scope (list of strings). "
            "No prose outside the JSON."
        )
    else:
        parts.append(f"Your previous draft:\n{json.dumps(prd)}")
        parts.append(f"Reviewer's missing_requirements: {review.get('missing_requirements')}")
        parts.append(f"Reviewer's issues: {review.get('issues')}")
        parts.append(f"Reviewer's revision_notes: {review.get('revision_notes')}")
        parts.append("Return the revised PRD as the same JSON shape, addressing every point above.")
    return "\n\n".join(parts)


def _reviewer_prompt(notes, prd):
    return "\n\n".join([
        _REVIEWER_ROLE,
        _PRD_SKILL,
        f"Source notes:\n{notes}",
        f"Current PRD draft:\n{json.dumps(prd)}",
        "Return ONLY a JSON object with keys: approved (boolean), "
        "missing_requirements (list of strings), issues (list of strings), "
        "revision_notes (string). No prose outside the JSON.",
    ])


def _render_markdown(prd):
    stakeholders = ", ".join(prd.get("stakeholders") or []) or "—"
    lines = [
        f"# {prd.get('title', 'Untitled')}",
        "",
        "| | |",
        "|---|---|",
        f"| **Target release** | {prd.get('target_release') or '—'} |",
        f"| **Document owner** | {prd.get('owner') or '—'} |",
        f"| **Stakeholders** | {stakeholders} |",
        "",
        "## Objective", "", prd.get("objective", ""), "",
        "## Background", "", prd.get("background", ""), "",
        "## Success metrics", "",
        "| Goal | Metric |",
        "|------|--------|",
    ]
    for m in prd.get("success_metrics") or []:
        lines.append(f"| {m.get('goal', '')} | {m.get('metric', '')} |")

    lines += ["", "## Assumptions", ""]
    for a in prd.get("assumptions") or []:
        lines.append(f"- {a}")

    lines += [
        "", "## Requirements", "",
        "| # | User story | Importance | Jira | Notes |",
        "|---|------------|------------|------|-------|",
    ]
    for i, r in enumerate(prd.get("requirements") or [], start=1):
        lines.append(
            f"| {i} | {r.get('user_story', '')} | {r.get('importance', 'Medium')} | "
            f"{r.get('jira_issue') or '—'} | {r.get('notes') or ''} |"
        )

    lines += ["", "## User interaction and design", "", prd.get("user_interaction_design") or "—", ""]
    lines += ["## Open questions", "", "| Question | Answer |", "|----------|--------|"]
    for q in prd.get("open_questions") or []:
        lines.append(f"| {q.get('question', '')} | {q.get('answer') or '—'} |")

    lines += ["", "## Out of scope", ""]
    for o in prd.get("out_of_scope") or []:
        lines.append(f"- {o}")

    return "\n".join(lines)


def run(session, notes):
    prd = None
    review = None

    for _ in range(MAX_PASSES):
        prompt = _writer_prompt(notes, prd, review)
        prd = _extract_json(_complete(session, prompt))

        review = _extract_json(_complete(session, _reviewer_prompt(notes, prd)))
        if review.get("approved"):
            break

    assert review is not None  # MAX_PASSES >= 1, so the loop always runs once

    markdown = _render_markdown(prd)

    if not review.get("approved"):
        markdown = (
            f"_Not fully approved after {MAX_PASSES} review passes. "
            f"Outstanding: {review.get('missing_requirements') or review.get('issues')}_\n\n"
            + markdown
        )

    return markdown
