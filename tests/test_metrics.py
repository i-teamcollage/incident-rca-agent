import math

import sample

from incident_rca_agent.confidence import Verdict
from incident_rca_agent.grounding import ClaimCheck
from incident_rca_agent.investigate import Investigation, ToolUse
from incident_rca_agent.metrics import rca_correct, score
from incident_rca_agent.pipeline import IncidentResult

ALERT = sample.incident().alert


def label(incident_id, category="api_rate_limit", pipeline="src_payments_api", red_herring=None):
    return {
        "id": incident_id,
        "category": category,
        "root_cause_pipeline": pipeline,
        "alert_pipeline": "mart_daily_revenue",
        "red_herring": red_herring,
    }


def result(incident_id, category="api_rate_limit", pipeline="src_payments_api", confident=True, grounded=(True,)):
    claims = [ClaimCheck("a claim", ["L-0013"], flag, [] if flag else ["x"]) for flag in grounded]
    uses = [ToolUse("get_alert", {}, "", ["ALERT"], by_model=False)] + [ToolUse("list_changes", {}, "", [])] * 5
    verdict = Verdict(confident, [] if confident else ["why"], sum(grounded), len(grounded), ["log", "metric"])
    return IncidentResult(
        incident_id=incident_id,
        alert=ALERT,
        category=category,
        pipeline=pipeline,
        summary="",
        claims=claims,
        affected=[pipeline],
        actions=[],
        verdict=verdict,
        investigation=Investigation(uses=uses, seconds=90.0, model_calls=6),
        seconds=90.0,
    )


def test_correct_means_right_cause_and_right_pipeline():
    assert rca_correct(label("a"), result("a"))
    assert not rca_correct(label("a"), result("a", pipeline="ing_payments"))
    assert not rca_correct(label("a"), result("a", category="dns_failure"))


def test_a_perfect_run_scores_full_marks():
    measures, _ = score([label("a"), label("b")], {"a": result("a"), "b": result("b")})
    assert measures["rca_accuracy"].value == 1.0
    assert measures["confident_precision"].value == 1.0
    assert measures["grounding"].value == 1.0


def test_a_needs_an_engineer_result_counts_with_its_best_guess():
    labels = [label("a"), label("b")]
    results = {"a": result("a", confident=False), "b": result("b", pipeline="fct_orders", confident=False)}
    measures, _ = score(labels, results)
    assert (measures["rca_accuracy"].hits, measures["rca_accuracy"].total) == (1, 2)
    assert measures["confident_precision"].total == 0
    assert math.isnan(measures["confident_precision"].value)


def test_confident_precision_only_looks_at_confident_rcas():
    labels = [label("a"), label("b"), label("c")]
    results = {
        "a": result("a"),
        "b": result("b", category="disk_full"),
        "c": result("c", category="disk_full", confident=False),
    }
    measures, _ = score(labels, results)
    assert (measures["confident_precision"].hits, measures["confident_precision"].total) == (1, 2)


def test_grounding_counts_every_claim_of_every_rca():
    results = {"a": result("a", grounded=(True, True, False)), "b": result("b", grounded=(True,))}
    measures, _ = score([label("a"), label("b")], results)
    assert (measures["grounding"].hits, measures["grounding"].total) == (3, 4)


def test_details_report_the_informational_figures_and_each_incident():
    labels = [label("a"), label("b", red_herring="deploy_on_alert_pipeline")]
    results = {"a": result("a"), "b": result("b", category="bad_config_deploy", confident=False)}
    _, details = score(labels, results)
    assert "Posted as confident: 1 of 2 (50.0%)" in details
    assert "Tool calls per incident: median 6" in details
    assert "Minutes per incident: median 1.5" in details
    assert "| b | API rate limit (HTTP 429) in src_payments_api | bad config deploy in src_payments_api | wrong |" in (
        details
    )
    assert "deploy on alert pipeline" in details
