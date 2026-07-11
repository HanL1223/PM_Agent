import reporting_intent as ri


def test_build_prompt_without_context_omits_context_section():
    prompt = ri._build_prompt("Some PRD text.")
    assert "PRD / requirements:\nSome PRD text." in prompt
    assert "Company context" not in prompt


def test_build_prompt_with_context_includes_it():
    prompt = ri._build_prompt("Some PRD text.", "On-time delivery rate is already defined as X.")
    assert "Company context (existing metrics/data objects/terms):" in prompt
    assert "On-time delivery rate is already defined as X." in prompt


def test_format_result_includes_core_fields():
    intent = {
        "primary_audience": "Supply Chain Ops Managers",
        "grain": "Per shipment, daily",
        "refresh_frequency": "Daily batch",
        "delivery_channel": "Power BI dashboard",
        "business_questions": ["Which lanes are consistently late?"],
        "key_metrics": [{"name": "On-time delivery rate", "definition": "% shipments delivered by SLA date"}],
        "dimensions": ["Region", "Carrier"],
        "constraints": [],
        "open_questions": [],
    }

    output = ri._format_result(intent)

    assert "Primary audience: Supply Chain Ops Managers" in output
    assert "Which lanes are consistently late?" in output
    assert "On-time delivery rate — % shipments delivered by SLA date" in output
    assert "Region" in output
    assert "Constraints:" not in output
    assert "Open questions before modelling starts:" not in output


def test_format_result_lists_constraints_and_open_questions_when_present():
    intent = {
        "constraints": ["Must respect existing row-level security by region"],
        "open_questions": ["Is 'on time' measured against promised or requested date?"],
    }

    output = ri._format_result(intent)

    assert "Constraints:" in output
    assert "Must respect existing row-level security by region" in output
    assert "Open questions before modelling starts:" in output
    assert "Is 'on time' measured against promised or requested date?" in output
