import draft_diagram_brief as ddb


def test_build_prompt_without_reporting_intent_omits_section():
    prompt = ddb._build_prompt("Some PRD text.")
    assert "PRD:\nSome PRD text." in prompt
    assert "Reporting Intent brief:\n" not in prompt


def test_build_prompt_with_reporting_intent_includes_it():
    prompt = ddb._build_prompt("Some PRD text.", "Primary audience: Ops Managers")
    assert "Reporting Intent brief:\nPrimary audience: Ops Managers" in prompt


def test_render_brief_includes_core_fields():
    brief = {
        "diagram_type": "flowchart",
        "title": "Checkout Flow",
        "description": "The customer submits an order, which triggers payment capture.",
        "nodes": [
            {"id": "customer", "label": "Customer"},
            {"id": "payment", "label": "Payment Service"},
        ],
        "edges": [
            {"from": "customer", "to": "payment", "label": "submits order"},
        ],
    }

    output = ddb._render_brief(brief)

    assert "Diagram type: flowchart" in output
    assert "Title: Checkout Flow" in output
    assert "triggers payment capture" in output
    assert "- customer: Customer" in output
    assert "- customer -> payment (submits order)" in output


def test_render_brief_handles_missing_optional_fields():
    output = ddb._render_brief({"diagram_type": "architecture", "title": "System Map"})
    assert "Nodes:" in output
    assert "Edges:" in output
