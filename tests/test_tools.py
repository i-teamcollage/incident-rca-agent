import json

import sample

from incident_rca_agent.tools import MAX_LOG_LINES, TOOLS, Session, change_point


def session() -> Session:
    return Session(sample.incident(), sample.platform(), sample.knowledge())


def ids(text: str) -> list[str]:
    return [line.split("]")[0][1:] for line in text.splitlines() if line.startswith("[")]


# get_alert


def test_get_alert_returns_the_alert_and_makes_it_citable():
    s = session()
    result = s.run("get_alert", {})
    assert result.text.startswith("[ALERT] ALR-4471")
    assert result.source_ids == ["ALERT"]
    assert s.sources["ALERT"].kind == "alert"
    assert s.sources["ALERT"].pipelines == frozenset({"mart_daily_revenue"})


# get_lineage


def test_lineage_upstream_lists_inputs_and_run_status():
    s = session()
    result = s.run("get_lineage", {"pipeline": "mart_daily_revenue", "direction": "upstream"})
    lines = result.text.splitlines()
    assert lines[0].startswith("[LIN-1] Lineage of mart_daily_revenue (mart, owner finance-data, hourly at :45)")
    assert "- mart_daily_revenue itself: reads fct_orders; runs: 1 ok, 5 skipped" in lines
    assert "- fct_orders (model), 1 step up, reads stg_orders, stg_payments; runs: 6 ok" in lines
    assert "- src_payments_api (source), 4 steps up, calls api.payments.example; runs: 1 ok, 5 failed" in lines
    assert "- stg_orders (staging), 2 steps up, reads ing_orders; no run data in this incident" in lines
    assert result.source_ids == ["LIN-1"]
    assert s.sources["LIN-1"].kind == "lineage"


def test_lineage_downstream_and_numbering_of_repeated_calls():
    s = session()
    s.run("get_lineage", {"pipeline": "fct_orders"})
    result = s.run("get_lineage", {"pipeline": "ing_payments", "direction": "down"})
    assert result.text.startswith("[LIN-2] Lineage of ing_payments (ingestion")
    assert "downstream" in result.text.splitlines()[0]
    assert "- exp_finance_ledger (export), 4 steps down, reads mart_daily_revenue; no run data in this incident" in (
        result.text
    )


def test_lineage_of_an_unknown_pipeline_lists_the_known_ones():
    s = session()
    result = s.run("get_lineage", {"pipeline": "orders_mart"})
    assert "Unknown pipeline 'orders_mart'" in result.text
    assert "mart_daily_revenue" in result.text
    assert result.source_ids == []
    assert s.sources == {}


def test_pipeline_names_are_forgiving_about_case_and_spaces():
    result = session().run("get_lineage", {"pipeline": " Fct_Orders "})
    assert result.text.startswith("[LIN-1] Lineage of fct_orders")


# search_logs


def test_search_logs_without_a_query_puts_the_most_severe_lines_first():
    s = session()
    result = s.run("search_logs", {"pipeline": "src_payments_api", "level": "WARN"})
    # Repeated messages are cut to two examples: L-0023 repeats L-0013 and L-0019.
    assert ids(result.text) == ["L-0011", "L-0012", "L-0013", "L-0014", "L-0019"]
    assert "5 of 6 matching lines shown" in result.text
    assert s.sources["L-0013"].kind == "log"
    assert s.sources["L-0013"].abnormal
    assert s.sources["L-0013"].pipelines == frozenset({"src_payments_api"})


def test_search_logs_level_is_a_minimum_and_case_does_not_matter():
    result = session().run("search_logs", {"pipeline": "src_payments_api", "level": "error"})
    assert ids(result.text) == ["L-0013", "L-0014", "L-0019"]


def test_search_logs_query_ranks_by_relevance_across_pipelines():
    result = session().run("search_logs", {"query": "HTTP 429 Too Many Requests"})
    found = ids(result.text)
    assert "L-0011" in found and "L-0013" in found
    assert "L-0008" not in found


def test_search_logs_time_window():
    result = session().run(
        "search_logs", {"pipeline": "src_payments_api", "since": "2026-03-14 02:10", "until": "02:20"}
    )
    assert ids(result.text) == ["L-0012", "L-0013", "L-0014"]


def test_a_bare_time_later_than_the_alert_means_the_day_before():
    result = session().run("search_logs", {"since": "23:00", "until": "01:10"})
    assert "2026-03-13 23:00 to 2026-03-14 01:10 UTC" in result.text
    assert ids(result.text) == ["L-0001", "L-0002", "L-0003", "L-0004", "L-0005"]


def test_search_logs_without_matches_says_so_and_registers_nothing():
    s = session()
    result = s.run("search_logs", {"pipeline": "src_payments_api", "query": "OOMKilled"})
    assert result.text.startswith("No log lines")
    assert result.source_ids == [] and s.sources == {}


def test_search_logs_shows_at_most_the_limit():
    result = session().run("search_logs", {"level": "INFO"})
    assert len(ids(result.text)) == MAX_LOG_LINES
    assert f"{MAX_LOG_LINES} of 24 matching lines shown" in result.text


def test_search_logs_reads_back_unknown_levels_and_times_honestly():
    result = session().run("search_logs", {"pipeline": "src_payments_api", "level": "loud", "since": "soon"})
    assert "level 'loud' is not known" in result.text
    assert "could not read since 'soon'" in result.text


# get_metric_summary


def test_change_point_is_the_first_run_off_its_usual_level():
    assert change_point("rows_out", [100, 98, 20, 0], [100, 100, 100, 100]) == 2
    assert change_point("rows_out", [100, 98, 101], [100, 100, 100]) is None
    assert change_point("errors", [0, 2, 3], [0, 0, 0]) == 2
    assert change_point("disk", [70, 84, 96], [60, 60, 90]) == 1


def test_metric_summary_compares_each_run_with_the_same_hour_before():
    s = session()
    result = s.run("get_metric_summary", {"pipeline": "src_payments_api", "metric": "rows_out"})
    text = result.text
    assert text.startswith("[M-1] rows_out of src_payments_api (rows written per run)")
    assert "02:05 3,140 (usual 12,020)" in text
    assert "Change point at 02:05: 3,140 against a usual 12,020 (-74%)." in text
    assert "Still off at the end of the window." in text
    assert s.sources["M-1"].kind == "metric" and s.sources["M-1"].abnormal


def test_metric_summary_without_a_change_point():
    s = session()
    result = s.run("get_metric_summary", {"pipeline": "src_shop_db", "metric": "rows_out"})
    assert "No change point" in result.text
    assert not s.sources["M-1"].abnormal


def test_all_metrics_in_one_summary():
    result = session().run("get_metric_summary", {"pipeline": "src_payments_api"})
    text = result.text
    assert text.startswith("[M-1] All metrics of src_payments_api")
    assert "- errors (errors per run): 0 | 6 | 5 | 5 | 5 | 5" in text
    assert "change point at 02:05: 6 against a usual 0" in text
    assert "- disk (disk used, % of the volume)" in text and "no change point" in text
    assert "- runs: ok | failed | failed | failed | failed | failed" in text


def test_metric_names_have_a_few_aliases_and_unknown_ones_are_listed():
    s = session()
    assert s.run("get_metric_summary", {"pipeline": "src_payments_api", "metric": "row_count"}).text.startswith(
        "[M-1] rows_out of src_payments_api"
    )
    unknown = s.run("get_metric_summary", {"pipeline": "src_payments_api", "metric": "happiness"})
    assert "Unknown metric 'happiness'" in unknown.text and "rows_out" in unknown.text
    assert unknown.source_ids == []


def test_metric_summary_for_a_pipeline_without_data():
    result = session().run("get_metric_summary", {"pipeline": "stg_orders", "metric": "rows_out"})
    assert result.text == "No metrics for stg_orders in this incident's data."
    assert result.source_ids == []


def test_metric_summary_window_can_be_narrowed():
    result = session().run(
        "get_metric_summary", {"pipeline": "src_payments_api", "metric": "errors", "since": "03:00", "until": "04:30"}
    )
    assert "03:05 5 (usual 0) | 04:05 5 (usual 0)" in result.text
    assert "Change point at 03:05" in result.text


# list_changes


def test_list_changes_defaults_to_the_week_before_the_alert():
    s = session()
    result = s.run("list_changes", {})
    assert ids(result.text) == ["CHG-4402", "CHG-4410", "CHG-4411"]
    assert s.sources["CHG-4410"].pipelines == frozenset({"src_payments_api"})
    assert s.sources["CHG-4410"].kind == "change"


def test_list_changes_in_a_window():
    result = session().run("list_changes", {"since": "2026-03-14 00:00", "until": "2026-03-14 03:00"})
    assert ids(result.text) == ["CHG-4410"]


def test_list_changes_with_nothing_in_the_window():
    result = session().run("list_changes", {"since": "2026-03-01", "until": "2026-03-02"})
    assert result.text.startswith("No changes between 2026-03-01 00:00 and 2026-03-02 00:00")


# search_runbooks and search_past_incidents


def test_search_runbooks_returns_sections_with_ids():
    s = session()
    result = s.run("search_runbooks", {"query": "HTTP 429 rate limit"})
    assert ids(result.text)[0] == "RB-04.1"
    assert s.sources["RB-04.1"].kind == "runbook"


def test_search_past_incidents_returns_post_mortems_with_ids():
    s = session()
    result = s.run("search_past_incidents", {"query": "quota used up by a backfill"})
    assert ids(result.text)[0] == "PM-2025-007"
    assert s.sources["PM-2025-007"].kind == "past_incident"


def test_searches_need_a_query():
    assert session().run("search_runbooks", {}).text == "Give a query, for example the error message you found."


# the dispatcher


def test_unknown_tool_is_answered_not_raised():
    result = session().run("restart_pipeline", {"pipeline": "fct_orders"})
    assert result.text.startswith("Unknown tool 'restart_pipeline'")


def test_arguments_given_as_a_json_string_are_read():
    result = session().run("get_lineage", json.dumps({"pipeline": "fct_orders"}))
    assert result.text.startswith("[LIN-1] Lineage of fct_orders")


def test_the_same_call_gives_the_same_text():
    first, second = session(), session()
    arguments = {"pipeline": "src_payments_api", "level": "WARN"}
    assert first.run("search_logs", arguments).text == second.run("search_logs", arguments).text


def test_every_tool_is_described_in_the_function_format():
    names = [tool["function"]["name"] for tool in TOOLS]
    assert names == [
        "get_alert",
        "get_lineage",
        "search_logs",
        "get_metric_summary",
        "list_changes",
        "search_runbooks",
        "search_past_incidents",
    ]
    for tool in TOOLS:
        assert tool["type"] == "function"
        assert tool["function"]["parameters"]["type"] == "object"
