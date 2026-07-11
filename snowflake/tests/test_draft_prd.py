import draft_prd


def test_writer_prompt_first_pass_asks_for_full_json_shape():
    prompt = draft_prd._writer_prompt("Finance needs prior-period FX rates fixed.")
    assert "Source notes:" in prompt
    assert "Return ONLY a JSON object" in prompt
    assert "Your previous draft" not in prompt


def test_writer_prompt_revision_pass_includes_reviewer_feedback():
    prd = {"title": "FX Reporting"}
    review = {
        "missing_requirements": ["prior-period rates"],
        "issues": ["vague success metric"],
        "revision_notes": "Add a metric for FX variance.",
    }
    prompt = draft_prd._writer_prompt("notes", prd, review)
    assert "Your previous draft" in prompt
    assert "prior-period rates" in prompt
    assert "Add a metric for FX variance." in prompt


def test_render_markdown_includes_all_sections():
    prd = {
        "title": "FX Reporting Fix",
        "target_release": "Q3",
        "owner": "J. Lee",
        "stakeholders": ["Finance", "Treasury"],
        "objective": "Fix EOM FX variance.",
        "background": "Rates lag by one period.",
        "success_metrics": [{"goal": "Reduce errors", "metric": "FX variance < 0.1%"}],
        "assumptions": ["Source feed is daily"],
        "requirements": [
            {
                "user_story": "As a treasury analyst, I want corrected FX rates, so that reports are accurate.",
                "importance": "High",
                "notes": "",
                "jira_issue": "",
            }
        ],
        "open_questions": [{"question": "Which base currency?", "answer": ""}],
        "out_of_scope": ["Historical restatement"],
    }

    markdown = draft_prd._render_markdown(prd)

    assert "# FX Reporting Fix" in markdown
    assert "Finance, Treasury" in markdown
    assert "FX variance < 0.1%" in markdown
    assert "Which base currency?" in markdown
    assert "Historical restatement" in markdown
