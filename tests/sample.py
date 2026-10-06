"""A small hand-made platform and incident for the unit tests.

Nine pipelines, one incident: the payments API starts answering HTTP 429 at 02:05, and the alert
fires four steps downstream, on mart_daily_revenue's freshness.
"""

import json
from pathlib import Path

from incident_rca_agent.incident import Incident, Knowledge, Platform

LINEAGE = {
    "pipelines": [
        {
            "name": "src_shop_db",
            "layer": "source",
            "owner": "data-platform",
            "schedule": "hourly at :05",
            "reads": "shopdb-replica.internal",
            "writes": "landing.shop_orders",
            "host": "shopdb-replica.internal",
        },
        {
            "name": "src_payments_api",
            "layer": "source",
            "owner": "data-platform",
            "schedule": "hourly at :05",
            "reads": "api.payments.example",
            "writes": "landing.payments",
            "host": "api.payments.example",
        },
        {
            "name": "ing_orders",
            "layer": "ingestion",
            "owner": "data-platform",
            "schedule": "hourly at :15",
            "reads": "landing.shop_orders",
            "writes": "raw.orders",
        },
        {
            "name": "ing_payments",
            "layer": "ingestion",
            "owner": "data-platform",
            "schedule": "hourly at :15",
            "reads": "landing.payments",
            "writes": "raw.payments",
        },
        {
            "name": "stg_orders",
            "layer": "staging",
            "owner": "analytics-eng",
            "schedule": "hourly at :25",
            "reads": "raw.orders",
            "writes": "staging.orders",
        },
        {
            "name": "stg_payments",
            "layer": "staging",
            "owner": "analytics-eng",
            "schedule": "hourly at :25",
            "reads": "raw.payments",
            "writes": "staging.payments",
        },
        {
            "name": "fct_orders",
            "layer": "model",
            "owner": "analytics-eng",
            "schedule": "hourly at :35",
            "reads": "staging.orders, staging.payments",
            "writes": "analytics.fct_orders",
        },
        {
            "name": "mart_daily_revenue",
            "layer": "mart",
            "owner": "finance-data",
            "schedule": "hourly at :45",
            "reads": "analytics.fct_orders",
            "writes": "marts.daily_revenue",
        },
        {
            "name": "exp_finance_ledger",
            "layer": "export",
            "owner": "finance-data",
            "schedule": "hourly at :55",
            "reads": "marts.daily_revenue",
            "writes": "sftp.finance.example:/inbound/ledger",
            "host": "sftp.finance.example",
        },
    ],
    "edges": [
        ["src_shop_db", "ing_orders"],
        ["src_payments_api", "ing_payments"],
        ["ing_orders", "stg_orders"],
        ["ing_payments", "stg_payments"],
        ["stg_orders", "fct_orders"],
        ["stg_payments", "fct_orders"],
        ["fct_orders", "mart_daily_revenue"],
        ["mart_daily_revenue", "exp_finance_ledger"],
    ],
}

ALERT = {
    "id": "ALR-4471",
    "fired_at": "2026-03-14T06:50:00Z",
    "pipeline": "mart_daily_revenue",
    "check": "freshness",
    "severity": "high",
    "message": "mart_daily_revenue has not loaded new data since 2026-03-14 01:45 UTC (freshness SLA 4 h)",
    "window_start": "2026-03-14T00:50:00Z",
}

LOGS = """\
L-0001 2026-03-14T01:05:01Z INFO src_shop_db run #5120 started
L-0002 2026-03-14T01:05:02Z INFO src_payments_api run #8811 started
L-0003 2026-03-14T01:06:10Z INFO src_payments_api fetched 12388 records from api.payments.example in 25 pages
L-0004 2026-03-14T01:06:12Z INFO src_payments_api run #8811 finished in 70 s, 12388 rows written to landing.payments
L-0005 2026-03-14T01:06:40Z INFO src_shop_db run #5120 finished in 99 s, 30211 rows written to landing.shop_orders
L-0006 2026-03-14T01:15:02Z INFO ing_payments run #7710 started
L-0007 2026-03-14T01:16:30Z INFO ing_payments run #7710 finished in 88 s, 12388 rows written to raw.payments
L-0008 2026-03-14T01:35:04Z WARN fct_orders slow query on staging.orders: 14.2 s (threshold 10 s)
L-0009 2026-03-14T01:45:03Z INFO mart_daily_revenue run #3301 finished in 41 s, 288 rows written to marts.daily_revenue
L-0010 2026-03-14T02:05:02Z INFO src_payments_api run #8812 started
L-0011 2026-03-14T02:05:44Z WARN src_payments_api GET https://api.payments.example/v2/payments?page=14 returned \
429 Too Many Requests, Retry-After 120 s (attempt 1/5)
L-0012 2026-03-14T02:13:51Z WARN src_payments_api GET https://api.payments.example/v2/payments?page=14 returned \
429 Too Many Requests, Retry-After 120 s (attempt 5/5)
L-0013 2026-03-14T02:14:07Z ERROR src_payments_api giving up on page 14 after 5 attempts: HTTP 429 \
(hourly quota of 3600 requests used up)
L-0014 2026-03-14T02:14:08Z ERROR src_payments_api run #8812 failed after 546 s: 3140 of about 12900 records fetched
L-0015 2026-03-14T02:15:02Z WARN ing_payments no new landing files for raw.payments since 01:06; skipping run #7711
L-0016 2026-03-14T02:25:03Z WARN stg_payments upstream ing_payments has no new data since 01:16; skipping run #6610
L-0017 2026-03-14T02:35:02Z ERROR fct_orders dependency check failed: stg_payments last loaded new data at 01:26
L-0018 2026-03-14T02:45:02Z WARN mart_daily_revenue upstream fct_orders has no new data since 01:36; skipping run #3302
L-0019 2026-03-14T03:05:40Z ERROR src_payments_api giving up on page 1 after 5 attempts: HTTP 429 \
(hourly quota of 3600 requests used up)
L-0020 2026-03-14T03:35:02Z ERROR fct_orders dependency check failed: stg_payments last loaded new data at 01:26
L-0021 2026-03-14T04:12:30Z WARN src_shop_db retrying connection to shopdb-replica.internal after reset (attempt 1/5)
L-0022 2026-03-14T04:12:31Z INFO src_shop_db connection to shopdb-replica.internal re-established
L-0023 2026-03-14T05:05:41Z ERROR src_payments_api giving up on page 1 after 5 attempts: HTTP 429 \
(hourly quota of 3600 requests used up)
L-0024 2026-03-14T06:45:02Z WARN mart_daily_revenue upstream fct_orders has no new data since 01:36; skipping run #3307
"""

CHANGES = [
    {
        "id": "CHG-4402",
        "time": "2026-03-11T09:30:00Z",
        "kind": "deploy",
        "target": "stg_orders",
        "author": "analytics-eng",
        "summary": "v1.8.0 adds column coupon_code",
    },
    {
        "id": "CHG-4410",
        "time": "2026-03-14T01:58:00Z",
        "kind": "backfill",
        "target": "src_payments_api",
        "author": "finance-data",
        "summary": "backfill of 2025 payments started; it shares the api.payments.example quota",
    },
    {
        "id": "CHG-4411",
        "time": "2026-03-14T04:20:00Z",
        "kind": "config",
        "target": "mart_daily_revenue",
        "author": "finance-data",
        "summary": "config v18: dashboard refresh moved to :50",
    },
]

RUNBOOKS = [
    {
        "id": "RB-01",
        "title": "Triage a pipeline alert",
        "sections": [
            {
                "id": "RB-01.1",
                "title": "Walk the lineage upstream",
                "text": "Alerts usually fire on a downstream symptom. List the upstream pipelines and find the "
                "first one whose runs failed or wrote fewer rows.",
            },
            {
                "id": "RB-01.2",
                "title": "Find the first failure",
                "text": "Search the logs of that pipeline for warnings and errors around the time its runs changed.",
            },
        ],
    },
    {
        "id": "RB-04",
        "title": "API rate limits (HTTP 429)",
        "sections": [
            {
                "id": "RB-04.1",
                "title": "Symptoms",
                "text": "Source pipelines log HTTP 429 Too Many Requests with a Retry-After header, then give up.",
            },
            {
                "id": "RB-04.2",
                "title": "Fix",
                "text": "Pause backfills that share the API quota, then rerun the source pipeline and its "
                "downstream pipelines.",
            },
        ],
    },
    {
        "id": "RB-08",
        "title": "Out of memory (exit 137)",
        "sections": [
            {
                "id": "RB-08.1",
                "title": "Symptoms",
                "text": "The container is OOMKilled and the task fails with exit code 137.",
            },
        ],
    },
]

POSTMORTEMS = [
    {
        "id": "PM-2025-007",
        "date": "2025-06-12",
        "title": "CRM API quota used up by a backfill",
        "category": "api_rate_limit",
        "pipelines": ["src_crm_api", "mart_customer_ltv"],
        "summary": "mart_customer_ltv was stale for 6 hours.",
        "root_cause": "A backfill on src_crm_api used the hourly quota; the API answered HTTP 429.",
        "resolution": "Paused the backfill and reran src_crm_api.",
        "follow_ups": "Backfills now run at night with their own quota.",
    },
    {
        "id": "PM-2025-011",
        "date": "2025-08-02",
        "title": "fct_orders killed for memory",
        "category": "out_of_memory",
        "pipelines": ["fct_orders", "mart_daily_revenue"],
        "summary": "mart_daily_revenue missed its freshness SLA.",
        "root_cause": "fct_orders was OOMKilled (exit code 137) after input volume tripled.",
        "resolution": "Raised the memory limit and split the join.",
        "follow_ups": "Memory alert at 85% of the limit.",
    },
    {
        "id": "PM-2025-019",
        "date": "2025-11-20",
        "title": "Late shop data",
        "category": "late_upstream_data",
        "pipelines": ["src_shop_db", "mart_daily_revenue"],
        "summary": "mart_daily_revenue freshness alert.",
        "root_cause": "The shop database replica lagged by 3 hours.",
        "resolution": "Waited for the replica and reran.",
        "follow_ups": "Replica lag alert.",
    },
]

FIRST_RUNS = {
    "src_shop_db": "2026-03-11T01:05:00Z",
    "src_payments_api": "2026-03-11T01:05:00Z",
    "ing_payments": "2026-03-11T01:15:00Z",
    "fct_orders": "2026-03-11T01:35:00Z",
    "mart_daily_revenue": "2026-03-11T01:45:00Z",
}


def _series(baseline, window):
    return [baseline(i) for i in range(72)] + window


def metrics() -> dict:
    """72 baseline runs (three days) and 6 window runs per pipeline."""
    normal = {
        "duration": lambda i: 70 + i % 5,
        "rows_in": lambda i: 12000 + (i % 24) * 20,
        "rows_out": lambda i: 12000 + (i % 24) * 20,
        "errors": lambda i: 0,
        "disk": lambda i: 40 + i % 3,
        "memory": lambda i: 50 + i % 4,
    }
    pipelines = {}
    for name, first_run in FIRST_RUNS.items():
        series = {metric: _series(make, [make(72 + j) for j in range(6)]) for metric, make in normal.items()}
        pipelines[name] = {"first_run": first_run, "status": ["ok"] * 78, **series}
    payments = pipelines["src_payments_api"]
    payments["rows_out"][72:] = [12388, 3140, 0, 0, 0, 0]
    payments["rows_in"][72:] = [12388, 3140, 0, 0, 0, 0]
    payments["errors"][72:] = [0, 6, 5, 5, 5, 5]
    payments["duration"][72:] = [70, 546, 610, 600, 605, 600]
    payments["status"][72:] = ["ok"] + ["failed"] * 5
    mart = pipelines["mart_daily_revenue"]
    mart["rows_out"][72:] = [288, 0, 0, 0, 0, 0]
    mart["status"][72:] = ["ok"] + ["skipped"] * 5
    return {"step_minutes": 60, "pipelines": pipelines}


def platform() -> Platform:
    return Platform.from_data(LINEAGE)


def incident() -> Incident:
    return Incident.from_data("sample", ALERT, LOGS, metrics(), CHANGES)


def knowledge() -> Knowledge:
    return Knowledge.from_data(RUNBOOKS, POSTMORTEMS)


def write(root: Path) -> tuple[Path, Path]:
    """Write the sample as files: (incident folder, shared folder)."""
    folder, shared = root / "sample", root / "shared"
    folder.mkdir(parents=True)
    shared.mkdir(parents=True)
    (folder / "alert.json").write_text(json.dumps(ALERT), encoding="utf-8")
    (folder / "logs.txt").write_text(LOGS, encoding="utf-8")
    (folder / "metrics.json").write_text(json.dumps(metrics()), encoding="utf-8")
    (folder / "changes.json").write_text(json.dumps(CHANGES), encoding="utf-8")
    (shared / "lineage.json").write_text(json.dumps(LINEAGE), encoding="utf-8")
    (shared / "runbooks.json").write_text(json.dumps(RUNBOOKS), encoding="utf-8")
    (shared / "postmortems.json").write_text(json.dumps(POSTMORTEMS), encoding="utf-8")
    return folder, shared
