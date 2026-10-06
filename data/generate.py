"""Generate the simulated data platform, its incidents and their answer key.

    python data/generate.py           write everything under data/
    python data/generate.py --check   regenerate in memory and compare with the committed files

Fixed seeds, the repo's own word lists, no Faker: the output never changes unless this file does.
Every pipeline, host, team and incident is made up. Hosts use the reserved .example and .internal names.

An incident is a folder: the alert, 200 to 500 log lines of the six hours before it, hourly metrics for
those six hours and the three days before (the baseline), and the change log of the week before.
Every time is UTC.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from templates import (  # noqa: E402
    CONFIG_ERRORS,
    CREDENTIALS,
    DUPLICATES,
    LATE_NOTICES,
    POSTMORTEMS,
    ROOTS,
    RUNBOOK_OF,
    RUNBOOKS,
    SCHEMA_FIELDS,
)
from words import COLUMNS, DASHBOARDS, EDGES, FEATURES, HOURLY, NOISE, PIPELINES, RESOURCES, SETTINGS  # noqa: E402

from incident_rca_agent.categories import CATEGORIES  # noqa: E402
from incident_rca_agent.incident import METRICS, Platform  # noqa: E402

__all__ = ["RUNBOOK_OF", "SPLITS", "build_platform", "build_postmortems", "build_runbooks", "build_split", "main"]

POSTMORTEM_SEED = 7
SPLITS = {"practice": (1, 15, "p"), "test": (2, 25, "t")}
ON_ROOT_SHARE = 0.28  # alerts that fire on the root-cause pipeline itself; the rest fire downstream
HERRING_SHARE = 0.36
HERRING_KINDS = ("deploy_on_alert_pipeline", "failure_in_other_pipeline", "misleading_metric")
WINDOW_RUNS = 6
BASELINE_RUNS = 72
MIN_PIPELINES = 12
FIRST_DAY, LAST_DAY = date(2026, 1, 12), date(2026, 9, 25)
DST_SPRING, DST_AUTUMN = date(2026, 3, 29), date(2025, 10, 26)  # clocks change at 01:00 UTC in Europe

# duplicate_keys: where duplicates entering at a root are caught by a unique test.
DETECT = {"ing_orders": "stg_orders", "ing_payments": "stg_payments", "ing_events": "stg_sessions"}
DETECT["stg_customers"] = "dim_customers"

# The metrics of the root-cause pipeline that each failure type moves: the answer key's metric evidence.
EVIDENCE_METRICS = {
    "upstream_schema_change": ["rows_out", "errors"],
    "api_rate_limit": ["duration", "rows_out", "errors"],
    "disk_full": ["disk", "rows_out", "errors"],
    "expired_credentials": ["rows_out", "errors"],
    "late_upstream_data": ["rows_in", "rows_out"],
    "out_of_memory": ["memory", "rows_out", "errors"],
    "bad_config_deploy": ["rows_out"],
    "duplicate_keys": ["rows_in", "rows_out"],
    "daylight_saving_bug": ["rows_out", "errors"],
    "dns_failure": ["rows_out", "errors"],
}

# Post-mortem templates that only fit some roots (see templates.POSTMORTEMS).
TEMPLATE_ROOTS = {
    ("expired_credentials", 1): ["exp_finance_ledger"],
    ("late_upstream_data", 0): ["src_partner_sftp"],
    ("late_upstream_data", 1): ["src_shop_db", "src_events_stream"],
    ("late_upstream_data", 2): ["src_ads_api"],
    ("duplicate_keys", 0): ["ing_orders", "ing_events"],
    ("duplicate_keys", 1): ["ing_payments"],
    ("duplicate_keys", 2): ["stg_customers"],
}

CREDENTIAL_NAMES = {
    "token": "access token",
    "password": "password of user etl_reader",
    "key": "SSH key etl-sftp-2025",
    "certificate": "client certificate",
}

INFRA_CHANGES = [
    ("infra", "warehouse.internal", "sre", "minor version upgrade during the maintenance window"),
    ("infra", "worker-pool", "sre", "worker pool scaled from 6 to 8 nodes"),
    ("infra", "scheduler", "sre", "scheduler upgraded to the next patch release"),
    ("access", "marts.daily_revenue", "finance-data", "read access granted to the finance analysts"),
    ("infra", "secret-store", "sre", "audit logging enabled on the secret store"),
]


class Spec(NamedTuple):
    name: str
    layer: str
    owner: str
    minute: int
    reads: str
    writes: str
    host: str
    rows: int
    seconds: int
    memory: int


SPEC = {row[0]: Spec(*row) for row in PIPELINES}
ORDER = {name: index for index, name in enumerate(SPEC)}
MINUTES = sorted({spec.minute for spec in SPEC.values()})


def table(name: str) -> str:
    """The warehouse table a pipeline writes; an export's is the mart it reads."""
    spec = SPEC[name]
    return (spec.reads if spec.layer == "export" else spec.writes).split(",")[0].split(" ")[0]


def topo(name: str) -> tuple[int, int]:
    """Pipelines run in this order within an hour: every pipeline runs after the ones it reads."""
    return SPEC[name].minute, ORDER[name]


def build_platform() -> dict:
    pipelines = [
        {
            "name": spec.name,
            "layer": spec.layer,
            "owner": spec.owner,
            "schedule": f"hourly at :{spec.minute:02d}",
            "reads": spec.reads,
            "writes": spec.writes,
            "host": spec.host,
        }
        for spec in SPEC.values()
    ]
    return {"pipelines": pipelines, "edges": [list(edge) for edge in EDGES]}


PLATFORM = Platform.from_data(build_platform())


def build_runbooks() -> list[dict]:
    return [
        {
            "id": runbook_id,
            "title": title,
            "sections": [
                {"id": f"{runbook_id}.{number}", "title": section_title, "text": text}
                for number, (section_title, text) in enumerate(sections, start=1)
            ],
        }
        for runbook_id, title, sections in RUNBOOKS
    ]


def _below(name: str, layers: tuple[str, ...]) -> list[str]:
    return [other for other, _ in PLATFORM.downstream(name) if SPEC[other].layer in layers]


def build_postmortems(seed: int = POSTMORTEM_SEED) -> list[dict]:
    rng = random.Random(seed)
    drafts = []
    for category, templates in POSTMORTEMS.items():
        for number, template in enumerate(templates):
            root = rng.choice(TEMPLATE_ROOTS.get((category, number), ROOTS[category]))
            below = _below(root, ("mart", "export"))
            alert = rng.choice(below) if below else root
            if category == "daylight_saving_bug":
                day = date(2025, 3, 30) if number == 0 else date(2024, 10, 27)
            else:
                day = date(2024, 11, 4) + timedelta(days=rng.randint(0, 320))
            values = {
                "root": root,
                "alert": alert,
                "hours": rng.randint(2, 9),
                "host": SPEC[root].host or "warehouse.internal",
                "limit": SPEC[root].memory * 2,
                "day": day.isoformat(),
            }
            if category == "upstream_schema_change":
                _, old, new, owner = SCHEMA_FIELDS[root]
                values.update(old=old, new=new, owner=owner[0].upper() + owner[1:])
            elif category == "bad_config_deploy":
                key, value, _, _ = rng.choice(CONFIG_ERRORS)
                values.update(key=key, value=value)
            elif category == "duplicate_keys":
                values["key"] = DUPLICATES[root][1]
            title, summary, root_cause, resolution, follow_ups = (part.format(**values) for part in template)
            drafts.append(
                {
                    "date": day.isoformat(),
                    "title": title,
                    "category": category,
                    "pipelines": [root] if alert == root else [root, alert],
                    "summary": summary,
                    "root_cause": root_cause,
                    "resolution": resolution,
                    "follow_ups": follow_ups,
                }
            )
    drafts.sort(key=lambda item: (item["date"], item["title"]))
    numbers: dict[str, int] = {}
    items = []
    for draft in drafts:
        year = draft["date"][:4]
        numbers[year] = numbers.get(year, 0) + 1
        items.append({"id": f"PM-{year}-{numbers[year]:03d}", **draft})
    return items


POSTMORTEM_LIST = build_postmortems()


# Planning a split.


def _pick_alert(rng: random.Random, category: str, root: str, used: dict[str, int]) -> str:
    """Alerts fire mostly on marts and exports, where the freshness SLAs are, and on varied pipelines."""
    start = root
    if category == "duplicate_keys":
        if rng.random() < 0.6:
            return DETECT[root]
        start = DETECT[root]
    below = _below(start, ("model", "mart", "export")) or [name for name, _ in PLATFORM.downstream(start)]
    # exp_bi_dashboards sits below almost everything, so it only gets the weight of a model
    weights = [
        (3 if SPEC[name].layer in ("mart", "export") and name != "exp_bi_dashboards" else 1) / (1 + used.get(name, 0))
        for name in below
    ]
    return rng.choices(below, weights=weights)[0]


def _variant(rng: random.Random, category: str) -> dict:
    if category == "api_rate_limit":
        return {"cause": rng.choice(["backfill", "retries", "quota"])}
    if category == "out_of_memory":
        return {"cause": rng.choice(["backfill", "sale", "limit"])}
    if category == "bad_config_deploy":
        return {"config": rng.randrange(len(CONFIG_ERRORS))}
    if category == "daylight_saving_bug":
        return {"season": rng.choice(["spring", "autumn"])}
    if category == "dns_failure":
        return {"style": rng.choice(["temporary", "nxdomain"]), "change": rng.random() < 0.6}
    if category == "expired_credentials":
        return {"warning": rng.random() < 0.6, "change": rng.random() < 0.3}
    shares = {"upstream_schema_change": 0.5, "disk_full": 0.5, "late_upstream_data": 0.5, "duplicate_keys": 0.7}
    return {"change": rng.random() < shares[category]}


def _check(category: str, root: str, alert: str) -> str:
    if alert == root:
        return "failed_runs"
    if category == "duplicate_keys" and alert == DETECT[root]:
        return "data_test"
    if category == "late_upstream_data" and root in ("src_shop_db", "src_events_stream"):
        return "row_count"
    return "freshness"


def plan_split(rng: random.Random, n: int) -> list[dict]:
    names = list(CATEGORIES)
    categories = names * (n // len(names)) + rng.sample(names, n % len(names))
    rng.shuffle(categories)
    eligible = [i for i, category in enumerate(categories) if category not in ("late_upstream_data", "duplicate_keys")]
    on_root = set(rng.sample(eligible, round(ON_ROOT_SHARE * n)))
    plans, used = [], {}
    for index, category in enumerate(categories):
        if index in on_root:
            root = rng.choice(ROOTS[category])
            alert = root
        else:
            root = rng.choice([name for name in ROOTS[category] if PLATFORM.downstream(name)])
            alert = _pick_alert(rng, category, root, used)
            used[alert] = used.get(alert, 0) + 1
        plans.append(
            {
                "category": category,
                "root": root,
                "alert": alert,
                "check": _check(category, root, alert),
                "variant": _variant(rng, category),
                "herring": None,
            }
        )
    for number in range(round(HERRING_SHARE * n)):
        kind = HERRING_KINDS[number % len(HERRING_KINDS)]
        options = [
            index
            for index, plan in enumerate(plans)
            if plan["herring"] is None and (kind != "deploy_on_alert_pipeline" or plan["alert"] != plan["root"])
        ]
        plans[rng.choice(options)]["herring"] = kind
    return plans


# One incident.


class Run:
    """One run of one pipeline: its metric values, how it ended, and what it logged."""

    def __init__(self, pipeline: str, time: datetime, number: int):
        self.pipeline, self.time, self.number = pipeline, time, number
        self.status, self.new_data, self.forced = "ok", True, False
        self.factor, self.passes = 1.0, True  # rows compared with a normal run; whether downstream sees them
        self.rows_in = self.rows_out = self.duration = self.errors = self.disk = self.memory = 0
        self.lines: list[tuple[int, str, str, bool]] = []  # seconds after the start, level, message, evidence
        self.version = ""
        self.outcome: tuple[str, str] | None = None  # replaces the "finished" line

    @property
    def hour(self) -> datetime:
        return self.time.replace(minute=0)


def _free_minute(minute: int) -> int:
    while minute % 60 in MINUTES:
        minute += 1
    return minute


def _clock(moment: datetime, other: datetime) -> str:
    return f"{moment:%H:%M}" if moment.date() == other.date() else f"{moment:%Y-%m-%d %H:%M}"


class IncidentBuilder:
    def __init__(self, rng: random.Random, incident_id: str, plan: dict, alert_number: int):
        self.rng, self.id, self.plan = rng, incident_id, plan
        self.category, self.root, self.alert = plan["category"], plan["root"], plan["alert"]
        self.check, self.variant = plan["check"], plan["variant"]
        self.alert_id = f"ALR-{alert_number}"
        self.runs: dict[str, list[Run]] = {}
        self.changes: list[tuple[datetime, str, str, str, str, bool]] = []
        self.failures = 0
        self.herring_pipeline: str | None = None
        self.dupes = 0

    # building blocks

    def at(self, name: str, hour: datetime) -> Run | None:
        for run in reversed(self.runs[name][-8:]):
            if run.hour == hour:
                return run
        return None

    def window(self, name: str) -> list[Run]:
        return self.runs[name][BASELINE_RUNS:]

    def bad(self, name: str) -> list[Run]:
        return [run for run in self.window(name) if run.hour >= self.h0]

    def before(self, name: str) -> list[Run]:
        return [run for run in self.window(name) if run.hour < self.h0]

    def fail(self, run: Run, seconds: int, lines: list, reason: str, errors: int) -> None:
        run.forced, run.status, run.new_data = True, "failed", False
        run.rows_out, run.duration, run.errors = 0, seconds, errors
        run.lines += lines
        run.outcome = ("ERROR", f"run #{run.number} failed after {seconds} s: {reason}")

    def scale(self, run: Run, factor: float) -> None:
        run.factor = factor
        run.rows_out = max(0, round(run.rows_out * factor))
        run.rows_in = max(run.rows_out, round(run.rows_in * factor))
        run.duration = max(3, round(run.duration * (0.5 + 0.5 * factor if factor > 1 else 0.3 + 0.7 * factor)))
        if factor > 1:
            run.memory = min(94, run.memory + round(8 * (factor - 1)))

    def change(self, moment: datetime, kind: str, target: str, author: str, summary: str, evidence: bool) -> None:
        self.changes.append((moment.replace(second=0, microsecond=0), kind, target, author, summary, evidence))

    def usual(self, name: str, metric: str, run: Run) -> int:
        series = self.runs[name]
        index = series.index(run)
        return round(statistics.median(getattr(series[index - 24 * day], metric) for day in (1, 2, 3)))

    # the plan, step by step

    def build(self) -> dict:
        self._timeline()
        self._involve()
        self._baseline()
        getattr(self, f"_{self.category}")()
        self._herring()
        self._propagate()
        self._herring_change()
        self._unrelated_changes()
        alert = self._alert_record()
        logs, evidence_logs = self._logs()
        changes, evidence_changes = self._change_records()
        label = self._label(evidence_logs, evidence_changes)
        files = {
            "alert.json": json.dumps(alert, indent=2, ensure_ascii=False) + "\n",
            "logs.txt": logs,
            "metrics.json": self._metrics_json(),
            "changes.json": json.dumps(changes, indent=2, ensure_ascii=False) + "\n",
        }
        return {"id": self.id, "files": files, "label": label}

    def _failed_seconds_bound(self) -> float:
        if self.check == "data_test":
            return SPEC[self.alert].seconds * 1.4
        if self.category == "api_rate_limit":
            return 640
        if self.category == "out_of_memory":
            return SPEC[self.root].seconds * 2.6 + 5
        if self.category == "disk_full":
            return SPEC[self.root].seconds * 1.1
        return 60

    def _timeline(self) -> None:
        rng = self.rng
        if self.category == "daylight_saving_bug":
            day = DST_SPRING if self.variant["season"] == "spring" else DST_AUTUMN
            h0 = datetime(day.year, day.month, day.day, 1, tzinfo=UTC)
        else:
            span = (LAST_DAY - FIRST_DAY).days
            day = FIRST_DAY + timedelta(days=rng.randint(0, span))
            while day in (DST_SPRING, DST_AUTUMN):
                day += timedelta(days=1)
            h0 = None
        if self.check in ("freshness", "row_count"):
            minute = 50
            back = 5 - rng.choice([1, 2, 3])
        else:
            minute = _free_minute(SPEC[self.alert].minute + math.ceil(self._failed_seconds_bound() / 60) + 1)
            if self.check == "failed_runs":
                self.failures = rng.choice([2, 3])
                back = self.failures - 1
            else:
                back = 0
        if h0 is None:
            fired_hour = datetime(day.year, day.month, day.day, rng.randint(8, 22), tzinfo=UTC)
            h0 = fired_hour - timedelta(hours=back)
        else:
            fired_hour = h0 + timedelta(hours=back)
        self.h0 = h0
        self.fired = fired_hour + timedelta(minutes=minute)
        self.start = self.fired - timedelta(hours=WINDOW_RUNS)

    def _involve(self) -> None:
        rng, root = self.rng, self.root
        closure = [name for name, _ in PLATFORM.upstream(self.alert)]
        involved = {*closure, self.alert, root}
        down = {name for name, _ in PLATFORM.downstream(root)}
        self.affected = [root] + sorted(
            (name for name in down if name == self.alert or PLATFORM.is_upstream(name, self.alert)), key=topo
        )
        blocked = set(self.affected) | {name for name, _ in PLATFORM.upstream(root)} | down
        if self.plan["herring"] in ("failure_in_other_pipeline", "misleading_metric"):
            inside = sorted((name for name in closure if name not in blocked), key=topo)
            outside = sorted((name for name in SPEC if name not in involved and name not in blocked), key=topo)
            self.herring_pipeline = rng.choice(inside or outside)
            involved.add(self.herring_pipeline)
        target = rng.randint(MIN_PIPELINES, MIN_PIPELINES + 3)
        while len(involved) < target:
            spare = sorted((name for name in SPEC if name not in involved and name not in down), key=topo)
            if not spare:
                break
            involved.add(rng.choice(spare))
        self.pipelines = sorted(involved, key=topo)
        self.involved = set(involved)

    def _baseline(self) -> None:
        rng = self.rng
        for name in self.pipelines:
            spec = SPEC[name]
            first = self.start.replace(minute=spec.minute)
            if first < self.start:
                first += timedelta(hours=1)
            number = rng.randint(1200, 9800)
            disk_level, memory_level = rng.randint(32, 62), rng.randint(36, 60)
            runs = []
            for index in range(BASELINE_RUNS + WINDOW_RUNS):
                run = Run(name, first + timedelta(hours=index - BASELINE_RUNS), number + index)
                share = HOURLY[run.time.hour]
                run.rows_out = max(1, round(spec.rows * share * rng.uniform(0.95, 1.05)))
                extra = 0 if spec.layer == "source" else round(run.rows_out * rng.uniform(0.0, 0.02))
                run.rows_in = run.rows_out + extra
                run.duration = max(4, round(spec.seconds * (0.75 + 0.25 * share) * rng.uniform(0.92, 1.08)))
                run.errors = 1 if rng.random() < 0.03 else 0
                run.disk = disk_level + run.time.hour // 8 + rng.randint(-1, 1)
                run.memory = memory_level + round(10 * (share - 1)) + rng.randint(-2, 2)
                runs.append(run)
            self.runs[name] = runs

    # the ten failure types: each one changes the root's runs from its first bad run (self.h0)

    def _upstream_schema_change(self) -> None:
        rng, root = self.rng, self.root
        table, old, new, owner = SCHEMA_FIELDS[root]
        contract = rng.randint(3, 9)
        for run in self.bad(root):
            seconds = max(8, round(run.duration * rng.uniform(0.25, 0.4)))
            records = run.rows_in
            lines = [
                (2, "INFO", f"read {records} records from {table}", False),
                (
                    seconds - 3,
                    "ERROR",
                    f"data contract violation on {table}: required field '{old}' is missing in {records} of "
                    f"{records} records",
                    True,
                ),
                (seconds - 2, "WARN", f"unexpected field '{new}' in {table} is not in contract v{contract}", True),
            ]
            self.fail(
                run, seconds, lines, f"batch b-{run.time:%Y%m%d%H} quarantined, 0 rows written", rng.randint(3, 5)
            )
        if self.variant["change"]:
            summary = f"release notes from {owner}: field {old} renamed to {new} in {table}, read by {root}"
            moment = self.h0 + timedelta(minutes=SPEC[root].minute - rng.randint(30, 240))
            self.change(moment, "notice", root, "data-platform", summary, True)

    def _api_rate_limit(self) -> None:
        rng, root = self.rng, self.root
        spec, resource = SPEC[root], RESOURCES[root]
        quota, retry = rng.choice([1800, 2400, 3600, 5000]), rng.choice([60, 90, 120])
        export = spec.layer == "export"
        for number, run in enumerate(self.bad(root)):
            seconds = rng.randint(520, 640)
            page = rng.randint(6, 30) if number == 0 else 1
            expected = run.rows_in
            done = round(expected * rng.uniform(0.2, 0.4)) if number == 0 else 0
            call = (
                f"POST https://{spec.host}/v3/{resource} (batch {page})"
                if export
                else f"GET https://{spec.host}/v2/{resource}?page={page}"
            )
            lines = [
                (
                    rng.randint(20, 60),
                    "WARN",
                    f"{call} returned 429 Too Many Requests, Retry-After {retry} s (attempt 1/5)",
                    True,
                ),
                (
                    seconds - rng.randint(40, 80),
                    "WARN",
                    f"{call} returned 429 Too Many Requests, Retry-After {retry} s (attempt 5/5)",
                    True,
                ),
                (
                    seconds - 2,
                    "ERROR",
                    f"giving up on {'batch' if export else 'page'} {page} after 5 attempts: HTTP 429 (hourly quota of "
                    f"{quota} requests used up)",
                    True,
                ),
            ]
            reason = f"{done} of {expected} rows uploaded" if export else f"{done} of about {expected} records fetched"
            self.fail(run, seconds, lines, reason, rng.randint(5, 6))
            run.rows_in = done
        first = self.h0 + timedelta(minutes=spec.minute)
        cause = self.variant["cause"]
        if cause == "backfill":
            summary = f"backfill of 2025 {resource} started; it shares the {spec.host} API key with {root}"
            self.change(first - timedelta(minutes=rng.randint(10, 50)), "backfill", root, spec.owner, summary, True)
        elif cause == "retries":
            summary = f"config v{rng.randint(5, 30)}: retries for {spec.host} changed from 3 to 8"
            self.change(first - timedelta(minutes=rng.randint(30, 180)), "config", root, spec.owner, summary, True)
        else:
            summary = f"notice from the {spec.host} provider: hourly quota cut from 6000 to {quota} requests"
            self.change(first - timedelta(minutes=rng.randint(60, 600)), "notice", root, "data-platform", summary, True)

    def _disk_full(self) -> None:
        rng, root = self.rng, self.root
        mount = f"/mnt/work/{root}"
        climbing = self.before(root)
        start = max(80, self.usual(root, "disk", climbing[0]) + 18)
        step = (98 - start) / max(1, len(climbing))
        for number, run in enumerate(climbing):
            run.disk = min(98, round(start + step * number))
            if run.disk >= 90:
                run.lines.append((2, "WARN", f"disk usage on {mount} at {run.disk}% (warning threshold 90%)", True))
        for run in self.bad(root):
            seconds = max(8, round(run.duration * rng.uniform(0.5, 0.9)))
            part = rng.randint(1, 60)
            lines = [
                (2, "WARN", f"disk usage on {mount} at 100% (warning threshold 90%)", True),
                (
                    seconds - 1,
                    "ERROR",
                    f"write to {mount}/tmp/part-{part:05d}.parquet failed: OSError: [Errno 28] No space left on device",
                    True,
                ),
            ]
            self.fail(run, seconds, lines, f"no space left on {mount}", rng.randint(3, 4))
            run.disk = 100
        if self.variant["change"]:
            month = (self.h0 - timedelta(days=40)).strftime("%Y-%m")
            summary = f"backfill of {root} for {month} started; it writes temporary files to {mount}"
            self.change(
                self.h0 - timedelta(hours=rng.randint(18, 40)), "backfill", root, SPEC[root].owner, summary, True
            )

    def _expired_credentials(self) -> None:
        rng, root = self.rng, self.root
        spec, kind = SPEC[root], CREDENTIALS[root]
        expiry = self.h0 + timedelta(minutes=spec.minute - rng.randint(5, 50))
        what = CREDENTIAL_NAMES[kind]
        if self.variant["warning"]:
            run = self.before(root)[-1]
            message = f"{what} for {spec.host} expires at {expiry:%Y-%m-%d %H:%M} UTC; renew it in the secret store"
            run.lines.append((1, "WARN", message, True))
        export = spec.layer == "export"
        for run in self.bad(root):
            seconds = rng.randint(2, 5)
            if kind == "token":
                call = f"POST https://{spec.host}/v3/{RESOURCES.get(root, 'export')}" if export else ""
                call = call or f"GET https://{spec.host}/v2/{RESOURCES[root]}"
                message = f"{call} returned 401 Unauthorized: access token expired at {expiry:%H:%M} UTC"
            elif kind == "password":
                message = f"login to {spec.host} as etl_reader failed: password expired"
            elif kind == "key":
                message = (
                    f"SSH authentication to {spec.host} failed: Permission denied (publickey); "
                    "key etl-sftp-2025 expired"
                )
            else:
                message = (
                    f"TLS handshake with {spec.host} failed: client certificate expired at {expiry:%Y-%m-%d %H:%M} UTC"
                )
            self.fail(
                run, seconds, [(1, "ERROR", message, True)], "authentication failed, no data read", rng.randint(3, 4)
            )
            run.rows_in = 0
        if self.variant["change"]:
            summary = f"automatic rotation of the {what} used by {root} paused during the secret-store migration"
            self.change(self.h0 - timedelta(hours=rng.randint(30, 110)), "infra", "secret-store", "sre", summary, True)

    def _late_upstream_data(self) -> None:
        rng, root = self.rng, self.root
        spec = SPEC[root]
        factor = rng.uniform(0.03, 0.1)
        lag_minutes = rng.randint(95, 160)
        for number, run in enumerate(self.bad(root)):
            run.forced = True
            if root == "src_partner_sftp":
                message = f"no file shipments_{run.time:%Y%m%d_%H}.csv in /inbound/shipments yet; nothing to load"
            elif root == "src_ads_api":
                message = f"report for {run.time:%Y-%m-%d %H}:00 is not ready (status PROCESSING); nothing to load"
            elif root == "src_shop_db":
                lag = lag_minutes + 60 * number
                mark = run.time - timedelta(minutes=lag)
                message = (
                    f"replica {spec.host} is {lag // 60} h {lag % 60:02d} min behind the primary; extracting up to "
                    f"watermark {mark:%H:%M}"
                )
            else:
                lag = lag_minutes + 60 * number
                message = (
                    f"consumer lag on topic web-events is {round(spec.rows * lag / 60)} messages, about "
                    f"{lag // 60} h {lag % 60:02d} min behind"
                )
            run.lines.append((2, "WARN", message, True))
            if root in ("src_partner_sftp", "src_ads_api"):
                run.new_data, run.rows_in, run.rows_out = False, 0, 0
                run.duration = rng.randint(3, 8)
            else:
                self.scale(run, factor * rng.uniform(0.8, 1.2))
        if self.variant["change"]:
            text = LATE_NOTICES[root].format(day=f"{self.h0:%Y-%m-%d}", hours=rng.randint(3, 6))
            self.change(self.h0 - timedelta(hours=rng.randint(6, 30)), "notice", root, "data-platform", text, True)

    def _out_of_memory(self) -> None:
        rng, root = self.rng, self.root
        spec = SPEC[root]
        cause = self.variant["cause"]
        limit = spec.memory // 2 if cause == "limit" else spec.memory
        surge = rng.uniform(2.2, 3.0) if cause != "limit" else 1.0
        doing = f"joining {spec.reads}" if spec.layer != "ingestion" else f"parsing {spec.reads}"
        first = self.h0 + timedelta(minutes=spec.minute)
        if cause != "limit":
            run = self.before(root)[-1]
            run.forced = True
            self.scale(run, surge)
            run.memory = rng.randint(88, 93)
            message = f"memory at {run.memory}% of the {limit} GiB limit while {doing}"
            run.lines.append((round(run.duration * 0.7), "WARN", message, True))
        for run in self.bad(root):
            seconds = round(run.duration * surge**0.5 * rng.uniform(1.4, 2.2))
            run.rows_in = round(run.rows_in * surge)
            lines = [
                (
                    round(seconds * 0.6),
                    "WARN",
                    f"memory at {rng.randint(96, 99)}% of the {limit} GiB limit while {doing}",
                    True,
                ),
                (seconds - 1, "ERROR", f"container {root}-{run.number} was OOMKilled (exit code 137)", True),
            ]
            self.fail(run, seconds, lines, "exit code 137", rng.randint(3, 4))
            run.memory = 100
        if cause == "limit":
            summary = f"config v{rng.randint(5, 30)}: memory limit lowered from {spec.memory} to {limit} GiB"
            self.change(first - timedelta(minutes=rng.randint(30, 300)), "config", root, spec.owner, summary, True)
        elif cause == "backfill":
            source = PLATFORM.inputs(root)[0]
            month = (self.h0 - timedelta(days=60)).strftime("%Y-%m")
            summary = (
                f"backfill of {source} for {month} started; {root} will read about {round(surge)} times the usual rows"
            )
            self.change(
                first - timedelta(minutes=rng.randint(70, 150)), "backfill", source, SPEC[source].owner, summary, True
            )

    def _bad_config_deploy(self) -> None:
        rng, root = self.rng, self.root
        key, value, error, description = CONFIG_ERRORS[self.variant["config"]]
        version = rng.randint(8, 40)
        for run in self.window(root):
            run.version = f"config v{version - 1}"
        for run in self.bad(root):
            run.version = f"config v{version}"
            seconds = rng.randint(2, 6)
            line = (1, "ERROR", f"config error in {root}.yaml v{version}: {key} = '{value}': {error}", True)
            self.fail(run, seconds, [line], "invalid configuration", rng.randint(1, 2))
        first = self.h0 + timedelta(minutes=SPEC[root].minute)
        summary = f"config v{version}: {description}"
        self.change(first - timedelta(minutes=rng.randint(4, 40)), "config", root, SPEC[root].owner, summary, True)

    def _duplicate_keys(self) -> None:
        rng, root = self.rng, self.root
        template, key, infra = DUPLICATES[root]
        detector = DETECT[root]
        hours = 1 if self.check == "data_test" else rng.choice([1, 2])
        factor = rng.uniform(1.8, 2.3)
        for run in self.bad(root)[:hours]:
            run.forced, run.passes = True, False
            normal = run.rows_out
            self.scale(run, factor)
            extra = run.rows_out - normal
            self.dupes += extra
            message = template.format(offset=rng.randint(10**7, 9 * 10**7), count=extra, stamp=f"{run.time:%Y%m%d%H}")
            run.lines.append((2, "WARN", message, True))
            run.lines.append(
                (run.duration - 2, "WARN", f"row count {run.rows_out} is {factor:.1f}x the usual for this hour", False)
            )
        for run in self.bad(detector):
            parent = self.at(root, run.hour)
            if parent is not None and parent.factor > 1:
                run.rows_in = round(run.rows_in * parent.factor)
            seconds = run.duration
            line = (
                seconds - 2,
                "ERROR",
                f"unique test on {key} failed: {self.dupes} duplicate keys in rows read from {root}",
                False,
            )
            self.fail(run, seconds, [line], "data test failed", 1)
        first = self.h0 + timedelta(minutes=SPEC[root].minute)
        if root == "stg_customers":
            summary = "deploy stg_customers v2.4.0: matches CRM contacts and shop customers on email"
            self.change(
                first - timedelta(minutes=rng.randint(20, 120)), "deploy", root, SPEC[root].owner, summary, True
            )
        elif self.variant["change"]:
            target = (
                infra.split(" ")[2] if infra.startswith("CDC") else "stream" if "stream" in infra else "landing-bucket"
            )
            self.change(first - timedelta(minutes=rng.randint(10, 50)), "infra", target, "sre", infra, True)

    def _daylight_saving_bug(self) -> None:
        rng, root = self.rng, self.root
        day = f"{self.h0:%Y-%m-%d}"
        spring = self.variant["season"] == "spring"
        for number, run in enumerate(self.bad(root)):
            seconds = rng.randint(4, 12) if spring else rng.randint(6, 20)
            if spring:
                lines = [
                    (
                        1,
                        "INFO",
                        f"building hourly partitions of {SPEC[root].writes} for local day {day} (Europe/Berlin)",
                        False,
                    ),
                    (
                        seconds - 1,
                        "ERROR",
                        f"NonExistentTimeError: {day} 02:00:00 does not exist in Europe/Berlin",
                        True,
                    ),
                ]
                reason = "could not build the partition for local hour 02"
            else:
                test = f"completeness test failed: local hour 02 of {day} has 2 partitions, expected 1"
                lines = [(seconds - 2, "ERROR", test, False)]
                if number == 0:
                    run.rows_in *= 2
                    message = (
                        f"AmbiguousTimeError: {day} 02:00:00 is ambiguous in Europe/Berlin; both occurrences written "
                        f"to partition {day} 02"
                    )
                    lines.insert(0, (2, "WARN", message, True))
                reason = "data test failed"
            self.fail(run, seconds, lines, reason, rng.randint(3, 4))

    def _dns_failure(self) -> None:
        rng, root = self.rng, self.root
        host = SPEC[root].host
        for run in self.bad(root):
            seconds = rng.randint(15, 22)
            if self.variant["style"] == "temporary":
                message = f"could not resolve {host}: Temporary failure in name resolution (attempt 3/3)"
            else:
                message = f"DNS lookup for {host} failed: NXDOMAIN from resolver 10.20.0.2"
            self.fail(run, seconds, [(seconds - 1, "ERROR", message, True)], f"could not connect to {host}", 3)
        if self.variant["change"]:
            first = self.h0 + timedelta(minutes=SPEC[root].minute)
            summary = rng.choice(
                [
                    f"DNS record for {host}, used by {root}, moved to the new internal zone",
                    f"resolvers of the worker pool that runs {root} switched to 10.20.0.53",
                ]
            )
            self.change(first - timedelta(minutes=rng.randint(10, 90)), "infra", "dns", "sre", summary, True)

    # red herrings

    def _herring(self) -> None:
        rng, name = self.rng, self.herring_pipeline
        if self.plan["herring"] == "failure_in_other_pipeline":
            spec = SPEC[name]
            runs = self.window(name)
            position = rng.randint(1, 4)
            run = runs[position]
            seconds = max(6, round(run.duration * rng.uniform(0.4, 0.8)))
            if spec.host and spec.host != "bi.internal":
                message, reason = (
                    f"request to {spec.host} failed: HTTP 503 Service Unavailable (attempt 3/3)",
                    "service unavailable",
                )
            else:
                message, reason = rng.choice(
                    [
                        (
                            f"lock wait timeout exceeded on {spec.writes}; transaction rolled back",
                            "could not get a lock",
                        ),
                        (
                            f"connection to warehouse.internal lost during COPY into {spec.writes}",
                            "warehouse connection lost",
                        ),
                    ]
                )
            self.fail(run, seconds, [(seconds - 1, "ERROR", message, False)], reason, rng.randint(3, 4))
            catch_up = runs[position + 1]
            catch_up.forced = True
            self.scale(catch_up, 2.0)
            catch_up.lines.append((2, "INFO", "catching up: processing 2 hours of new data", False))
        elif self.plan["herring"] == "misleading_metric":
            spec = SPEC[name]
            first = rng.randint(1, 3)
            for run in self.window(name)[first : first + rng.choice([2, 3])]:
                extra = round(run.duration * rng.uniform(1.4, 2.4))
                run.duration += extra
                message = rng.choice(
                    [
                        f"waited {extra} s for a free warehouse slot (queue: {rng.randint(14, 40)} queries)",
                        f"statistics refresh on {spec.writes} took {extra} s",
                        f"vacuum of {spec.writes} took {extra} s",
                    ]
                )
                run.lines.append((run.duration - 5, "INFO", message, False))

    def _herring_change(self) -> None:
        if self.plan["herring"] != "deploy_on_alert_pipeline":
            return
        rng, alert = self.rng, self.alert
        spec = SPEC[alert]
        first_bad = next(run for run in self.window(alert) if run.status != "ok" or run.factor != 1)
        moment = first_bad.time - timedelta(minutes=rng.randint(15, 80))
        if rng.random() < 0.5:
            version = f"v{rng.randint(1, 4)}.{rng.randint(0, 12)}.{rng.randint(0, 9)}"
            feature = rng.choice(FEATURES).format(column=rng.choice(COLUMNS), table=table(alert))
            self.change(moment, "deploy", alert, spec.owner, f"deploy {alert} {version}: {feature}", False)
        else:
            setting, old, new = rng.choice(SETTINGS)
            version = f"config v{rng.randint(5, 30)}"
            self.change(moment, "config", alert, spec.owner, f"{version}: {setting} changed from {old} to {new}", False)
        for run in self.window(alert):
            if run.time > moment:
                run.version = version

    # what the root's trouble does downstream

    def _propagate(self) -> None:
        last_new = {name: self.runs[name][BASELINE_RUNS - 1].time for name in self.pipelines}
        hours = sorted({run.hour for name in self.pipelines for run in self.window(name)})
        for hour in hours:
            for name in self.pipelines:
                run = self.at(name, hour)
                if run is None or run.time < self.start:
                    continue
                if not run.forced:
                    parents = [self.at(parent, hour) for parent in PLATFORM.inputs(name) if parent in self.involved]
                    parents = [parent for parent in parents if parent is not None]
                    stale = [parent for parent in parents if not parent.new_data]
                    if stale:
                        upstream = stale[0].pipeline
                        run.status, run.new_data = "skipped", False
                        run.rows_in = run.rows_out = run.errors = 0
                        run.duration = self.rng.randint(1, 3)
                        run.memory = max(4, run.memory // 4)
                        run.lines = []
                        since = _clock(last_new[upstream], run.time)
                        run.outcome = (
                            "WARN",
                            f"upstream {upstream} has no new data since {since}; skipping run #{run.number}",
                        )
                    else:
                        factors = [parent.factor for parent in parents if parent.passes and parent.factor != 1]
                        if factors:
                            factor = max(factors, key=lambda value: abs(math.log(value)))
                            self.scale(run, factor)
                            if factor >= 1.5:
                                message = f"row count {run.rows_out} is {factor:.1f}x the usual for this hour"
                                run.lines.append((2, "WARN", message, False))
                if run.new_data and run.status == "ok":
                    last_new[name] = run.time
        self.last_new = last_new

    # the alert, the changes, the logs

    def _alert_record(self) -> dict:
        alert, check = self.alert, self.check
        runs = self.window(alert)
        if check == "failed_runs":
            message = f"{self.failures} consecutive runs of {alert} failed, the last at {runs[-1].time:%H:%M} UTC"
        elif check == "data_test":
            key = DUPLICATES[self.root][1]
            message = f"data test unique {key} failed on {alert}: {self.dupes} duplicate keys in {SPEC[alert].writes}"
        elif check == "row_count":
            last = runs[-1]
            usual = self.usual(alert, "rows_out", last)
            message = (
                f"{alert} wrote {last.rows_out} rows in its {last.time:%H:%M} run, against about {usual} at this hour"
            )
        else:
            since = self.last_new[alert]
            sla = int((self.fired - since).total_seconds() // 3600)
            message = f"{alert} has not loaded new data since {since:%Y-%m-%d %H:%M} UTC (freshness SLA {sla} h)"
        return {
            "id": self.alert_id,
            "fired_at": f"{self.fired:%Y-%m-%dT%H:%M:%SZ}",
            "pipeline": alert,
            "check": check,
            "severity": "high" if SPEC[alert].layer in ("mart", "export") else "medium",
            "message": message,
            "window_start": f"{self.start:%Y-%m-%dT%H:%M:%SZ}",
        }

    def _unrelated_changes(self) -> None:
        rng = self.rng
        targets = sorted((name for name in SPEC if name not in (self.root, self.alert)), key=topo)
        earliest, latest = self.fired - timedelta(days=7) + timedelta(minutes=30), self.start - timedelta(minutes=30)
        span = int((latest - earliest).total_seconds() // 60)
        for _ in range(rng.randint(5, 8)):
            moment = earliest + timedelta(minutes=rng.randint(0, span))
            kind = rng.choices(["deploy", "config", "dashboard", "infra"], weights=[4, 3, 1, 2])[0]
            if kind == "deploy":
                target = rng.choice(targets)
                version = f"v{rng.randint(1, 4)}.{rng.randint(0, 12)}.{rng.randint(0, 9)}"
                feature = rng.choice(FEATURES).format(column=rng.choice(COLUMNS), table=table(target))
                self.change(
                    moment, "deploy", target, SPEC[target].owner, f"deploy {target} {version}: {feature}", False
                )
            elif kind == "config":
                target = rng.choice(targets)
                setting, old, new = rng.choice(SETTINGS)
                summary = f"config v{rng.randint(5, 30)}: {setting} changed from {old} to {new}"
                self.change(moment, "config", target, SPEC[target].owner, summary, False)
            elif kind == "dashboard":
                summary = f"dashboard '{rng.choice(DASHBOARDS)}' published"
                self.change(moment, "dashboard", "bi.internal", "analytics-eng", summary, False)
            else:
                change_kind, target, author, summary = rng.choice(INFRA_CHANGES)
                self.change(moment, change_kind, target, author, summary, False)

    def _change_records(self) -> tuple[list[dict], list[str]]:
        rng = self.rng
        number = rng.randint(3000, 8000)
        records, evidence = [], []
        for offset, (moment, kind, target, author, summary, related) in enumerate(sorted(self.changes)):
            change_id = f"CHG-{number + offset}"
            records.append(
                {
                    "id": change_id,
                    "time": f"{moment:%Y-%m-%dT%H:%M:%SZ}",
                    "kind": kind,
                    "target": target,
                    "author": author,
                    "summary": summary,
                }
            )
            if related:
                evidence.append(change_id)
        return records, evidence

    def _detail(self, name: str, run: Run) -> str:
        spec = SPEC[name]
        if spec.layer == "source":
            if name == "src_partner_sftp":
                return f"downloaded {self.rng.randint(2, 6)} files ({run.rows_in} rows) from {spec.host}"
            if name == "src_shop_db":
                return f"extracted {run.rows_in} rows from {spec.host}"
            if name == "src_events_stream":
                return f"consumed {run.rows_in} messages from topic web-events"
            return f"fetched {run.rows_in} records from {spec.host} in {max(1, run.rows_in // 500)} pages"
        if spec.layer == "ingestion":
            return f"loaded {run.rows_in} rows from {spec.reads} into {spec.writes}"
        if spec.layer in ("staging", "model"):
            return f"{self.rng.randint(4, 18)} data tests passed on {spec.writes}"
        if spec.layer == "mart":
            return f"refreshed {spec.writes} ({run.rows_out} rows)"
        if name == "exp_bi_dashboards":
            return f"refreshed {self.rng.randint(3, 6)} extracts on {spec.host}"
        return f"delivered {run.rows_out} rows to {spec.host}"

    def _noise(self) -> tuple[datetime, str, str, str]:
        rng = self.rng
        name = rng.choice(self.pipelines)
        level, template = rng.choice(NOISE)
        message = template.format(
            table=table(name),
            seconds=rng.randint(11, 19),
            tenth=rng.randint(0, 9),
            count=rng.randint(3, 900),
            tables=rng.randint(180, 260),
            workers=rng.randint(4, 12),
        )
        moment = self.start + timedelta(seconds=rng.randint(40, WINDOW_RUNS * 3600 - 40))
        return moment, level, name, message

    def _logs(self) -> tuple[str, list[str]]:
        rng = self.rng
        mandatory, optional = [], []
        for name in self.pipelines:
            for run in self.window(name):
                start = run.time
                if run.status == "skipped":
                    mandatory.append((start + timedelta(seconds=1), run.outcome[0], name, run.outcome[1], False))
                    continue
                started = f"run #{run.number} started" + (f" with {run.version}" if run.version else "")
                mandatory.append((start + timedelta(seconds=rng.randint(1, 3)), "INFO", name, started, False))
                if run.status == "ok" and run.rows_out > 0:
                    middle = start + timedelta(seconds=max(3, run.duration // 2))
                    optional.append((middle, "INFO", name, self._detail(name, run), False))
                for offset, level, message, evidence in run.lines:
                    mandatory.append((start + timedelta(seconds=max(1, offset)), level, name, message, evidence))
                end = start + timedelta(seconds=run.duration)
                level, message = run.outcome or (
                    "INFO",
                    f"run #{run.number} finished in {run.duration} s, {run.rows_out} rows written to "
                    f"{SPEC[name].writes}",
                )
                mandatory.append((end, level, name, message, False))
        mandatory = [line for line in mandatory if line[0] <= self.fired]
        optional = [line for line in optional if line[0] <= self.fired]
        optional += [(*self._noise(), False) for _ in range(140)]
        rng.shuffle(optional)
        low = max(len(mandatory) + 15, 205)
        high = max(low, min(495, len(mandatory) + len(optional), len(mandatory) + 200))
        lines = mandatory + optional[: rng.randint(low, high) - len(mandatory)]
        lines = sorted(enumerate(lines), key=lambda item: (item[1][0], topo(item[1][2]), item[0]))
        rows, evidence = [], []
        for number, (_, (moment, level, name, message, flagged)) in enumerate(lines, start=1):
            line_id = f"L-{number:04d}"
            rows.append(f"{line_id} {moment:%Y-%m-%dT%H:%M:%SZ} {level} {name} {message}")
            if flagged:
                evidence.append(line_id)
        return "\n".join(rows) + "\n", evidence

    def _metrics_json(self) -> str:
        lines = ["{", '  "step_minutes": 60,', '  "pipelines": {']
        for position, name in enumerate(self.pipelines):
            runs = self.runs[name]
            entries = [
                f'      "first_run": "{runs[0].time:%Y-%m-%dT%H:%M:%SZ}"',
                f'      "status": {json.dumps([run.status for run in runs], separators=(",", ":"))}',
                *(
                    f'      "{metric}": {json.dumps([getattr(run, metric) for run in runs], separators=(",", ":"))}'
                    for metric in METRICS
                ),
            ]
            lines.append(f'    "{name}": {{')
            lines.append(",\n".join(entries))
            lines.append("    }" + ("," if position < len(self.pipelines) - 1 else ""))
        lines += ["  }", "}"]
        return "\n".join(lines) + "\n"

    def _label(self, evidence_logs: list[str], evidence_changes: list[str]) -> dict:
        runbook = RUNBOOK_OF[self.category]
        first_bad = self.bad(self.root)[0]
        return {
            "id": self.id,
            "dir": f"incidents/{self.id}",
            "category": self.category,
            "root_cause_pipeline": self.root,
            "alert_pipeline": self.alert,
            "alert_on_root": self.alert == self.root,
            "check": self.check,
            "affected_pipelines": self.affected,
            "red_herring": self.plan["herring"],
            "herring_pipeline": self.herring_pipeline,
            "evidence": {
                "logs": evidence_logs,
                "metrics": EVIDENCE_METRICS[self.category],
                "changes": evidence_changes,
            },
            "references": {
                "runbook_sections": [f"{runbook}.1", f"{runbook}.3"],
                "past_incidents": [item["id"] for item in POSTMORTEM_LIST if item["category"] == self.category],
            },
            "started_at": f"{first_bad.time:%Y-%m-%dT%H:%M:%SZ}",
            "fired_at": f"{self.fired:%Y-%m-%dT%H:%M:%SZ}",
        }


def build_split(seed: int, n: int, prefix: str) -> list[dict]:
    rng = random.Random(seed)
    plans = plan_split(rng, n)
    numbers = rng.sample(range(1000, 9999), n)
    return [
        IncidentBuilder(rng, f"{prefix}-{index + 1:03d}", plan, numbers[index]).build()
        for index, plan in enumerate(plans)
    ]


def _json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def all_files() -> dict[str, str]:
    files = {
        "shared/lineage.json": _json(build_platform()),
        "shared/runbooks.json": _json(build_runbooks()),
        "shared/postmortems.json": _json(POSTMORTEM_LIST),
    }
    for split, settings in SPLITS.items():
        items = build_split(*settings)
        for item in items:
            for name, content in item["files"].items():
                files[f"{split}/incidents/{item['id']}/{name}"] = content
        files[f"{split}/labels.jsonl"] = "".join(json.dumps(item["label"], ensure_ascii=False) + "\n" for item in items)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare with the committed files instead of writing")
    args = parser.parse_args(argv)
    files = all_files()
    if args.check:
        stale = [
            path
            for path, content in files.items()
            if not (HERE / path).exists() or (HERE / path).read_text(encoding="utf-8") != content
        ]
        for path in stale:
            print(f"differs: data/{path}")
        print("data is up to date" if not stale else f"{len(stale)} file(s) differ: run `make data`")
        return 1 if stale else 0
    for path, content in files.items():
        target = HERE / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    print(f"wrote {len(files)} files under data/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
