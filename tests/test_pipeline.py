import json

import sample

from incident_rca_agent.llm import LLM
from incident_rca_agent.pipeline import analyse, as_record

STEPS = [
    [("get_lineage", {"pipeline": "mart_daily_revenue", "direction": "upstream"})],
    [("search_logs", {"pipeline": "src_payments_api", "level": "WARN"})],
    [("get_metric_summary", {"pipeline": "src_payments_api", "metric": "rows_out"}), ("list_changes", {})],
]
GOOD = {
    "claims": [
        {
            "text": "src_payments_api gave up on page 14 after 5 attempts: HTTP 429 at 02:14:07",
            "source_ids": ["L-0013"],
        },
        {"text": "rows_out of src_payments_api fell to 3,140 at 02:05 (-74%)", "source_ids": ["M-1"]},
        {"text": "The 2025 payments backfill on src_payments_api started at 01:58", "source_ids": ["CHG-4410"]},
        {"text": "mart_daily_revenue reads fct_orders, which is fed by src_payments_api", "source_ids": ["LIN-1"]},
    ],
    "root_cause_category": "api_rate_limit",
    "root_cause_pipeline": "src_payments_api",
    "summary": "The payments API rate-limited src_payments_api after a backfill started.",
    "affected_pipelines": ["src_payments_api", "ing_payments", "stg_payments", "fct_orders", "mart_daily_revenue"],
    "actions": [{"text": "Pause the backfill that shares the quota", "source_ids": ["CHG-4410"]}],
}


class Model:
    """Tool steps from a script, then the RCA answer(s) for the structured call."""

    def __init__(self, steps, *answers):
        self.steps, self.answers = list(steps), list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if "format" in request:
            answer = self.answers.pop(0)
            content = answer if isinstance(answer, str) else json.dumps(answer)
            return {"content": content, "tool_calls": [], "prompt_tokens": 4000, "output_tokens": 400}
        calls = self.steps.pop(0) if self.steps else []
        tool_calls = [{"name": name, "arguments": arguments} for name, arguments in calls]
        return {"content": "", "tool_calls": tool_calls, "prompt_tokens": 2000, "output_tokens": 30}


def analyse_with(tmp_path, steps, *answers):
    model = Model(steps, *answers)
    llm = LLM(mode="live", runs_root=tmp_path, num_ctx=16384, transport=model)
    return analyse("sample", sample.incident(), llm, sample.platform(), sample.knowledge()), model


def test_a_well_evidenced_rca_is_posted_as_confident(tmp_path):
    result, model = analyse_with(tmp_path, STEPS, GOOD)
    assert result.status == "confident"
    assert (result.category, result.pipeline) == ("api_rate_limit", "src_payments_api")
    assert all(claim.grounded for claim in result.claims)
    assert result.verdict.supporting_kinds == ["change", "log", "metric"]
    assert result.tool_calls == 5
    assert "tools" not in model.requests[-1] and "format" in model.requests[-1]


def test_wrong_numbers_make_claims_ungrounded_and_the_rca_needs_an_engineer(tmp_path):
    sloppy = {**GOOD, "claims": [{**claim, "text": claim["text"] + " (12 times)"} for claim in GOOD["claims"]]}
    result, _ = analyse_with(tmp_path, STEPS, sloppy)
    assert result.status == "needs an engineer"
    assert result.verdict.grounded == 0
    assert "only 0 of 4 claims are grounded (80% needed)" in result.verdict.reasons


def test_a_root_cause_downstream_of_the_alert_needs_an_engineer(tmp_path):
    result, _ = analyse_with(tmp_path, STEPS, {**GOOD, "root_cause_pipeline": "exp_finance_ledger"})
    assert result.status == "needs an engineer"
    assert any("not upstream of the alerting pipeline" in reason for reason in result.verdict.reasons)


def test_an_unreadable_answer_needs_an_engineer_and_is_counted_as_unknown(tmp_path):
    result, _ = analyse_with(tmp_path, STEPS, "not json", '{"claims": []}')
    assert result.status == "needs an engineer"
    assert (result.category, result.pipeline) == ("unknown", "unknown")
    assert result.error
    assert result.verdict.reasons == ["the model's RCA could not be read, even after a retry"]


def test_the_record_holds_everything_the_slack_message_needs(tmp_path):
    result, _ = analyse_with(tmp_path, STEPS, GOOD)
    record = as_record(result)
    assert record["incident"] == "sample"
    assert record["status"] == "confident"
    assert record["alert"]["pipeline"] == "mart_daily_revenue"
    assert record["claims"][0] == {
        "text": GOOD["claims"][0]["text"],
        "source_ids": ["L-0013"],
        "grounded": True,
        "problems": [],
    }
    assert record["confidence"]["supporting_kinds"] == ["change", "log", "metric"]
    assert [call["tool"] for call in record["investigation"]["tool_calls"]][:2] == ["get_alert", "get_lineage"]
    json.dumps(record)
