"""Word lists for the simulated data platform. Every system, host and team here is fictional.

Hosts use the reserved .example and .internal names, so none of them can point at a real service.
"""

# The 30 pipelines: name, layer, owner, minute past the hour it runs, what it reads, what it writes,
# external host (sources and exports), rows per run at an average hour, seconds per run, memory limit in GiB.
PIPELINES = [
    ("src_shop_db", "source", "data-platform", 5, "shopdb-replica.internal", "landing.shop_orders",
     "shopdb-replica.internal", 30000, 95, 2),
    ("src_payments_api", "source", "data-platform", 5, "api.payments.example", "landing.payments",
     "api.payments.example", 12000, 70, 2),
    ("src_crm_api", "source", "data-platform", 5, "api.crm.example", "landing.crm_contacts",
     "api.crm.example", 4000, 60, 2),
    ("src_ads_api", "source", "marketing-data", 5, "api.ads.example", "landing.ad_spend",
     "api.ads.example", 2500, 50, 2),
    ("src_partner_sftp", "source", "data-platform", 5, "sftp.logistics-partner.example", "landing.shipments",
     "sftp.logistics-partner.example", 6000, 40, 2),
    ("src_events_stream", "source", "data-platform", 5, "broker-1.stream.internal", "landing.events",
     "broker-1.stream.internal", 400000, 120, 4),
    ("ing_orders", "ingestion", "data-platform", 15, "landing.shop_orders", "raw.orders", "", 30000, 80, 4),
    ("ing_payments", "ingestion", "data-platform", 15, "landing.payments", "raw.payments", "", 12000, 60, 4),
    ("ing_crm_contacts", "ingestion", "data-platform", 15, "landing.crm_contacts", "raw.crm_contacts", "",
     4000, 45, 4),
    ("ing_ad_spend", "ingestion", "marketing-data", 15, "landing.ad_spend", "raw.ad_spend", "", 2500, 40, 4),
    ("ing_shipments", "ingestion", "data-platform", 15, "landing.shipments", "raw.shipments", "", 6000, 50, 4),
    ("ing_events", "ingestion", "data-platform", 15, "landing.events", "raw.events", "", 400000, 150, 8),
    ("stg_orders", "staging", "analytics-eng", 25, "raw.orders", "staging.orders", "", 29500, 70, 8),
    ("stg_payments", "staging", "analytics-eng", 25, "raw.payments", "staging.payments", "", 11800, 55, 8),
    ("stg_customers", "staging", "analytics-eng", 25, "raw.crm_contacts, raw.orders", "staging.customers", "",
     9000, 65, 8),
    ("stg_ad_spend", "staging", "marketing-data", 25, "raw.ad_spend", "staging.ad_spend", "", 2400, 35, 8),
    ("stg_shipments", "staging", "analytics-eng", 25, "raw.shipments", "staging.shipments", "", 5900, 45, 8),
    ("stg_sessions", "staging", "marketing-data", 25, "raw.events", "staging.sessions", "", 52000, 160, 8),
    ("dim_customers", "model", "analytics-eng", 32, "staging.customers", "analytics.dim_customers", "",
     8800, 60, 16),
    ("fct_orders", "model", "analytics-eng", 35, "staging.orders, staging.payments", "analytics.fct_orders", "",
     29000, 140, 16),
    ("fct_shipments", "model", "analytics-eng", 40, "staging.shipments, analytics.fct_orders",
     "analytics.fct_shipments", "", 5800, 75, 16),
    ("fct_marketing_spend", "model", "marketing-data", 35, "staging.ad_spend", "analytics.fct_marketing_spend", "",
     2300, 40, 16),
    ("fct_sessions", "model", "marketing-data", 35, "staging.sessions", "analytics.fct_sessions", "",
     51000, 180, 16),
    ("mart_daily_revenue", "mart", "finance-data", 45, "analytics.fct_orders, analytics.dim_customers",
     "marts.daily_revenue", "", 320, 45, 8),
    ("mart_customer_ltv", "mart", "analytics-eng", 45, "analytics.fct_orders, analytics.dim_customers",
     "marts.customer_ltv", "", 8700, 95, 8),
    ("mart_delivery_sla", "mart", "analytics-eng", 45, "analytics.fct_shipments", "marts.delivery_sla", "",
     450, 35, 8),
    ("mart_marketing_roi", "mart", "marketing-data", 45,
     "analytics.fct_marketing_spend, analytics.fct_sessions, analytics.fct_orders", "marts.marketing_roi", "",
     610, 50, 8),
    ("exp_finance_ledger", "export", "finance-data", 55, "marts.daily_revenue", "sftp.finance.example:/inbound",
     "sftp.finance.example", 320, 30, 2),
    ("exp_crm_segments", "export", "marketing-data", 55, "marts.customer_ltv", "api.crm.example /v3/segments",
     "api.crm.example", 8700, 65, 2),
    ("exp_bi_dashboards", "export", "analytics-eng", 55, "marts.daily_revenue, marts.delivery_sla, "
     "marts.marketing_roi", "bi.internal extracts", "bi.internal", 1380, 40, 2),
]  # fmt: skip

EDGES = [
    ("src_shop_db", "ing_orders"),
    ("src_payments_api", "ing_payments"),
    ("src_crm_api", "ing_crm_contacts"),
    ("src_ads_api", "ing_ad_spend"),
    ("src_partner_sftp", "ing_shipments"),
    ("src_events_stream", "ing_events"),
    ("ing_orders", "stg_orders"),
    ("ing_orders", "stg_customers"),
    ("ing_payments", "stg_payments"),
    ("ing_crm_contacts", "stg_customers"),
    ("ing_ad_spend", "stg_ad_spend"),
    ("ing_shipments", "stg_shipments"),
    ("ing_events", "stg_sessions"),
    ("stg_customers", "dim_customers"),
    ("stg_orders", "fct_orders"),
    ("stg_payments", "fct_orders"),
    ("stg_shipments", "fct_shipments"),
    ("fct_orders", "fct_shipments"),
    ("stg_ad_spend", "fct_marketing_spend"),
    ("stg_sessions", "fct_sessions"),
    ("fct_orders", "mart_daily_revenue"),
    ("dim_customers", "mart_daily_revenue"),
    ("fct_orders", "mart_customer_ltv"),
    ("dim_customers", "mart_customer_ltv"),
    ("fct_shipments", "mart_delivery_sla"),
    ("fct_marketing_spend", "mart_marketing_roi"),
    ("fct_sessions", "mart_marketing_roi"),
    ("fct_orders", "mart_marketing_roi"),
    ("mart_daily_revenue", "exp_finance_ledger"),
    ("mart_customer_ltv", "exp_crm_segments"),
    ("mart_daily_revenue", "exp_bi_dashboards"),
    ("mart_delivery_sla", "exp_bi_dashboards"),
    ("mart_marketing_roi", "exp_bi_dashboards"),
]

# Share of an average hour's rows that arrive in each hour of the day (UTC); evenings are busiest.
HOURLY = [
    0.55, 0.45, 0.40, 0.38, 0.40, 0.50, 0.65, 0.80, 0.92, 1.00, 1.05, 1.08,
    1.10, 1.10, 1.08, 1.07, 1.10, 1.18, 1.28, 1.35, 1.30, 1.15, 0.92, 0.70,
]  # fmt: skip

TEAMS = ["data-platform", "analytics-eng", "finance-data", "marketing-data", "sre"]

# What each source calls the thing it fetches, for log lines.
RESOURCES = {
    "src_payments_api": "payments",
    "src_crm_api": "contacts",
    "src_ads_api": "campaign-stats",
    "exp_crm_segments": "segments",
}

# Unique keys checked by the data tests of staging and model pipelines.
KEYS = {
    "stg_orders": "order_id",
    "stg_payments": "payment_id",
    "stg_customers": "customer_id",
    "stg_ad_spend": "campaign_day_id",
    "stg_shipments": "shipment_id",
    "stg_sessions": "session_id",
    "dim_customers": "customer_id",
    "fct_orders": "order_id",
    "fct_shipments": "shipment_id",
    "fct_marketing_spend": "campaign_day_id",
    "fct_sessions": "session_id",
}

COLUMNS = [
    "coupon_code",
    "gift_wrap",
    "loyalty_tier",
    "utm_campaign",
    "refund_reason",
    "carrier_code",
    "basket_size",
    "first_order_at",
    "vat_rate",
    "device_type",
]

FEATURES = [
    "adds column {column}",
    "removes the unused column {column}",
    "refactors the incremental filter",
    "updates the tests for {table}",
    "speeds up the merge step",
    "renames an internal macro",
    "adds a not-null test on {column}",
    "documents {table}",
]

SETTINGS = [
    ("retries", "3", "5"),
    ("timeout_seconds", "600", "900"),
    ("batch_size", "5000", "4000"),
    ("threads", "4", "6"),
    ("alert_channel", "#data-alerts", "#data-oncall"),
]

DASHBOARDS = ["Weekly cohort retention", "Carrier on-time rate", "Refunds by reason", "Campaign spend pacing"]

# Routine warnings and notices that appear on any day. They are not the cause of anything.
NOISE = [
    ("WARN", "deprecated setting 'legacy_timestamps' is still set; it will be removed in v3"),
    ("WARN", "slow query on {table}: {seconds}.{tenth} s (threshold 10 s)"),
    ("INFO", "connection to warehouse.internal reset by peer; reconnected on attempt 2"),
    ("INFO", "{count} late-arriving rows older than 48 h merged into {table}"),
    ("INFO", "schema cache refreshed ({tables} tables)"),
    ("INFO", "garbage collection took {seconds}.{tenth} s"),
    ("WARN", "statistics for {table} are stale; running analyze"),
    ("INFO", "heartbeat ok, {workers} active workers"),
]
