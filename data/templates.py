"""Text for the simulated platform: runbooks, failure signatures, change notices and post-mortems.

Everything here is made up for this sample.
"""

# Which pipelines each failure type can start in.
ROOTS = {
    "upstream_schema_change": ["ing_orders", "ing_payments", "ing_crm_contacts", "ing_ad_spend", "ing_shipments",
                               "ing_events"],
    "api_rate_limit": ["src_payments_api", "src_crm_api", "src_ads_api", "exp_crm_segments"],
    "disk_full": ["ing_orders", "ing_events", "ing_shipments", "stg_sessions", "fct_sessions", "exp_finance_ledger"],
    "expired_credentials": ["src_shop_db", "src_payments_api", "src_crm_api", "src_partner_sftp", "exp_crm_segments",
                            "exp_finance_ledger"],
    "late_upstream_data": ["src_partner_sftp", "src_shop_db", "src_events_stream", "src_ads_api"],
    "out_of_memory": ["ing_events", "stg_sessions", "fct_orders", "fct_sessions", "dim_customers",
                      "mart_customer_ltv"],
    "bad_config_deploy": ["ing_payments", "stg_customers", "fct_orders", "fct_marketing_spend", "mart_daily_revenue",
                          "mart_delivery_sla", "exp_finance_ledger", "exp_crm_segments"],
    "duplicate_keys": ["ing_orders", "ing_payments", "ing_events", "stg_customers"],
    "daylight_saving_bug": ["ing_events", "stg_sessions", "fct_sessions"],
    "dns_failure": ["src_payments_api", "src_crm_api", "src_ads_api", "src_partner_sftp", "exp_crm_segments",
                    "exp_finance_ledger"],
}  # fmt: skip

RUNBOOK_OF = {
    "upstream_schema_change": "RB-03",
    "api_rate_limit": "RB-04",
    "disk_full": "RB-05",
    "expired_credentials": "RB-06",
    "late_upstream_data": "RB-07",
    "out_of_memory": "RB-08",
    "bad_config_deploy": "RB-09",
    "duplicate_keys": "RB-10",
    "daylight_saving_bug": "RB-11",
    "dns_failure": "RB-12",
}

RUNBOOKS = [
    ("RB-01", "Triage a pipeline alert", [
        ("Read the alert", "Note the alerting pipeline, the check that fired (freshness, row count, failed runs, "
         "data test or duration) and the time. The alert tells you where the problem shows, not where it starts."),
        ("Walk the lineage upstream", "List the upstream pipelines of the alerting pipeline. Pipelines that skip "
         "runs or wait for data are symptoms. Look for the upstream-most pipeline whose runs failed, slowed down or "
         "wrote unusual row counts."),
        ("Find the first failure", "Search that pipeline's logs at WARN and above around its first bad run, and "
         "compare its metrics with the same hour on previous days. The first warning or error usually names the "
         "cause."),
        ("Check recent changes", "List deploys, config and infrastructure changes of the last days. A change shortly "
         "before the first failure, on the failing pipeline, is a strong suspect. A change on a pipeline that only "
         "shows symptoms usually is not."),
    ]),
    ("RB-02", "Freshness and row-count alerts", [
        ("Stale or empty data", "A freshness alert means no new data arrived in time. A row-count alert means a run "
         "wrote far fewer or far more rows than usual for that hour. Both are usually symptoms of a problem further "
         "upstream."),
        ("Failed or skipped upstream runs", "When an upstream pipeline fails, downstream pipelines skip their runs or "
         "fail their dependency checks. Fix the upstream failure first; downstream pipelines catch up on their next "
         "run."),
        ("Successful runs without data", "When every upstream run succeeded but wrote zero or few rows, the data is "
         "late or missing at the source. See RB-07 for late upstream data."),
    ]),
    ("RB-03", "Upstream schema change", [
        ("Symptoms", "An ingestion pipeline fails its data contract: a required field is missing, renamed or has a "
         "new type. Runs fail quickly with 0 rows written and the batch is quarantined, while rows read stay normal."),
        ("Confirm", "Compare the field names in the contract error with the source's release notes or change "
         "notices. A renamed field shows as one field missing and one unexpected field in the same batch."),
        ("Fix", "Update the data contract and the mapping for the renamed field, release the quarantined batches, "
         "then rerun the ingestion pipeline and its downstream pipelines (RB-13)."),
        ("Prevent", "Subscribe to the source's change notices and run the contract test against the source's "
         "sandbox before each release."),
    ]),
    ("RB-04", "API rate limits (HTTP 429)", [
        ("Symptoms", "A source or export pipeline logs HTTP 429 Too Many Requests with a Retry-After header, retries, "
         "then gives up. Run time grows because of the back-off, and fewer records arrive."),
        ("Confirm", "Check the hourly quota in the error message, and look for other jobs that use the same API key "
         "at the same time, such as backfills."),
        ("Fix", "Pause or reschedule the job that shares the quota, wait for the quota window to reset, then rerun "
         "the pipeline. Do not raise the retry count: it uses up the quota faster."),
        ("Prevent", "Give backfills their own API key or run them at night, and alert when quota use passes 80%."),
    ]),
    ("RB-05", "Disk full on workers", [
        ("Symptoms", "Writes fail with OSError: [Errno 28] No space left on device. Disk use on the pipeline's work "
         "volume climbs over several runs before the failure."),
        ("Confirm", "Check disk use of the pipeline's volume under /mnt/work and list the largest files. Temporary "
         "files left behind by backfills are the usual cause."),
        ("Fix", "Delete temporary files that no running job needs, or extend the volume, then rerun the failed runs."),
        ("Prevent", "Clean temporary files at the end of every run and alert at 85% disk use."),
    ]),
    ("RB-06", "Expired credentials and certificates", [
        ("Symptoms", "Runs fail within seconds with 401 Unauthorized, a refused login, an expired password, an "
         "expired SSH key or an expired client certificate. No data is read."),
        ("Confirm", "Find the expiry date in the error or in the secret store. Expiry warnings are often logged hours "
         "before the failure."),
        ("Fix", "Rotate the credential in the secret store, restart the pipeline so it reads the new secret, and "
         "rerun the failed runs."),
        ("Prevent", "Track every expiry date, rotate 14 days ahead, and alert on expiry warnings."),
    ]),
    ("RB-07", "Late upstream data", [
        ("Symptoms", "Runs succeed but write zero or very few rows: an expected file is not there, a replica lags, a "
         "stream consumer falls behind, or a report is not ready. Downstream pipelines run on stale data without "
         "errors."),
        ("Confirm", "Read the source pipeline's warnings about missing files, lag or watermarks, and compare rows "
         "written with the same hour on previous days."),
        ("Fix", "Contact the data provider, wait for the data, then rerun the source pipeline and backfill the "
         "missing hours downstream (RB-13)."),
        ("Prevent", "Add a freshness check on the source itself, so the alert fires where the delay starts."),
    ]),
    ("RB-08", "Out of memory (exit 137)", [
        ("Symptoms", "The container is OOMKilled and the task fails with exit code 137, often after running longer "
         "than usual. Memory climbs towards the limit in the runs before."),
        ("Confirm", "Compare rows read with the same hour on previous days: a surge in input volume, from a backfill "
         "or a sale, is the usual trigger."),
        ("Fix", "Raise the memory limit of the job or process the input in smaller batches, then rerun the failed "
         "runs."),
        ("Prevent", "Alert when memory passes 85% of the limit, and size limits for the peak volume."),
    ]),
    ("RB-09", "Bad config deploys and rollbacks", [
        ("Symptoms", "A pipeline fails at start-up, seconds after loading a new config version, with a config error "
         "that names the key and the value."),
        ("Confirm", "Match the config version in the log with the change log: the failures start with the first run "
         "after the deploy."),
        ("Fix", "Roll back to the previous config version, rerun the failed runs, then fix the config in review."),
        ("Prevent", "Validate configs in CI against the warehouse schema before they are deployed."),
    ]),
    ("RB-10", "Duplicate keys", [
        ("Symptoms", "A unique test or a merge fails because a key appears more than once. The table where the test "
         "fails is often not where the duplicates came from."),
        ("Confirm", "Walk upstream to the first table where rows written jumped: a consumer that restarted from an "
         "old offset, landing files loaded twice, or a merge that matched twice."),
        ("Fix", "Remove the duplicates in the affected partitions of the pipeline where they entered, then rerun "
         "downstream."),
        ("Prevent", "Make loads idempotent with a key-based merge, and test for duplicates at ingestion."),
    ]),
    ("RB-11", "Daylight-saving time transitions", [
        ("Symptoms", "On the night clocks change (the last Sunday of March and of October in Europe), jobs that work "
         "in local time fail with NonExistentTimeError or AmbiguousTimeError, or write a missing or doubled hour."),
        ("Confirm", "Check the date: an error about local hour 02 on a clock-change day points to time-zone "
         "handling, not to the data."),
        ("Fix", "Rebuild the affected day in UTC, then rerun downstream. Remove duplicates from a repeated hour "
         "first."),
        ("Prevent", "Store and partition by UTC, and convert to local time only for display."),
    ]),
    ("RB-12", "DNS and name resolution failures", [
        ("Symptoms", "Connections fail before they start: Temporary failure in name resolution, NXDOMAIN, or DNS "
         "lookups that time out, for one host or a whole worker pool."),
        ("Confirm", "Resolve the host from the worker and from another network, and check recent DNS and network "
         "changes for that host and the resolver."),
        ("Fix", "Restore the DNS record or point the workers back to a working resolver, then rerun the failed runs."),
        ("Prevent", "Monitor name resolution of every external host the platform depends on."),
    ]),
    ("RB-13", "Rerun and backfill", [
        ("Rerun in lineage order", "After the cause is fixed, rerun the root-cause pipeline first, then each "
         "downstream pipeline in lineage order. Most pipelines catch up on their next hourly run."),
        ("Backfill missing hours", "For hours that were skipped or empty, start a backfill for that time range only, "
         "and check row counts against the same hour on previous days."),
        ("Close the loop", "Confirm that the alerting pipeline is fresh again before you close the incident."),
    ]),
    ("RB-14", "Data tests", [
        ("Unique and not-null tests", "A failed unique test means duplicate keys; a failed not-null test means "
         "missing values. Find where they entered before you fix the table where the test failed."),
        ("Completeness tests", "A completeness test counts hourly partitions per day. 23 or 25 partitions on a "
         "clock-change day point to daylight-saving handling (RB-11)."),
        ("Row-count tests", "A row-count test compares a run with the same hour on previous days. A drop means late "
         "or failed upstream data; a jump often means duplicates."),
    ]),
    ("RB-15", "Communicating an incident", [
        ("First update", "Post in the on-call channel within 15 minutes: what is affected, since when, and who is "
         "looking."),
        ("Root cause update", "When the cause is known, post it with its evidence, the affected pipelines and the next "
         "steps."),
        ("Post-mortem", "Write a post-mortem within five working days for every incident that affected a mart or an "
         "export."),
    ]),
]  # fmt: skip

# upstream_schema_change: per ingestion pipeline, the landing table, the old field, the new field, who changed it.
SCHEMA_FIELDS = {
    "ing_orders": ("landing.shop_orders", "total_amount", "order_total", "the shop database team"),
    "ing_payments": ("landing.payments", "amount", "amount_minor", "the payment provider"),
    "ing_crm_contacts": ("landing.crm_contacts", "email", "email_address", "the CRM vendor"),
    "ing_ad_spend": ("landing.ad_spend", "cost", "cost_micros", "the ad platform"),
    "ing_shipments": ("landing.shipments", "delivered_at", "delivery_ts", "the logistics partner"),
    "ing_events": ("landing.events", "session_id", "sid", "the web tracking team"),
}

# bad_config_deploy: key, bad value, the error it causes, and how the change log described the deploy.
CONFIG_ERRORS = [
    ("target_schema", "analytics_v2", "schema analytics_v2 does not exist in warehouse.internal",
     "target_schema changed from analytics to analytics_v2"),
    ("partition_column", "order_dt", "column order_dt not found in the source table",
     "partition_column renamed from order_date to order_dt"),
    ("timezone", "Europe/Berln", "unknown time zone 'Europe/Berln'", "timezone set to Europe/Berln"),
    ("connection", "warehouse_prod2", "connection warehouse_prod2 is not defined",
     "connection switched to warehouse_prod2"),
    ("incremental_key", "updated", "column updated not found; did you mean updated_at?",
     "incremental_key changed from updated_at to updated"),
    ("max_parallel_tasks", "0", "must be between 1 and 32", "max_parallel_tasks set to 0 for the maintenance window"),
]  # fmt: skip

# expired_credentials: what kind of credential each pipeline uses.
CREDENTIALS = {
    "src_shop_db": "password",
    "src_payments_api": "token",
    "src_crm_api": "token",
    "src_partner_sftp": "key",
    "exp_crm_segments": "token",
    "exp_finance_ledger": "certificate",
}

# duplicate_keys: how duplicates enter at each root, and the infrastructure change that can explain it.
DUPLICATES = {
    "ing_orders": ("consumer group cdc-orders restarted from offset {offset}: replaying {count} change events",
                   "order_id", "CDC connector cdc-orders restarted after broker maintenance (feeds ing_orders)"),
    "ing_events": ("consumer group events-loader restarted from offset {offset}: replaying {count} messages",
                   "event_id", "stream consumer events-loader redeployed after broker maintenance (feeds ing_events)"),
    "ing_payments": ("landing file payments_{stamp}.json loaded twice (same checksum)", "payment_id",
                     "landing bucket replication re-enabled after maintenance; it copies the files read by "
                     "ing_payments"),
    "stg_customers": ("merge of CRM contacts and shop customers matched {count} customers twice on email",
                      "customer_id", ""),
}  # fmt: skip

# late_upstream_data: the notice a provider may have sent.
LATE_NOTICES = {
    "src_partner_sftp": "logistics partner notice: file drops on {day} delayed by about {hours} hours (maintenance); "
    "read by src_partner_sftp",
    "src_shop_db": "shop database replica maintenance on {day}; replication to the replica read by src_shop_db may lag",
    "src_events_stream": "stream broker upgrade on {day}; consumers such as src_events_stream may lag",
    "src_ads_api": "ad platform notice: reports for {day} may be published up to {hours} hours late; read by "
    "src_ads_api",
}

POSTMORTEM_EXTRA = [
    "api_rate_limit",
    "late_upstream_data",
    "out_of_memory",
    "bad_config_deploy",
    "duplicate_keys",
]

# Past post-mortems: title, what happened, root cause, resolution, follow-up.
POSTMORTEMS = {
    "upstream_schema_change": [
        ("Renamed field broke {root}",
         "{alert} was stale for {hours} hours and its freshness alert fired.",
         "{owner} renamed the field {old} to {new}. {root} failed its data contract on every batch and quarantined "
         "it, so nothing reached the warehouse.",
         "Updated the contract and the mapping, released the quarantined batches and reran {root}.",
         "Subscribed to the source's change notices."),
        ("Contract violation on {root} after a source release",
         "Row counts of {alert} dropped to zero for {hours} hours.",
         "A source release replaced {old} with {new}. The data contract of {root} rejected every record: required "
         "field missing.",
         "Mapped the new field, reran {root} and backfilled the missing hours.",
         "Contract tests now run against the source sandbox before each release."),
    ],
    "api_rate_limit": [
        ("{host} quota used up by a backfill",
         "{alert} missed its freshness SLA by {hours} hours.",
         "A backfill shared the API key of {root}. The API answered HTTP 429 Too Many Requests and {root} gave up "
         "after 5 attempts on each page.",
         "Paused the backfill, waited for the quota to reset and reran {root}.",
         "Backfills now use their own API key and run at night."),
        ("HTTP 429 from {host} after a quota cut",
         "{alert} received partial data for {hours} hours.",
         "The provider lowered the hourly quota. {root} hit HTTP 429 Too Many Requests every hour, and its run time "
         "grew with the Retry-After waits.",
         "Asked the provider to restore the quota and spread the requests over the hour.",
         "Alert when quota use passes 80%."),
        ("Retry storm on {root}",
         "{alert} was late for {hours} hours.",
         "A higher retry count made {root} use up the hourly quota of {host} faster; every call then got HTTP 429.",
         "Restored the retry setting and reran {root}.",
         "Retries now back off and respect Retry-After."),
    ],
    "disk_full": [
        ("No space left on device on {root}",
         "{alert} failed its freshness check for {hours} hours.",
         "Temporary files from a backfill filled /mnt/work/{root}. Writes failed with OSError: [Errno 28] No space "
         "left on device.",
         "Deleted the temporary files and reran the failed runs.",
         "Temporary files are now cleaned at the end of every run; disk alert at 85%."),
        ("Work volume of {root} full",
         "{alert} showed no new data for {hours} hours.",
         "Disk use on /mnt/work/{root} grew for days and reached 100%; every run then failed with No space left on "
         "device.",
         "Extended the volume and reran {root}.",
         "Weekly review of volume growth."),
    ],
    "expired_credentials": [
        ("Expired credential stopped {root}",
         "{alert} was stale for {hours} hours.",
         "The credential {root} uses for {host} expired. Every run failed within seconds with an authentication "
         "error (401 Unauthorized).",
         "Rotated the credential and restarted {root}.",
         "Expiry dates are tracked and rotated 14 days ahead."),
        ("Certificate expiry on {root}",
         "{alert} did not receive data for {hours} hours.",
         "A client certificate used by {root} expired; the TLS handshake with {host} failed on every run.",
         "Issued a new certificate and reran the failed runs.",
         "Alert on expiry warnings."),
    ],
    "late_upstream_data": [
        ("Late files from {host}",
         "{alert} row counts dropped to zero for {hours} hours; no pipeline failed.",
         "The provider delivered the files hours late. {root} found no file and finished with 0 rows, so every "
         "downstream pipeline ran on stale data.",
         "Waited for the files, reran {root} and backfilled the missing hours.",
         "Freshness check on the source itself."),
        ("Replica lag behind {root}",
         "{alert} showed partial data for {hours} hours.",
         "The source lagged by hours. {root} extracted only up to its watermark and wrote few rows; no run failed.",
         "Waited for the source to catch up and backfilled.",
         "Lag alert at 30 minutes."),
        ("Report not ready for {root}",
         "{alert} freshness alert fired after {hours} hours.",
         "The provider published its report late. {root} logged that the report was not ready and wrote 0 rows.",
         "Reran {root} once the report was published.",
         "Retry the report until it is ready, with a clear warning."),
    ],
    "out_of_memory": [
        ("{root} OOMKilled after an input surge",
         "{alert} missed its freshness SLA by {hours} hours.",
         "Rows read by {root} tripled during a backfill. The container was OOMKilled (exit code 137) on every run.",
         "Raised the memory limit to {limit} GiB and split the input into batches.",
         "Memory alert at 85% of the limit."),
        ("Exit code 137 on {root}",
         "{alert} was stale for {hours} hours.",
         "A sale doubled the input of {root}. Memory reached the limit and the task failed with exit code 137 after "
         "running twice as long as usual.",
         "Processed the input in smaller batches and reran.",
         "Limits sized for peak volume."),
        ("Memory leak in {root}",
         "{alert} received no data for {hours} hours.",
         "Memory of {root} climbed towards the limit run after run until the container was OOMKilled (exit code 137).",
         "Restarted with a higher limit, then fixed the leak.",
         "Track peak memory per run."),
    ],
    "bad_config_deploy": [
        ("Config deploy broke {root}",
         "{alert} failed for {hours} hours.",
         "A config deploy set {key} to {value}. {root} failed at start-up with a config error on every run after the "
         "deploy.",
         "Rolled back to the previous config version and reran.",
         "Configs are validated in CI before deploy."),
        ("Invalid {key} in {root}.yaml",
         "{alert} was stale for {hours} hours.",
         "A new config version of {root} had an invalid {key}; runs failed within seconds of loading it.",
         "Rolled back, fixed the value in review and redeployed.",
         "Config schema check in CI."),
        ("Rollback of {root} config",
         "{alert} failed its freshness check for {hours} hours.",
         "The first run after a config deploy of {root} failed with a config error: {key} = '{value}'.",
         "Rolled back the deploy and reran the failed runs.",
         "Deploys of production configs need a second reviewer."),
    ],
    "duplicate_keys": [
        ("Duplicate {key} values after a consumer restart",
         "A unique test failed downstream of {root}; {alert} stopped updating for {hours} hours.",
         "A consumer of {root} restarted from an old offset and replayed messages, so {root} loaded rows twice. The "
         "unique test on {key} failed further down.",
         "Removed the duplicates at {root} and reran downstream.",
         "Loads are now idempotent with a key-based merge."),
        ("Files loaded twice by {root}",
         "{alert} failed for {hours} hours after a unique test failure.",
         "The same landing file was loaded twice by {root}; the test unique {key} failed downstream.",
         "Deleted the second copy and reran.",
         "Checksums of loaded files are recorded."),
        ("Merge matched customers twice in {root}",
         "{alert} was stale for {hours} hours.",
         "A merge in {root} matched some records twice, so {key} values were duplicated and a unique test failed "
         "downstream.",
         "Fixed the merge condition and rebuilt the table.",
         "Duplicate test at the pipeline itself."),
    ],
    "daylight_saving_bug": [
        ("Clock change broke {root}",
         "{alert} stopped updating on the clock-change night, for {hours} hours.",
         "{root} builds hourly partitions in local time (Europe/Berlin). On {day} local hour 02 did not exist and "
         "the job failed with NonExistentTimeError.",
         "Rebuilt the day in UTC and reran downstream.",
         "Partitions are now built in UTC."),
        ("Doubled hour on the autumn clock change",
         "A unique test failed downstream of {root}; {alert} was stale for {hours} hours.",
         "On {day} local hour 02 happened twice. {root} raised AmbiguousTimeError warnings and wrote the hour twice, "
         "so keys were duplicated.",
         "Removed the doubled hour and reran.",
         "Local time only for display."),
    ],
    "dns_failure": [
        ("{host} could not be resolved",
         "{alert} was stale for {hours} hours.",
         "After a DNS change, {host} returned NXDOMAIN from the resolver. Every run of {root} failed before "
         "connecting.",
         "Restored the DNS record and reran {root}.",
         "Monitor name resolution of external hosts."),
        ("Name resolution failures on {root}",
         "{alert} received no data for {hours} hours.",
         "DNS lookups for {host} timed out and then failed with Temporary failure in name resolution.",
         "Pointed the workers back to the old resolver and reran.",
         "Resolver changes go through change review."),
    ],
}  # fmt: skip
