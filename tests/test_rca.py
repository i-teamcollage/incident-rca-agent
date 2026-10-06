import json

import pytest
import sample

from incident_rca_agent.categories import CATEGORY_IDS
from incident_rca_agent.investigate import Investigation, ToolUse
from incident_rca_agent.llm import LLM
from incident_rca_agent.rca import build_messages, call_text, rca_schema, write_rca
from incident_rca_agent.schema import validate
from incident_rca_agent.tools import Session

PIPELINES = sample.platform().names

ANSWER = {
    "root_cause_category": "api_rate_limit",
    "root_cause_pipeline": "src_payments_api",
    "summary": "The payments API answered HTTP 429, so src_payments_api stopped loading.",
    "claims": [{"text": "src_payments_api got HTTP 429 at 02:05", "source_ids": ["L-0011"]}],
    "affected_pipelines": ["src_payments_api", "mart_daily_revenue"],
    "actions": [{"text": "Pause the backfill", "source_ids": ["RB-04.2"]}],
}


def investigation() -> Investigation:
    session = Session(sample.incident(), sample.platform(), sample.knowledge())
    uses = []
    for name, arguments, by_model in [
        ("get_alert", {}, False),
        ("get_lineage", {"pipeline": "mart_daily_revenue", "direction": "upstream"}, True),
        ("search_logs", {"pipeline": "src_payments_api", "level": "WARN"}, True),
        ("search_runbooks", {"query": "HTTP 429"}, True),
    ]:
        result = session.run(name, arguments)
        uses.append(ToolUse(name, arguments, result.text, result.source_ids, by_model))
    return Investigation(uses=uses, seconds=12.0, model_calls=3, stopped="the model had enough evidence")


def test_schema_fixes_the_category_the_pipelines_and_the_citable_ids():
    schema = rca_schema(PIPELINES, ["ALERT", "L-0011", "RB-04.2"])
    assert validate(ANSWER, schema) is ANSWER
    assert schema["properties"]["root_cause_category"]["enum"] == list(CATEGORY_IDS)
    with pytest.raises(ValueError, match="root_cause_pipeline"):
        validate({**ANSWER, "root_cause_pipeline": "payments"}, schema)
    with pytest.raises(ValueError, match=r"claims\[0\]\.source_ids\[0\]"):
        validate({**ANSWER, "claims": [{"text": "x", "source_ids": ["L-0999"]}]}, schema)
    with pytest.raises(ValueError, match="root_cause_category"):
        validate({**ANSWER, "root_cause_category": "gremlins"}, schema)


def test_unknown_is_allowed_as_a_pipeline_best_guess():
    schema = rca_schema(PIPELINES, ["ALERT"])
    answer = {**ANSWER, "root_cause_category": "unknown", "root_cause_pipeline": "unknown", "claims": [], "actions": []}
    assert validate(answer, schema) is answer


def test_the_final_prompt_carries_every_result_with_its_ids_in_order():
    messages = build_messages(investigation())
    assert [message["role"] for message in messages] == ["system", "user"]
    system, user = messages[0]["content"], messages[1]["content"]
    for category in CATEGORY_IDS[:-1]:
        assert category in system
    assert user.index("1. get_alert()") < user.index('2. get_lineage(pipeline="mart_daily_revenue"')
    assert '3. search_logs(pipeline="src_payments_api", level="WARN")' in user
    assert "[L-0013] 2026-03-14 02:14:07 ERROR src_payments_api" in user
    assert "[RB-04.2]" in user


def test_the_transcript_shows_arguments_in_a_fixed_order_whatever_order_the_model_used():
    # A stored run keeps the model's arguments with sorted keys, so the order must not change the prompt.
    model_order = ToolUse("search_logs", {"pipeline": "src_payments_api", "level": "WARN", "query": "429"}, "", [])
    sorted_order = ToolUse("search_logs", {"level": "WARN", "pipeline": "src_payments_api", "query": "429"}, "", [])
    assert call_text(model_order) == call_text(sorted_order)
    assert call_text(model_order) == 'search_logs(pipeline="src_payments_api", query="429", level="WARN")'
    assert call_text(ToolUse("get_alert", {"extra": 1}, "", [])) == "get_alert(extra=1)"


def test_write_rca_is_one_structured_call_without_tools(tmp_path):
    requests = []

    def model(request):
        requests.append(request)
        return {"content": json.dumps(ANSWER), "tool_calls": [], "prompt_tokens": 3000, "output_tokens": 300}

    llm = LLM(mode="live", runs_root=tmp_path, num_ctx=16384, transport=model)
    found = investigation()
    reply = write_rca(llm, found, PIPELINES, label="sample")
    assert reply.data == ANSWER
    assert len(requests) == 1
    assert "tools" not in requests[0]
    assert requests[0]["format"]["properties"]["claims"]["items"]["properties"]["source_ids"]["items"]["enum"] == (
        found.source_ids()
    )
