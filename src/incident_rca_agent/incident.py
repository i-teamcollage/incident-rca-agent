"""The data an investigation can look at: the platform's lineage, one incident, and the team's knowledge.

One incident is a folder:

    alert.json     the alert that fired, and where the exported window of logs and metrics starts
    logs.txt       one log line per row: id, UTC time, level, pipeline, message
    metrics.json   per pipeline, one value per hourly run: duration, rows in and out, errors, disk, memory
    changes.json   deploys, config and infrastructure changes, notices

The knowledge is shared by every incident: runbooks with section ids, and past post-mortems.
Every time is UTC.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .textsearch import BM25

LAYERS = ("source", "ingestion", "staging", "model", "mart", "export")
METRICS = ("duration", "rows_in", "rows_out", "errors", "disk", "memory")
LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")


def parse_time(text: str, day: date | None = None) -> datetime | None:
    """Read "2026-03-14T02:05:00Z", "2026-03-14 02:05", "2026-03-14", or "02:05" on a given day."""
    value = str(text).strip().removesuffix("UTC").strip().removesuffix("Z")
    value = re.sub(r"^(\d{4}-\d{2}-\d{2})[T ]+", r"\1 ", value)
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC)
        except ValueError:
            pass
    if day is not None:
        for pattern in ("%H:%M:%S", "%H:%M"):
            try:
                clock = datetime.strptime(value, pattern)
            except ValueError:
                continue
            return datetime(day.year, day.month, day.day, clock.hour, clock.minute, clock.second, tzinfo=UTC)
    return None


def show_time(moment: datetime, seconds: bool = False) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


def _iso(text: str) -> datetime:
    moment = parse_time(text)
    if moment is None:
        raise ValueError(f"not a time: {text!r}")
    return moment


@dataclass(frozen=True)
class Pipeline:
    name: str
    layer: str
    owner: str
    schedule: str
    reads: str
    writes: str
    host: str = ""


class Platform:
    """The pipelines and the lineage graph between them."""

    def __init__(self, pipelines: list[Pipeline], edges: list[tuple[str, str]]):
        self.pipelines = {pipeline.name: pipeline for pipeline in pipelines}
        self.names = [pipeline.name for pipeline in pipelines]
        self._order = {name: index for index, name in enumerate(self.names)}
        self._inputs: dict[str, list[str]] = {name: [] for name in self.names}
        self._outputs: dict[str, list[str]] = {name: [] for name in self.names}
        for upstream, downstream in edges:
            self._outputs[upstream].append(downstream)
            self._inputs[downstream].append(upstream)
        for table in (self._inputs, self._outputs):
            for name in table:
                table[name].sort(key=self._order.__getitem__)
        # Not after a dot: analytics.fct_orders is a table, not the pipeline fct_orders.
        self._pattern = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, self.names)) + r")(?![\w])")

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> Platform:
        pipelines = [Pipeline(**item) for item in data["pipelines"]]
        return cls(pipelines, [(a, b) for a, b in data["edges"]])

    @classmethod
    def load(cls, path: str | Path) -> Platform:
        return cls.from_data(json.loads(Path(path).read_text(encoding="utf-8")))

    def __contains__(self, name: object) -> bool:
        return name in self.pipelines

    def inputs(self, name: str) -> list[str]:
        return list(self._inputs[name])

    def outputs(self, name: str) -> list[str]:
        return list(self._outputs[name])

    def _walk(self, name: str, step: dict[str, list[str]]) -> list[tuple[str, int]]:
        distance = {name: 0}
        queue = deque([name])
        while queue:
            current = queue.popleft()
            for neighbour in step[current]:
                if neighbour not in distance:
                    distance[neighbour] = distance[current] + 1
                    queue.append(neighbour)
        del distance[name]
        return sorted(distance.items(), key=lambda item: (item[1], self._order[item[0]]))

    def upstream(self, name: str) -> list[tuple[str, int]]:
        """Every pipeline that feeds this one, directly or not, nearest first."""
        return self._walk(name, self._inputs)

    def downstream(self, name: str) -> list[tuple[str, int]]:
        """Every pipeline this one feeds, directly or not, nearest first."""
        return self._walk(name, self._outputs)

    def is_upstream(self, candidate: str, of: str) -> bool:
        if candidate not in self or of not in self:
            return False
        return any(name == candidate for name, _ in self.upstream(of))

    def mentioned(self, text: str) -> set[str]:
        """The pipeline names that appear in a text as whole words, not as part of a table name."""
        return set(self._pattern.findall(text))


@dataclass(frozen=True)
class Alert:
    id: str
    fired_at: datetime
    pipeline: str
    check: str
    severity: str
    message: str
    window_start: datetime

    def text(self) -> str:
        window = f"{show_time(self.window_start)} to {self.fired_at:%H:%M} UTC"
        if self.window_start.date() != self.fired_at.date():
            window = f"{show_time(self.window_start)} to {show_time(self.fired_at)} UTC"
        return (
            f"[ALERT] {self.id} fired {show_time(self.fired_at)} UTC on {self.pipeline} "
            f"(check: {self.check}, severity: {self.severity}): {self.message}. "
            f"Logs and metrics are available for the incident window {window}."
        )


@dataclass(frozen=True)
class LogLine:
    id: str
    time: datetime
    level: str
    pipeline: str
    message: str

    def text(self) -> str:
        return f"[{self.id}] {show_time(self.time, seconds=True)} {self.level} {self.pipeline}: {self.message}"


@dataclass
class Runs:
    """One pipeline's run history: a value per run for every metric, and how each run ended."""

    pipeline: str
    first_run: datetime
    step: timedelta
    values: dict[str, list[int]]
    status: list[str]

    def times(self) -> list[datetime]:
        return [self.first_run + self.step * index for index in range(len(self.status))]


@dataclass(frozen=True)
class Change:
    id: str
    time: datetime
    kind: str
    target: str
    author: str
    summary: str

    def text(self) -> str:
        return f"[{self.id}] {show_time(self.time)} {self.kind} {self.target} by {self.author}: {self.summary}"


def parse_logs(text: str) -> list[LogLine]:
    lines = []
    for row in text.splitlines():
        if not row.strip():
            continue
        line_id, moment, level, pipeline, message = row.split(" ", 4)
        lines.append(LogLine(line_id, _iso(moment), level, pipeline, message))
    return lines


@dataclass
class Incident:
    name: str
    alert: Alert
    logs: list[LogLine]
    runs: dict[str, Runs]
    changes: list[Change]

    @property
    def window_start(self) -> datetime:
        return self.alert.window_start

    @property
    def window_end(self) -> datetime:
        return self.alert.fired_at

    @classmethod
    def from_data(
        cls, name: str, alert: dict[str, Any], logs: str, metrics: dict[str, Any], changes: list[dict[str, Any]]
    ) -> Incident:
        step = timedelta(minutes=metrics["step_minutes"])
        runs = {}
        for pipeline, data in metrics["pipelines"].items():
            values = {metric: list(data[metric]) for metric in METRICS}
            runs[pipeline] = Runs(pipeline, _iso(data["first_run"]), step, values, list(data["status"]))
        return cls(
            name=name,
            alert=Alert(
                id=alert["id"],
                fired_at=_iso(alert["fired_at"]),
                pipeline=alert["pipeline"],
                check=alert["check"],
                severity=alert["severity"],
                message=alert["message"],
                window_start=_iso(alert["window_start"]),
            ),
            logs=parse_logs(logs),
            runs=runs,
            changes=[
                Change(c["id"], _iso(c["time"]), c["kind"], c["target"], c["author"], c["summary"]) for c in changes
            ],
        )

    @classmethod
    def load(cls, folder: str | Path) -> Incident:
        path = Path(folder)

        def read(name: str) -> str:
            return (path / name).read_text(encoding="utf-8")

        return cls.from_data(
            path.name,
            json.loads(read("alert.json")),
            read("logs.txt"),
            json.loads(read("metrics.json")),
            json.loads(read("changes.json")),
        )


@dataclass(frozen=True)
class Section:
    """One section of a runbook."""

    id: str
    runbook: str
    title: str
    body: str

    def text(self) -> str:
        return f"[{self.id}] {self.runbook}: {self.title}. {self.body}"


@dataclass(frozen=True)
class PostMortem:
    id: str
    date: str
    title: str
    category: str
    pipelines: tuple[str, ...]
    summary: str
    root_cause: str
    resolution: str
    follow_ups: str

    def text(self) -> str:
        return (
            f"[{self.id}] {self.date}, {self.category.replace('_', ' ')}: {self.title}. "
            f"Pipelines: {', '.join(self.pipelines)}. What happened: {self.summary} Root cause: {self.root_cause} "
            f"Resolution: {self.resolution} Follow-up: {self.follow_ups}"
        )


class Knowledge:
    """Runbooks and past post-mortems, each searchable with BM25."""

    def __init__(self, sections: list[Section], postmortems: list[PostMortem]):
        self.sections = sections
        self.postmortems = postmortems
        self._sections = {section.id: section for section in sections}
        self._postmortems = {postmortem.id: postmortem for postmortem in postmortems}
        self._section_index = BM25({section.id: section.text() for section in sections})
        self._postmortem_index = BM25({postmortem.id: postmortem.text() for postmortem in postmortems})

    @classmethod
    def from_data(cls, runbooks: list[dict[str, Any]], postmortems: list[dict[str, Any]]) -> Knowledge:
        sections = [
            Section(section["id"], runbook["title"], section["title"], section["text"])
            for runbook in runbooks
            for section in runbook["sections"]
        ]
        return cls(sections, [PostMortem(**{**item, "pipelines": tuple(item["pipelines"])}) for item in postmortems])

    @classmethod
    def load(cls, shared: str | Path) -> Knowledge:
        folder = Path(shared)
        runbooks = json.loads((folder / "runbooks.json").read_text(encoding="utf-8"))
        postmortems = json.loads((folder / "postmortems.json").read_text(encoding="utf-8"))
        return cls.from_data(runbooks, postmortems)

    def search_runbooks(self, query: str, k: int = 3) -> list[Section]:
        return [self._sections[doc_id] for doc_id, _ in self._section_index.search(query, k)]

    def search_postmortems(self, query: str, k: int = 3) -> list[PostMortem]:
        return [self._postmortems[doc_id] for doc_id, _ in self._postmortem_index.search(query, k)]
