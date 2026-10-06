import pytest

from incident_rca_agent.categories import CATEGORIES, CATEGORY_IDS, SIGNATURES, category_name, shows

EXAMPLES = {
    "upstream_schema_change": "data contract violation on landing.payments: required field 'amount' is missing",
    "api_rate_limit": "GET https://api.crm.example/v2/contacts?page=3 returned 429 Too Many Requests (attempt 1/5)",
    "disk_full": "write to /mnt/work/ing_events/tmp/part-0004.parquet failed: OSError: [Errno 28] No space left",
    "expired_credentials": "GET https://api.payments.example/v2/payments returned 401 Unauthorized: token expired",
    "late_upstream_data": "no file shipments_20260314_03.csv in /inbound/shipments yet; nothing to load",
    "out_of_memory": "container fct_orders-8812 was OOMKilled (exit code 137)",
    "bad_config_deploy": "config error in fct_orders.yaml v18: target_schema = 'analytics_v2': schema does not exist",
    "duplicate_keys": "consumer group cdc-orders restarted from offset 48211907: re-reading 41200 change events",
    "daylight_saving_bug": "NonExistentTimeError: 2026-03-29 02:00:00 does not exist in Europe/Berlin",
    "dns_failure": "could not resolve api.ads.example: Temporary failure in name resolution",
}

NOT_A_FAILURE = [
    "deprecated setting 'legacy_timestamps' is still set; it will be removed in v3",
    "slow query on staging.orders: 14.2 s (threshold 10 s)",
    "1240 late-arriving rows older than 48 h merged into raw.orders",
    "schema cache refreshed (212 tables)",
    "upstream ing_payments has no new data since 01:16; skipping run #6610",
    "HTTP 503 Service Unavailable from api.crm.example; the run failed and will be retried next hour",
    "row count 61840 is 2.1x the usual for this hour; continuing",
]


def test_every_failure_type_has_a_name_a_description_and_a_signature():
    assert set(SIGNATURES) == set(CATEGORIES) == set(CATEGORY_IDS[:-1])
    assert CATEGORY_IDS[-1] == "unknown"
    assert category_name("api_rate_limit") == "API rate limit (HTTP 429)"
    assert category_name("unknown") == "unknown cause"


@pytest.mark.parametrize("category", list(EXAMPLES))
def test_each_signature_recognises_its_own_error_and_no_other(category):
    assert shows(category, EXAMPLES[category])
    others = [other for other in EXAMPLES if other != category and shows(other, EXAMPLES[category])]
    assert others == []


def test_signatures_ignore_case():
    assert shows("out_of_memory", "Container was oomkilled")


@pytest.mark.parametrize("message", NOT_A_FAILURE)
def test_routine_warnings_and_symptoms_show_no_failure_type(message):
    assert [category for category in CATEGORIES if shows(category, message)] == []


def test_unknown_shows_nothing():
    assert not shows("unknown", EXAMPLES["dns_failure"])
