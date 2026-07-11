import classify_data_product as cdp


def test_format_result_includes_artefacts_and_next_agents():
    output = cdp._format_result(
        pattern="new_gold_mart_only",
        rationale="Platinum already covers this.",
        open_questions=[],
    )
    assert "Delivery pattern: new_gold_mart_only" in output
    assert "dbt Gold mart/view" in output
    assert "Gold Data Modelling Agent" in output
    assert "Open questions before proceeding:" not in output


def test_format_result_lists_open_questions_when_present():
    output = cdp._format_result(
        pattern="ad_hoc_extract",
        rationale="One-off analysis only.",
        open_questions=["Do we need this recurring?"],
    )
    assert "Open questions before proceeding:" in output
    assert "Do we need this recurring?" in output
