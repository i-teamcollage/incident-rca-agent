import json

import sample
from test_pipeline import GOOD, STEPS, analyse_with

from incident_rca_agent.slack import block_kit, markdown


def confident(tmp_path):
    return analyse_with(tmp_path, STEPS, GOOD)[0]


def unsure(tmp_path):
    return analyse_with(tmp_path, STEPS, {**GOOD, "root_cause_pipeline": "exp_finance_ledger"})[0]


def texts(message):
    found = []
    for block in message["blocks"]:
        if "text" in block:
            found.append(block["text"]["text"])
        for field in block.get("fields", []) + block.get("elements", []):
            found.append(field["text"])
    return found


def test_a_confident_rca_leads_with_the_root_cause(tmp_path):
    message = block_kit(confident(tmp_path))
    assert message["blocks"][0] == {
        "type": "header",
        "text": {"type": "plain_text", "text": "Root cause: API rate limit (HTTP 429) in src_payments_api"},
    }
    assert message["text"].startswith("Root cause: API rate limit (HTTP 429) in src_payments_api")
    joined = "\n".join(texts(message))
    assert "`L-0013`" in joined and "`M-1`" in joined
    assert "*Next steps*" in joined
    assert message["blocks"][-1]["type"] == "context"
    assert "Confident: 4 of 4 claims grounded" in joined


def test_an_unsure_rca_says_it_needs_an_engineer_and_why(tmp_path):
    message = block_kit(unsure(tmp_path))
    header = message["blocks"][0]["text"]["text"]
    assert header == "Needs an engineer: freshness alert on mart_daily_revenue"
    joined = "\n".join(texts(message))
    assert "*Best guess, not confirmed*" in joined
    assert "exp_finance_ledger is not upstream of the alerting pipeline mart_daily_revenue" in joined
    assert "*What was found*" in joined


def test_slack_control_characters_are_escaped(tmp_path):
    result = confident(tmp_path)
    result.summary = "<!channel> rows & errors > 0"
    joined = "\n".join(texts(block_kit(result)))
    assert "&lt;!channel&gt; rows &amp; errors &gt; 0" in joined
    assert "<!channel>" not in joined


def test_blocks_stay_within_slack_limits(tmp_path):
    result = confident(tmp_path)
    for claim in result.claims:
        claim.text = "very long claim " * 300
    message = block_kit(result)
    assert len(message["blocks"]) <= 50
    assert len(message["blocks"][0]["text"]["text"]) <= 150
    for block in message["blocks"]:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000
    json.dumps(message)


def test_markdown_preview_shows_the_same_content(tmp_path):
    preview = markdown(confident(tmp_path))
    assert preview.startswith("## Root cause: API rate limit (HTTP 429) in src_payments_api")
    assert "`L-0013`" in preview
    assert "Confident: 4 of 4 claims grounded" in preview
    assert sample.ALERT["id"] in preview
