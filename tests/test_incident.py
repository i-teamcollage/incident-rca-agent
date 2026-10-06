from datetime import UTC, date, datetime

import sample

from incident_rca_agent.incident import Incident, Knowledge, Platform, parse_time

PLATFORM = sample.platform()
INCIDENT = sample.incident()
KNOWLEDGE = sample.knowledge()


def test_upstream_lists_nearest_pipelines_first():
    assert PLATFORM.upstream("mart_daily_revenue") == [
        ("fct_orders", 1),
        ("stg_orders", 2),
        ("stg_payments", 2),
        ("ing_orders", 3),
        ("ing_payments", 3),
        ("src_shop_db", 4),
        ("src_payments_api", 4),
    ]


def test_downstream_follows_the_edges_the_other_way():
    assert PLATFORM.downstream("ing_payments") == [
        ("stg_payments", 1),
        ("fct_orders", 2),
        ("mart_daily_revenue", 3),
        ("exp_finance_ledger", 4),
    ]


def test_is_upstream_is_strict_and_directional():
    assert PLATFORM.is_upstream("src_payments_api", "mart_daily_revenue")
    assert not PLATFORM.is_upstream("mart_daily_revenue", "src_payments_api")
    assert not PLATFORM.is_upstream("fct_orders", "fct_orders")
    assert not PLATFORM.is_upstream("src_unknown", "fct_orders")


def test_inputs_and_layers_come_from_the_catalogue():
    assert PLATFORM.inputs("fct_orders") == ["stg_orders", "stg_payments"]
    assert PLATFORM.pipelines["src_payments_api"].host == "api.payments.example"
    assert "fct_orders" in PLATFORM and "fct_unknown" not in PLATFORM


def test_pipeline_names_are_found_in_text_as_whole_words():
    text = "stg_payments waits for ing_payments; not stg_payments_v2 or xing_orders"
    assert PLATFORM.mentioned(text) == {"stg_payments", "ing_payments"}


def test_parse_time_reads_the_usual_formats():
    day = date(2026, 3, 14)
    expected = datetime(2026, 3, 14, 2, 5, tzinfo=UTC)
    assert parse_time("2026-03-14T02:05:00Z") == expected
    assert parse_time("2026-03-14 02:05") == expected
    assert parse_time("2026-03-14T02:05") == expected
    assert parse_time("2026-03-14 02:05 UTC") == expected
    assert parse_time("02:05", day) == expected
    assert parse_time("2026-03-14") == datetime(2026, 3, 14, tzinfo=UTC)
    assert parse_time("yesterday", day) is None
    assert parse_time("02:05") is None


def test_log_lines_keep_their_ids_and_show_as_one_line():
    line = INCIDENT.logs[12]
    assert line.id == "L-0013"
    assert line.level == "ERROR"
    assert line.pipeline == "src_payments_api"
    assert line.text() == (
        "[L-0013] 2026-03-14 02:14:07 ERROR src_payments_api: giving up on page 14 after 5 attempts: "
        "HTTP 429 (hourly quota of 3600 requests used up)"
    )


def test_alert_text_names_the_pipeline_check_and_window():
    text = INCIDENT.alert.text()
    assert text.startswith("[ALERT] ALR-4471 fired 2026-03-14 06:50 UTC on mart_daily_revenue")
    assert "freshness" in text and "high" in text
    assert "2026-03-14 00:50 to 06:50 UTC" in text


def test_runs_know_their_times():
    runs = INCIDENT.runs["src_payments_api"]
    times = runs.times()
    assert len(times) == 78
    assert times[0] == datetime(2026, 3, 11, 1, 5, tzinfo=UTC)
    assert times[73] == datetime(2026, 3, 14, 2, 5, tzinfo=UTC)
    assert runs.values["rows_out"][73] == 3140
    assert runs.status[73] == "failed"


def test_change_text_has_id_time_target_and_author():
    change = INCIDENT.changes[1]
    assert change.text() == (
        "[CHG-4410] 2026-03-14 01:58 backfill src_payments_api by finance-data: "
        "backfill of 2025 payments started; it shares the api.payments.example quota"
    )


def test_incident_and_knowledge_load_from_files(tmp_path):
    folder, shared = sample.write(tmp_path)
    loaded = Incident.load(folder)
    assert loaded.name == "sample"
    assert loaded.alert == INCIDENT.alert
    assert loaded.logs == INCIDENT.logs
    assert loaded.changes == INCIDENT.changes
    assert loaded.runs["fct_orders"].values == INCIDENT.runs["fct_orders"].values
    assert Platform.load(shared / "lineage.json").names == PLATFORM.names
    assert [s.id for s in Knowledge.load(shared).sections] == ["RB-01.1", "RB-01.2", "RB-04.1", "RB-04.2", "RB-08.1"]


def test_runbook_search_finds_the_matching_section():
    sections = KNOWLEDGE.search_runbooks("HTTP 429 Too Many Requests")
    assert sections[0].id == "RB-04.1"
    assert len(sections) <= 3


def test_runbook_section_text_carries_its_id_and_title():
    section = KNOWLEDGE.search_runbooks("OOMKilled exit code 137")[0]
    assert section.text().startswith("[RB-08.1] Out of memory (exit 137): Symptoms. ")


def test_post_mortem_search_ranks_by_relevance():
    found = KNOWLEDGE.search_postmortems("backfill quota HTTP 429")
    assert found[0].id == "PM-2025-007"
    assert found[0].text().startswith("[PM-2025-007] 2025-06-12, api rate limit: CRM API quota used up by a backfill.")


def test_window_starts_where_the_export_starts():
    assert INCIDENT.window_start == datetime(2026, 3, 14, 0, 50, tzinfo=UTC)
    assert INCIDENT.window_end == datetime(2026, 3, 14, 6, 50, tzinfo=UTC)


def test_a_table_named_like_a_pipeline_is_not_a_mention_of_the_pipeline():
    assert PLATFORM.mentioned("joining analytics.fct_orders, then mart_daily_revenue") == {"mart_daily_revenue"}
    assert PLATFORM.mentioned("config error in fct_orders.yaml v18") == {"fct_orders"}
