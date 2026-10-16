import copy

import sample

from incident_rca_agent.investigate import MAX_TOOL_CALLS, investigate
from incident_rca_agent.llm import LLM
from incident_rca_agent.tools import TOOLS, Session

LINEAGE_CALL = ("get_lineage", {"pipeline": "mart_daily_revenue", "direction": "upstream"})
LOGS_CALL = ("search_logs", {"pipeline": "src_payments_api", "level": "WARN"})
METRICS_CALL = ("get_metric_summary", {"pipeline": "src_payments_api", "metric": "all"})


class ScriptedModel:
    """Answers each tool step from a script: a list of (tool name, arguments) per reply."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request):
        self.requests.append(copy.deepcopy(request))  # the loop keeps appending to the same message list
        calls = self.replies.pop(0) if self.replies else []
        return {
            "content": "" if calls else "I have what I need.",
            "tool_calls": [{"name": name, "arguments": arguments} for name, arguments in calls],
            "prompt_tokens": 2000,
            "output_tokens": 40,
        }


def run(tmp_path, *replies):
    model = ScriptedModel(*replies)
    llm = LLM(mode="live", runs_root=tmp_path, num_ctx=16384, transport=model)
    session = Session(sample.incident(), sample.platform(), sample.knowledge())
    return investigate(llm, session, label="sample"), model, session


def test_the_code_reads_the_alert_first_and_offers_the_tools(tmp_path):
    investigation, model, _ = run(tmp_path)
    first = model.requests[0]
    assert first["tools"] == TOOLS
    assert "format" not in first
    assert "[ALERT] ALR-4471" in first["messages"][-1]["content"]
    assert [use.name for use in investigation.uses] == ["get_alert"]
    assert not investigation.uses[0].by_model


def test_tool_results_go_back_to_the_model_as_tool_messages(tmp_path):
    lineage = ("get_lineage", {"pipeline": "mart_daily_revenue", "direction": "upstream"})
    investigation, model, _ = run(tmp_path, [lineage])
    second = model.requests[1]["messages"]
    assert second[-2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "get_lineage", "arguments": lineage[1]}}],
    }
    assert second[-1]["role"] == "tool" and second[-1]["tool_name"] == "get_lineage"
    assert second[-1]["content"].startswith("[LIN-1] Lineage of mart_daily_revenue")
    assert investigation.uses[1].source_ids == ["LIN-1"]


def test_several_calls_in_one_reply_get_one_result_each_in_order(tmp_path):
    calls = [("list_changes", {}), ("search_runbooks", {"query": "HTTP 429"})]
    _, model, _ = run(tmp_path, calls)
    messages = model.requests[1]["messages"]
    assert [m.get("tool_name") for m in messages[-2:]] == ["list_changes", "search_runbooks"]


def test_the_loop_ends_when_the_model_stops_asking(tmp_path):
    investigation, model, _ = run(tmp_path, [LOGS_CALL, METRICS_CALL])
    assert len(model.requests) == 2
    assert investigation.stopped == "the model had enough evidence"
    assert investigation.model_calls == 2


def test_at_most_eight_tool_calls_including_the_alert(tmp_path):
    greedy = [[("search_logs", {"query": f"error {n}"})] for n in range(20)]
    investigation, model, _ = run(tmp_path, *greedy)
    assert MAX_TOOL_CALLS == 8
    assert len(investigation.uses) == 8
    assert len(model.requests) == 7
    assert investigation.stopped == "the limit of 8 tool calls was reached"


def test_calls_beyond_the_limit_are_not_run(tmp_path):
    first = [("list_changes", {})] * 6
    second = [("search_runbooks", {"query": "429"}), ("search_past_incidents", {"query": "429"})]
    investigation, _, session = run(tmp_path, first, second)
    assert len(investigation.uses) == 8
    assert investigation.skipped == 1
    assert [use.name for use in investigation.uses][-1] == "search_runbooks"
    assert not any(source.kind == "past_incident" for source in session.sources.values())


def test_time_and_model_calls_are_added_up(tmp_path):
    investigation, _, _ = run(tmp_path, [("list_changes", {})], [("search_runbooks", {"query": "429"})])
    assert investigation.model_calls == 4  # the third reply stops without logs or metrics and gets the reminder
    assert investigation.seconds >= 0
    assert investigation.source_ids()[:2] == ["ALERT", "CHG-4402"]


def test_the_model_is_asked_for_tool_calls_without_text(tmp_path):
    # On a CPU without a graphics card the model writes about three tokens a second, and a plan before every call
    # tripled the time of an investigation. Nothing reads that text: the RCA is written from the tool results alone.
    _, model, _ = run(tmp_path)
    system = model.requests[0]["messages"][0]["content"]
    assert "Answer with tool calls only" in system
    assert "reply with the single word DONE" in system
    assert "DONE is plain text, not a tool" in system


def test_the_model_is_told_to_follow_the_skipped_runs_to_the_failing_pipeline(tmp_path):
    # Walking down the lineage one pipeline at a time used up the calls before the root pipeline's metrics.
    _, model, _ = run(tmp_path)
    system = model.requests[0]["messages"][0]["content"]
    assert "follow the skipped runs upstream" in system
    assert "Go to the suspect directly" in system
    assert "level WARN" in system



def test_a_model_that_stops_without_logs_or_metrics_is_reminded_once(tmp_path):
    # The confidence line needs a log line and a metric summary or a change; a model that stops after the lineage
    # gets one reminder, then the loop goes on as before.
    investigation, model, _ = run(tmp_path, [LINEAGE_CALL], [], [LOGS_CALL, METRICS_CALL], [])
    reminder = model.requests[2]["messages"][-1]
    assert reminder["role"] == "user"
    assert "6 tool calls left" in reminder["content"]
    assert "search_logs" in reminder["content"] and "get_metric_summary" in reminder["content"]
    assert model.requests[2]["messages"][-2] == {"role": "assistant", "content": "I have what I need."}
    assert [use.name for use in investigation.uses] == ["get_alert", "get_lineage", "search_logs", "get_metric_summary"]
    assert investigation.stopped == "the model had enough evidence"
    assert investigation.model_calls == 4


def test_the_reminder_comes_only_once(tmp_path):
    investigation, model, _ = run(tmp_path, [LINEAGE_CALL], [], [])
    assert len(model.requests) == 3
    assert [use.name for use in investigation.uses] == ["get_alert", "get_lineage"]
    assert investigation.stopped == "the model had enough evidence"


def test_a_model_with_logs_and_metrics_is_not_reminded(tmp_path):
    investigation, model, _ = run(tmp_path, [LINEAGE_CALL, LOGS_CALL, METRICS_CALL], [])
    assert len(model.requests) == 2
    assert all(message["role"] != "user" for message in model.requests[-1]["messages"][2:])


def test_a_model_low_on_calls_without_a_metric_summary_is_reminded_of_what_is_missing(tmp_path):
    # It read the logs of every pipeline on the way up and never asked for the failing pipeline's metrics.
    logs = [[("search_logs", {"pipeline": "src_payments_api", "query": f"error {n}"})] for n in range(4)]
    investigation, model, _ = run(tmp_path, [LINEAGE_CALL], *logs, [METRICS_CALL], [])
    reminder = model.requests[5]["messages"][-1]
    assert reminder["role"] == "user"
    assert reminder["content"].startswith("You have 2 tool calls left.")
    assert "get_metric_summary" in reminder["content"] and "search_logs" not in reminder["content"]
    assert all(message["role"] != "user" for message in model.requests[4]["messages"][2:])
    assert [use.name for use in investigation.uses][-1] == "get_metric_summary"
    assert investigation.model_calls == 7
    assert investigation.stopped == "the model had enough evidence"


def test_metrics_of_one_pipeline_and_logs_of_another_get_a_reminder_for_the_last_one(tmp_path):
    # It read the warnings of the pipelines on the way up, then only the metrics of the failing one, and stopped.
    logs_below = ("search_logs", {"pipeline": "stg_payments", "level": "WARN"})
    investigation, model, _ = run(tmp_path, [LINEAGE_CALL], [logs_below], [METRICS_CALL], [], [LOGS_CALL], [])
    reminder = model.requests[4]["messages"][-1]
    assert reminder["role"] == "user"
    assert "use search_logs with level WARN on src_payments_api" in reminder["content"]
    assert "get_metric_summary" not in reminder["content"]
    assert [use.name for use in investigation.uses][-1] == "search_logs"
    assert investigation.uses[-1].arguments["pipeline"] == "src_payments_api"


def test_the_reminder_names_the_pipeline_the_model_looked_at_last(tmp_path):
    logs = [[("search_logs", {"pipeline": "src_payments_api", "query": f"error {n}"})] for n in range(4)]
    _, model, _ = run(tmp_path, [LINEAGE_CALL], *logs, [METRICS_CALL], [])
    assert "get_metric_summary with metric all on src_payments_api" in model.requests[5]["messages"][-1]["content"]


def test_each_pipeline_gets_its_own_reminder(tmp_path):
    # It read the logs of each pipeline on the way up; the first reminder named the last of them, the model then
    # asked for the metrics of the failing pipeline but had not read its logs.
    walk = [
        [("search_logs", {"pipeline": name, "level": "WARN"})]
        for name in ("stg_payments", "ing_payments", "stg_orders", "ing_orders")
    ]
    investigation, model, _ = run(tmp_path, [LINEAGE_CALL], *walk, [METRICS_CALL], [LOGS_CALL])
    first, second = model.requests[5]["messages"][-1], model.requests[6]["messages"][-1]
    assert "get_metric_summary with metric all on ing_orders" in first["content"]
    assert second["content"].startswith("You have 1 tool call left.")
    assert "search_logs with level WARN on src_payments_api" in second["content"]
    last = investigation.uses[-1]
    assert (last.name, last.arguments["pipeline"]) == ("search_logs", "src_payments_api")
    assert investigation.stopped == "the limit of 8 tool calls was reached"
