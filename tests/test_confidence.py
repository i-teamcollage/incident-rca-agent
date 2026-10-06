import sample

from incident_rca_agent.confidence import decide, supporting_kinds
from incident_rca_agent.grounding import ClaimCheck
from incident_rca_agent.tools import Source

PLATFORM = sample.platform()
ALERTING = "mart_daily_revenue"


def source(source_id, kind, pipelines, abnormal=True, text=""):
    return Source(source_id, kind, text or source_id, frozenset(pipelines), abnormal)


def log(line_id, level, pipeline, message):
    text = f"[{line_id}] 2026-03-14 02:14:07 {level} {pipeline}: {message}"
    return source(line_id, "log", [pipeline], level in ("WARN", "ERROR"), text)


SOURCES = {
    s.id: s
    for s in [
        log(
            "L-0013",
            "ERROR",
            "src_payments_api",
            "giving up on page 14 after 5 attempts: HTTP 429 (hourly quota used up)",
        ),
        log("L-0014", "ERROR", "src_payments_api", "run #8812 failed after 546 s: 3140 of about 12900 records fetched"),
        log("L-0002", "INFO", "src_payments_api", "run #8811 started"),
        log("L-0008", "WARN", "fct_orders", "slow query on staging.orders: 14.2 s (threshold 10 s)"),
        log("L-0016", "WARN", "stg_payments", "upstream ing_payments has no new data since 01:16; skipping run #6610"),
        log(
            "L-0018",
            "WARN",
            "mart_daily_revenue",
            "upstream fct_orders has no new data since 01:36; skipping run #3302",
        ),
        log(
            "L-0030", "ERROR", "fct_orders", "unique test on order_id failed: 1240 duplicate keys read from stg_orders"
        ),
        source("M-1", "metric", ["src_payments_api"]),
        source("M-2", "metric", ["src_payments_api"], abnormal=False),  # no change point
        source("M-3", "metric", ["mart_daily_revenue"]),
        source("M-4", "metric", ["fct_orders"]),
        source("M-5", "metric", ["stg_payments"]),
        source("CHG-4410", "change", ["src_payments_api"]),
        source("CHG-4411", "change", ["mart_daily_revenue"]),
        source("RB-04.2", "runbook", []),
        source("LIN-1", "lineage", ["mart_daily_revenue", "src_payments_api"], abnormal=False),
        source("ALERT", "alert", ["mart_daily_revenue"]),
    ]
}


def claim(*ids, grounded=True):
    return ClaimCheck("a claim", list(ids), grounded, [] if grounded else ["'12' is not in the cited sources"])


GOOD = [claim("L-0013"), claim("M-1"), claim("LIN-1", "ALERT")]
NOT_UPSTREAM = "is not upstream of the alerting pipeline"


def verdict(claims=GOOD, category="api_rate_limit", pipeline="src_payments_api"):
    return decide(category, pipeline, claims, SOURCES, PLATFORM, ALERTING)


def kinds(category, pipeline, *ids):
    return supporting_kinds(category, pipeline, [claim(*ids)], SOURCES, PLATFORM)


def test_confident_when_every_condition_holds():
    result = verdict()
    assert result.confident
    assert result.reasons == []
    assert (result.grounded, result.claims) == (3, 3)
    assert result.supporting_kinds == ["log", "metric"]


def test_an_unknown_cause_is_never_confident():
    result = verdict(category="unknown")
    assert not result.confident
    assert result.reasons == ["the cause is unknown"]


def test_no_named_pipeline_is_never_confident():
    result = verdict(category="unknown", pipeline="unknown")
    assert result.reasons == ["the cause is unknown", "no root-cause pipeline was named"]


def test_fewer_than_four_claims_in_five_grounded_is_not_confident():
    three_of_four = [*GOOD, claim("L-0013", grounded=False)]
    assert verdict(three_of_four).reasons == ["only 3 of 4 claims are grounded (80% needed)"]
    four_of_five = [*GOOD, claim("CHG-4410"), claim("L-0013", grounded=False)]
    assert verdict(four_of_five).confident


def test_an_rca_without_claims_is_not_confident():
    assert verdict([]).reasons == [
        "the RCA makes no claims",
        "no cited warning or error from src_payments_api shows API rate limit (HTTP 429)",
        "no cited metric summary or change about src_payments_api backs the cause up",
    ]


def test_the_root_cause_must_be_upstream_of_the_alert_or_the_alerting_pipeline_itself():
    downstream = verdict(pipeline="exp_finance_ledger")
    assert f"exp_finance_ledger {NOT_UPSTREAM} mart_daily_revenue" in downstream.reasons
    alerting = decide("late_upstream_data", ALERTING, [claim("L-0018"), claim("ALERT")], SOURCES, PLATFORM, ALERTING)
    assert not any(NOT_UPSTREAM in reason for reason in alerting.reasons)


def test_a_pipeline_outside_the_lineage_graph_is_not_confident():
    assert "nightly_job is not a pipeline in the lineage graph" in verdict(pipeline="nightly_job").reasons


def test_a_log_line_alone_is_not_enough():
    result = verdict([claim("L-0013"), claim("L-0013")])
    assert result.reasons == ["no cited metric summary or change about src_payments_api backs the cause up"]


def test_a_log_line_and_a_change_are_enough():
    assert verdict([claim("L-0013"), claim("CHG-4410")]).confident


def test_a_metric_and_a_change_without_a_log_line_are_not_enough():
    # The red herring: a deploy on the alerting pipeline, whose row count dropped because it waits for upstream.
    result = decide(
        "bad_config_deploy", ALERTING, [claim("CHG-4411"), claim("M-3"), claim("L-0018")], SOURCES, PLATFORM, ALERTING
    )
    assert not result.confident
    assert result.supporting_kinds == ["change", "metric"]
    assert result.reasons == ["no cited warning or error from mart_daily_revenue shows bad config deploy"]


def test_the_log_line_must_show_the_named_failure_type():
    assert kinds("api_rate_limit", "src_payments_api", "L-0013", "M-1") == ["log", "metric"]
    assert kinds("dns_failure", "src_payments_api", "L-0013", "M-1") == ["metric"]
    assert kinds("api_rate_limit", "src_payments_api", "L-0014", "M-1") == ["metric"]  # failed, but no 429
    assert kinds("out_of_memory", "fct_orders", "L-0008", "M-4") == ["metric"]  # routine noise


def test_a_log_line_that_blames_an_upstream_pipeline_is_a_symptom():
    assert kinds("duplicate_keys", "fct_orders", "L-0030", "M-4") == ["metric"]
    assert kinds("late_upstream_data", "stg_payments", "L-0016", "M-5") == ["metric"]


def test_only_abnormal_sources_about_the_root_cause_pipeline_support_it():
    assert kinds("api_rate_limit", "src_payments_api", "L-0002", "M-2") == []
    assert kinds("api_rate_limit", "src_payments_api", "L-0018", "RB-04.2", "LIN-1") == []
    assert kinds("api_rate_limit", "src_payments_api", "CHG-4410", "L-0013") == ["change", "log"]


def test_ungrounded_claims_support_nothing():
    claims = [claim("L-0013"), claim("M-1", grounded=False), claim("CHG-4410", grounded=False)]
    assert supporting_kinds("api_rate_limit", "src_payments_api", claims, SOURCES, PLATFORM) == ["log"]


def test_reading_an_upstream_table_is_not_blaming_the_upstream_pipeline():
    message = "memory at 97% of the 8 GiB limit while joining analytics.fct_orders"
    line = log("L-0040", "ERROR", "mart_daily_revenue", message)
    sources = {**SOURCES, "L-0040": line}
    claims = [claim("L-0040"), claim("M-3")]
    assert supporting_kinds("out_of_memory", "mart_daily_revenue", claims, sources, PLATFORM) == ["log", "metric"]
