from incident_rca_agent.grounding import check_claim, checkable
from incident_rca_agent.tools import Source


def source(source_id, kind, text, pipelines=(), abnormal=True):
    return Source(source_id, kind, text, frozenset(pipelines), abnormal)


SOURCES = {
    s.id: s
    for s in [
        source(
            "L-0011",
            "log",
            "[L-0011] 2026-03-14 02:05:44 WARN src_payments_api: GET https://api.payments.example/v2/payments?page=14 "
            "returned 429 Too Many Requests, Retry-After 120 s (attempt 1/5)",
            ["src_payments_api"],
        ),
        source(
            "L-0013",
            "log",
            "[L-0013] 2026-03-14 02:14:07 ERROR src_payments_api: giving up on page 14 after 5 attempts: HTTP 429 "
            "(hourly quota of 3600 requests used up)",
            ["src_payments_api"],
        ),
        source(
            "L-0014",
            "log",
            "[L-0014] 2026-03-14 02:14:08 ERROR src_payments_api: run #8812 failed after 546 s: 3140 of about 12900 "
            "records fetched",
            ["src_payments_api"],
        ),
        source(
            "M-1",
            "metric",
            "[M-1] rows_out of src_payments_api (rows written per run), 6 runs from 2026-03-14 01:05 to 06:05 UTC.\n"
            "01:05 12,388 (usual 12,000) | 02:05 3,140 (usual 12,020)\n"
            "Change point at 02:05: 3,140 against a usual 12,020 (-74%). Still off at the end of the window.",
            ["src_payments_api"],
        ),
        source(
            "CHG-4410",
            "change",
            "[CHG-4410] 2026-03-14 01:58 backfill src_payments_api by finance-data: backfill of 2025 payments "
            "started; it shares the api.payments.example quota",
            ["src_payments_api"],
        ),
    ]
}


def check(text, *ids):
    return check_claim(text, list(ids), SOURCES)


def test_a_claim_whose_names_and_numbers_are_in_its_source_is_grounded():
    result = check("src_payments_api gave up on page 14 after 5 attempts with HTTP 429 at 02:14", "L-0013")
    assert result.grounded
    assert result.problems == []


def test_a_claim_without_a_citation_is_not_grounded():
    result = check("src_payments_api gave up after 5 attempts")
    assert not result.grounded
    assert result.problems == ["cites no source"]


def test_a_citation_that_was_never_retrieved_is_not_grounded():
    result = check("src_payments_api gave up after 5 attempts", "L-0013", "L-0999")
    assert not result.grounded
    assert result.problems == ["cites L-0999, which this investigation never retrieved"]


def test_a_number_missing_from_the_cited_source_is_caught():
    result = check("src_payments_api gave up 12 times", "L-0013")
    assert not result.grounded
    assert result.problems == ["'12' is not in the cited sources"]


def test_a_pipeline_missing_from_the_cited_source_is_caught():
    result = check("stg_payments gave up after 5 attempts", "L-0013")
    assert result.problems == ["'stg_payments' is not in the cited sources"]


def test_names_and_numbers_may_come_from_any_of_the_cited_sources():
    claim = "After the backfill CHG-4410 started at 01:58, src_payments_api got HTTP 429 at 02:05:44"
    assert check(claim, "CHG-4410", "L-0011").grounded
    assert not check(claim, "CHG-4410").grounded


def test_thousands_separators_signs_and_percent_signs_do_not_matter():
    assert check("rows_out of src_payments_api fell to 3140 against a usual 12020, -74%", "M-1").grounded
    assert check("src_payments_api fetched only 3,140 of about 12,900 records", "L-0014").grounded


def test_times_may_drop_the_seconds_or_a_leading_zero():
    assert check("src_payments_api gave up at 02:14", "L-0013").grounded
    assert check("src_payments_api gave up at 2:14", "L-0013").grounded
    assert check("src_payments_api gave up at 2026-03-14T02:14:07Z", "L-0013").grounded


def test_host_names_are_checked_too():
    assert check("api.payments.example answered 429", "L-0011").grounded
    assert not check("api.payments.example answered 429", "L-0013").grounded


def test_units_glued_to_numbers_are_read():
    assert check("run #8812 failed after 546s", "L-0014").grounded


def test_plain_words_are_not_checked():
    assert check("The payments API refused the requests and the source pipeline stopped", "L-0013").grounded


def test_a_claim_may_name_the_id_it_cites():
    assert check("L-0013 shows HTTP 429 on src_payments_api", "L-0013").grounded
    assert not check("L-0011 shows HTTP 429 on src_payments_api", "L-0013").grounded


def test_cited_ids_are_read_leniently():
    assert check("HTTP 429 on src_payments_api", " [l-0013] ").grounded


def test_the_answer_vocabulary_is_not_a_fact_to_check():
    assert check("This is api_rate_limit, as search_logs showed for SRC_PAYMENTS_API", "L-0013").grounded


def test_checkable_keeps_identifiers_and_numbers_only():
    assert checkable("At 02:05 the `src_payments_api` job of api.payments.example failed 5 times, e.g. HTTP 429.") == [
        "02:05",
        "src_payments_api",
        "api.payments.example",
        "5",
        "429",
    ]


def test_a_bare_number_does_not_match_part_of_a_date():
    assert not check("src_payments_api failed on page 3", "L-0013").grounded  # 2026-03-14 holds 03, not page 3
