"""The seven tools of an investigation.

Every tool returns plain text that is the same every time for the same call, so a stored run replays
exactly. Every piece of evidence in a result carries an id: ALERT, LIN-1 (a lineage answer), L-0142
(a log line), M-1 (a metric summary), CHG-4410 (a change), RB-04.2 (a runbook section) and
PM-2025-007 (a past post-mortem). The session remembers everything it showed, so the grounding check
can tell a real citation from an invented one.
"""

from __future__ import annotations

import inspect
import json
import re
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .incident import LEVELS, METRICS, Incident, Knowledge, Platform, parse_time, show_time
from .textsearch import BM25
from .tool_messages import tool_spec

MAX_LOG_LINES = 15
SAME_MESSAGE = 2  # a repeated message is shown at most this many times per search
BASELINE_DAYS = 3
CHANGE_DAYS = 7

METRIC_LABELS = {
    "duration": "run time in seconds",
    "rows_in": "rows read per run",
    "rows_out": "rows written per run",
    "errors": "errors per run",
    "disk": "disk used, % of the volume",
    "memory": "peak memory, % of the limit",
}
METRIC_ALIASES = {
    "rows": "rows_out",
    "row_count": "rows_out",
    "rows_written": "rows_out",
    "rows_read": "rows_in",
    "latency": "duration",
    "runtime": "duration",
    "run_time": "duration",
    "duration_seconds": "duration",
    "error": "errors",
    "error_count": "errors",
    "disk_usage": "disk",
    "disk_used": "disk",
    "memory_usage": "memory",
    "memory_used": "memory",
    "mem": "memory",
}
LEVEL_ALIASES = {
    "error": "ERROR",
    "errors": "ERROR",
    "err": "ERROR",
    "fatal": "ERROR",
    "critical": "ERROR",
    "warn": "WARN",
    "warning": "WARN",
    "warnings": "WARN",
    "info": "INFO",
    "debug": "DEBUG",
    "all": "",
    "any": "",
    "": "",
}
DIRECTIONS = {
    "": "upstream",
    "up": "upstream",
    "upstream": "upstream",
    "inputs": "upstream",
    "parents": "upstream",
    "down": "downstream",
    "downstream": "downstream",
    "outputs": "downstream",
    "children": "downstream",
}
STATUS_ORDER = ("ok", "failed", "skipped")
NEED_QUERY = "Give a query, for example the error message you found."


@dataclass(frozen=True)
class Source:
    """Something a claim can cite: its id, what kind of source it is, and the exact text that was shown."""

    id: str
    kind: str  # alert, lineage, log, metric, change, runbook, past_incident
    text: str
    pipelines: frozenset[str]
    abnormal: bool  # a warning or error line, a metric with a change point, a change


@dataclass
class ToolResult:
    text: str
    source_ids: list[str]


def _off(metric: str, value: float, usual: float) -> bool:
    """Is one run far enough from its usual level to count? Fixed rules, the same for every incident."""
    if metric in ("rows_in", "rows_out"):
        return abs(value - usual) >= max(0.3 * usual, 10)
    if metric == "duration":
        return abs(value - usual) >= max(0.5 * usual, 30)
    if metric == "errors":
        return value - usual >= 3
    return value - usual >= 15 or value >= 95 > usual  # disk and memory, in percentage points


def change_point(metric: str, values: list[float], usual: list[float | None]) -> int | None:
    """Index of the first run that is off its usual level, or None."""
    for index, (value, expected) in enumerate(zip(values, usual, strict=True)):
        if expected is not None and _off(metric, value, expected):
            return index
    return None


def _number(value: float) -> str:
    return f"{round(value):,}"


def _same_message(message: str) -> str:
    return re.sub(r"\d+", "#", message)


class Session:
    """One investigation: the incident it looks at, and every source it has shown so far."""

    def __init__(self, incident: Incident, platform: Platform, knowledge: Knowledge):
        self.incident = incident
        self.platform = platform
        self.knowledge = knowledge
        self.sources: dict[str, Source] = {}
        self._numbers = {"LIN": 0, "M": 0}
        self._tools: dict[str, Callable[..., ToolResult]] = {
            "get_alert": self.get_alert,
            "get_lineage": self.get_lineage,
            "search_logs": self.search_logs,
            "get_metric_summary": self.get_metric_summary,
            "list_changes": self.list_changes,
            "search_runbooks": self.search_runbooks,
            "search_past_incidents": self.search_past_incidents,
        }

    # plumbing

    def run(self, name: str, arguments: Any) -> ToolResult:
        """Run one tool call as the model asked for it. Problems are answered in text, never raised."""
        handler = self._tools.get(name)
        if handler is None:
            return ToolResult(f"Unknown tool {name!r}. Tools: {', '.join(self._tools)}.", [])
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = None
        if not isinstance(arguments, dict):
            return ToolResult("The arguments must be a JSON object.", [])
        accepted = inspect.signature(handler).parameters
        values = {key: "" if value is None else str(value) for key, value in arguments.items() if key in accepted}
        return handler(**values)

    def _register(self, source: Source) -> str:
        self.sources[source.id] = source
        return source.id

    def _next(self, prefix: str) -> str:
        self._numbers[prefix] += 1
        return f"{prefix}-{self._numbers[prefix]}"

    def _pipeline(self, text: str) -> tuple[str, str]:
        name = text.strip().strip("`'\"").lower()
        if name in self.platform:
            return name, ""
        if not name:
            return "", "Give a pipeline name, for example the one in the alert."
        return "", f"Unknown pipeline {text.strip()!r}. Known pipelines: {', '.join(self.platform.names)}."

    def _window(self, since: str, until: str, default_start: datetime) -> tuple[datetime, datetime, list[str]]:
        day = self.incident.window_end.date()
        start, end, notes = default_start, self.incident.window_end, []
        for label, text in (("since", since), ("until", until)):
            if not text.strip():
                continue
            moment = parse_time(text, day)
            if moment is not None and parse_time(text) is None and moment > self.incident.window_end:
                moment -= timedelta(days=1)  # a bare clock time later than the alert means the day before
            if moment is None:
                fallback = start if label == "since" else end
                notes.append(f"Note: could not read {label} {text.strip()!r}; used {show_time(fallback)}.")
            elif label == "since":
                start = moment
            else:
                end = moment
        return start, end, notes

    def _span(self, start: datetime, end: datetime) -> str:
        if start.date() == end.date():
            return f"{show_time(start)} to {end:%H:%M} UTC"
        return f"{show_time(start)} to {show_time(end)} UTC"

    # the tools

    def get_alert(self) -> ToolResult:
        alert = self.incident.alert
        text = alert.text()
        source = Source("ALERT", "alert", text, frozenset({alert.pipeline}), True)
        return ToolResult(text, [self._register(source)])

    def _io(self, name: str) -> str:
        inputs = self.platform.inputs(name)
        if inputs:
            return f"reads {', '.join(inputs)}"
        pipeline = self.platform.pipelines[name]
        return f"calls {pipeline.host}" if pipeline.host else f"reads {pipeline.reads}"

    def _run_status(self, name: str) -> str:
        runs = self.incident.runs.get(name)
        if runs is None:
            return "no run data in this incident"
        start, end = self.incident.window_start, self.incident.window_end
        counts = Counter(
            status for moment, status in zip(runs.times(), runs.status, strict=True) if start <= moment <= end
        )
        if not counts:
            return "no runs in the incident window"
        order = [status for status in STATUS_ORDER if status in counts] + sorted(set(counts) - set(STATUS_ORDER))
        return "runs: " + ", ".join(f"{counts[status]} {status}" for status in order)

    def get_lineage(self, pipeline: str = "", direction: str = "upstream") -> ToolResult:
        name, problem = self._pipeline(pipeline)
        if problem:
            return ToolResult(problem, [])
        notes = []
        way = DIRECTIONS.get(direction.strip().lower())
        if way is None:
            notes.append(f"Note: direction {direction.strip()!r} is not known; showing upstream.")
            way = "upstream"
        related = self.platform.upstream(name) if way == "upstream" else self.platform.downstream(name)
        arrow = "up" if way == "upstream" else "down"
        meta = self.platform.pipelines[name]
        source_id = self._next("LIN")
        lines = [
            f"[{source_id}] Lineage of {name} ({meta.layer}, owner {meta.owner}, {meta.schedule}), {way}, "
            f"nearest first; runs counted in the incident window "
            f"{self._span(self.incident.window_start, self.incident.window_end)}.",
            *notes,
            f"- {name} itself: {self._io(name)}; {self._run_status(name)}",
        ]
        for other, steps in related:
            layer = self.platform.pipelines[other].layer
            unit = "step" if steps == 1 else "steps"
            lines.append(f"- {other} ({layer}), {steps} {unit} {arrow}, {self._io(other)}; {self._run_status(other)}")
        if not related:
            lines.append(f"- nothing is {way} of {name}")
        text = "\n".join(lines)
        involved = frozenset({name, *(other for other, _ in related)})
        return ToolResult(text, [self._register(Source(source_id, "lineage", text, involved, False))])

    def search_logs(
        self, pipeline: str = "", query: str = "", level: str = "", since: str = "", until: str = ""
    ) -> ToolResult:
        lines, scope, notes = self.incident.logs, "all pipelines", []
        if pipeline.strip():
            name, problem = self._pipeline(pipeline)
            if problem:
                return ToolResult(problem, [])
            lines, scope = [line for line in lines if line.pipeline == name], name
        minimum = LEVEL_ALIASES.get(level.strip().lower())
        if minimum is None:
            notes.append(f"Note: level {level.strip()!r} is not known (use ERROR, WARN or INFO); all levels are shown.")
            minimum = ""
        if minimum:
            lowest = LEVELS.index(minimum)
            lines = [line for line in lines if LEVELS.index(line.level) >= lowest]
        start, end, window_notes = self._window(since, until, self.incident.window_start)
        notes += window_notes
        lines = [line for line in lines if start <= line.time <= end]

        if query.strip():
            by_id = {line.id: line for line in lines}
            index = BM25({line.id: f"{line.level} {line.pipeline} {line.message}" for line in lines})
            ordered = [by_id[line_id] for line_id, _ in index.search(query, k=len(lines))]
        else:
            ordered = sorted(lines, key=lambda line: (-LEVELS.index(line.level), line.time, line.id))

        described = scope + (f", level {minimum} and above" if minimum else "")
        described += f", matching {query.strip()!r}" if query.strip() else ""
        if not ordered:
            text = "\n".join([f"No log lines from {described}, {self._span(start, end)}.", *notes])
            return ToolResult(text, [])

        seen: Counter[tuple[str, str]] = Counter()
        picked = []
        for line in ordered:
            key = (line.pipeline, _same_message(line.message))
            if seen[key] < SAME_MESSAGE:
                seen[key] += 1
                picked.append(line)
        picked = sorted(picked[:MAX_LOG_LINES], key=lambda line: (line.time, line.id))

        order = "most relevant first" if query.strip() else "most severe first"
        footer = f"{len(picked)} of {len(ordered)} matching lines shown ({order}, listed by time)."
        if len(picked) < len(ordered):
            footer += " Repeated messages are cut to two examples; narrow with query, since or until to see others."
        ids = []
        for line in picked:
            ids.append(
                self._register(
                    Source(line.id, "log", line.text(), frozenset({line.pipeline}), line.level in ("WARN", "ERROR"))
                )
            )
        header = f"Log lines from {described}, {self._span(start, end)}:"
        text = "\n".join([header, *notes, *(line.text() for line in picked), footer])
        return ToolResult(text, ids)

    def get_metric_summary(
        self, pipeline: str = "", metric: str = "all", since: str = "", until: str = ""
    ) -> ToolResult:
        name, problem = self._pipeline(pipeline)
        if problem:
            return ToolResult(problem, [])
        wanted = metric.strip().lower() or "all"
        wanted = METRIC_ALIASES.get(wanted, wanted)
        if wanted not in METRICS and wanted != "all":
            return ToolResult(f"Unknown metric {metric.strip()!r}. Use one of: {', '.join(METRICS)}, or all.", [])
        runs = self.incident.runs.get(name)
        if runs is None:
            return ToolResult(f"No metrics for {name} in this incident's data.", [])
        start, end, notes = self._window(since, until, self.incident.window_start)
        times = runs.times()
        window = [index for index, moment in enumerate(times) if start <= moment <= end]
        if not window:
            return ToolResult(f"No runs of {name} between {show_time(start)} and {show_time(end)} UTC.", [])

        per_day = round(timedelta(days=1) / runs.step)
        multi_day = times[window[0]].date() != times[window[-1]].date()

        def when(index: int) -> str:
            return show_time(times[index]) if multi_day else f"{times[index]:%H:%M}"

        def usual(series: list[int], index: int) -> int | None:
            before = [
                series[index - per_day * day] for day in range(1, BASELINE_DAYS + 1) if index - per_day * day >= 0
            ]
            return round(statistics.median(before)) if before else None

        def summarize(name_of_metric: str) -> tuple[list[int], list[int | None], int | None, str]:
            series = runs.values[name_of_metric]
            values = [series[index] for index in window]
            expected = [usual(series, index) for index in window]
            point = change_point(name_of_metric, values, expected)
            if point is None:
                return values, expected, None, ""
            value, base = values[point], expected[point]
            change = f"change point at {when(window[point])}: {_number(value)} against a usual {_number(base)}"
            if base:
                change += f" ({(value - base) / base * 100:+.0f}%)"
            ending = "still off at the end of the window"
            for start_index in range(point + 1, len(values)):
                rest = range(start_index, len(values))
                if all(expected[j] is None or not _off(name_of_metric, values[j], expected[j]) for j in rest):
                    ending = f"back to its usual level from {when(window[start_index])}"
                    break
            return values, expected, point, f"{change}, {ending}"

        source_id = self._next("M")
        span = f"{len(window)} runs from {show_time(times[window[0]])} to {when(window[-1])} UTC"
        compared = f"each compared with the same hour on the previous {BASELINE_DAYS} days"
        abnormal = False
        if wanted == "all":
            lines = [f"[{source_id}] All metrics of {name}, {span}, {compared}.", *notes]
            lines.append("- run times: " + " | ".join(when(index) for index in window))
            for name_of_metric in METRICS:
                values, expected, point, change = summarize(name_of_metric)
                abnormal = abnormal or point is not None
                known = [value for value in expected if value is not None]
                typical = f" (usual about {_number(statistics.median(known))})" if known else ""
                lines.append(
                    f"- {name_of_metric} ({METRIC_LABELS[name_of_metric]}): "
                    + " | ".join(_number(value) for value in values)
                    + f"{typical}; {change or 'no change point'}"
                )
            lines.append("- runs: " + " | ".join(runs.status[index] for index in window))
        else:
            values, expected, point, change = summarize(wanted)
            abnormal = point is not None
            shown = " | ".join(
                f"{when(index)} {_number(value)}" + (f" (usual {_number(base)})" if base is not None else "")
                for index, value, base in zip(window, values, expected, strict=True)
            )
            if point is None:
                verdict = "No change point: every run is within its usual level."
            else:
                head, _, ending = change.partition(", ")
                verdict = f"{head[0].upper()}{head[1:]}. {ending[0].upper()}{ending[1:]}."
            lines = [
                f"[{source_id}] {wanted} of {name} ({METRIC_LABELS[wanted]}), {span}, {compared}.",
                *notes,
                shown,
                verdict,
            ]
        text = "\n".join(lines)
        return ToolResult(text, [self._register(Source(source_id, "metric", text, frozenset({name}), abnormal))])

    def list_changes(self, since: str = "", until: str = "") -> ToolResult:
        default_start = self.incident.window_end - timedelta(days=CHANGE_DAYS)
        start, end, notes = self._window(since, until, default_start)
        changes = [change for change in self.incident.changes if start <= change.time <= end]
        if not changes:
            text = "\n".join([f"No changes between {show_time(start)} and {show_time(end)} UTC.", *notes])
            return ToolResult(text, [])
        ids = []
        for change in changes:
            text = change.text()
            ids.append(
                self._register(Source(change.id, "change", text, frozenset(self.platform.mentioned(text)), True))
            )
        header = f"Changes from {show_time(start)} to {show_time(end)} UTC ({len(changes)}), oldest first:"
        return ToolResult("\n".join([header, *notes, *(change.text() for change in changes)]), ids)

    def search_runbooks(self, query: str = "") -> ToolResult:
        if not query.strip():
            return ToolResult(NEED_QUERY, [])
        sections = self.knowledge.search_runbooks(query)
        if not sections:
            return ToolResult(f"No runbook section matches {query.strip()!r}.", [])
        ids = [
            self._register(Source(s.id, "runbook", s.text(), frozenset(self.platform.mentioned(s.text())), False))
            for s in sections
        ]
        header = f"Runbook sections matching {query.strip()!r}, best first:"
        return ToolResult("\n".join([header, *(section.text() for section in sections)]), ids)

    def search_past_incidents(self, query: str = "") -> ToolResult:
        if not query.strip():
            return ToolResult(NEED_QUERY, [])
        found = self.knowledge.search_postmortems(query)
        if not found:
            return ToolResult(f"No past incident matches {query.strip()!r}.", [])
        ids = [
            self._register(Source(p.id, "past_incident", p.text(), frozenset(self.platform.mentioned(p.text())), False))
            for p in found
        ]
        header = f"Past incidents matching {query.strip()!r}, best first:"
        return ToolResult("\n".join([header, *(postmortem.text() for postmortem in found)]), ids)


def _parameters(properties: dict[str, dict[str, Any]], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


_TIME = "UTC time, for example 2026-03-14 02:00 or 02:00 on the alert's day"

TOOLS = [
    tool_spec("get_alert", "Show the alert that started this investigation.", _parameters({}, [])),
    tool_spec(
        "get_lineage",
        "List the pipelines upstream of a pipeline (that feed it) or downstream (that it feeds), nearest first, "
        "with how their runs went in the incident window.",
        _parameters(
            {
                "pipeline": {"type": "string", "description": "pipeline name, for example mart_daily_revenue"},
                "direction": {"type": "string", "enum": ["upstream", "downstream"]},
            },
            ["pipeline"],
        ),
    ),
    tool_spec(
        "search_logs",
        f"Search the log lines of the incident window. Shows up to {MAX_LOG_LINES} lines with their ids (L-0142). "
        "Without a query, the most severe lines come first.",
        _parameters(
            {
                "pipeline": {"type": "string", "description": "only this pipeline; empty for all"},
                "query": {"type": "string", "description": "words to look for, for example 429 timeout"},
                "level": {"type": "string", "enum": ["ERROR", "WARN", "INFO"], "description": "minimum level"},
                "since": {"type": "string", "description": _TIME},
                "until": {"type": "string", "description": _TIME},
            },
            [],
        ),
    ),
    tool_spec(
        "get_metric_summary",
        "Compare a pipeline's runs in the incident window with the same hour on the previous days and report the "
        "change point. Metrics: duration, rows_in, rows_out, errors, disk, memory, or all of them.",
        _parameters(
            {
                "pipeline": {"type": "string"},
                "metric": {"type": "string", "enum": [*METRICS, "all"]},
                "since": {"type": "string", "description": _TIME},
                "until": {"type": "string", "description": _TIME},
            },
            ["pipeline"],
        ),
    ),
    tool_spec(
        "list_changes",
        f"List deploys, config and infrastructure changes and notices with their ids (CHG-4410). "
        f"Default: the {CHANGE_DAYS} days before the alert.",
        _parameters({"since": {"type": "string", "description": _TIME}, "until": {"type": "string"}}, []),
    ),
    tool_spec(
        "search_runbooks",
        "Search the team's runbooks. Shows the 3 best matching sections with their ids (RB-04.2).",
        _parameters({"query": {"type": "string"}}, ["query"]),
    ),
    tool_spec(
        "search_past_incidents",
        "Search past post-mortems. Shows the 3 best matches with their ids (PM-2025-007).",
        _parameters({"query": {"type": "string"}}, ["query"]),
    ),
]
