"""Acceptance test: compare measured results with thresholds written down before the build.

The thresholds live in acceptance.toml. Each criterion names a metric, an operator and a target.
This module turns measured values into a PASS/FAIL table, writes the reports, and keeps the
README's results block in sync with them.
"""

from __future__ import annotations

import json
import math
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

START = "<!-- results:start -->"
END = "<!-- results:end -->"

_CHECKS = {
    ">=": lambda value, target: value >= target - 1e-12,
    "<=": lambda value, target: value <= target + 1e-12,
    "==": lambda value, target: abs(value - target) < 1e-9,
}
_SYMBOLS = {">=": "≥", "<=": "≤", "==": "="}


@dataclass(frozen=True)
class Criterion:
    id: str
    label: str
    metric: str
    op: str
    target: float
    unit: str = "rate"  # "rate" is shown as a percentage, "count" as a whole number


@dataclass(frozen=True)
class Measure:
    value: float
    hits: int | None = None
    total: int | None = None


@dataclass(frozen=True)
class Outcome:
    criterion: Criterion
    measure: Measure
    passed: bool
    interval: tuple[float, float] | None


def rate(hits: int, total: int) -> Measure:
    """A share of items, for example correct fields out of accepted fields."""
    value = hits / total if total else math.nan
    return Measure(value=value, hits=hits, total=total)


def count(n: int) -> Measure:
    """A plain count, for example tickets that were wrongly closed."""
    return Measure(value=float(n))


def wilson(hits: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a share. Honest about small test sets."""
    if total == 0:
        return None
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def load_criteria(path: str | Path) -> list[Criterion]:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    criteria = [Criterion(**item) for item in data["criteria"]]
    for criterion in criteria:
        if criterion.op not in _CHECKS:
            raise ValueError(f"{criterion.id}: unknown operator {criterion.op!r}, use one of {sorted(_CHECKS)}")
    return criteria


def evaluate(criteria: list[Criterion], measures: dict[str, Measure]) -> list[Outcome]:
    outcomes = []
    for criterion in criteria:
        if criterion.metric not in measures:
            raise KeyError(f"no measured value for metric {criterion.metric!r}")
        measure = measures[criterion.metric]
        passed = not math.isnan(measure.value) and _CHECKS[criterion.op](measure.value, criterion.target)
        interval = wilson(measure.hits, measure.total) if measure.total is not None else None
        outcomes.append(Outcome(criterion, measure, passed, interval))
    return outcomes


def all_passed(outcomes: list[Outcome]) -> bool:
    return all(outcome.passed for outcome in outcomes)


def _percent(value: float) -> str:
    text = f"{value * 100:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text}%"


def format_target(criterion: Criterion) -> str:
    symbol = _SYMBOLS[criterion.op]
    if criterion.unit == "count":
        return f"{symbol} {criterion.target:g}"
    return f"{symbol} {_percent(criterion.target)}"


def format_value(criterion: Criterion, measure: Measure) -> str:
    if math.isnan(measure.value):
        return "n/a"
    if criterion.unit == "count":
        return f"{measure.value:g}"
    return f"{measure.value * 100:.1f}%"


def format_interval(outcome: Outcome) -> str:
    if outcome.interval is None:
        return ""
    low, high = outcome.interval
    return f"{low * 100:.1f}–{high * 100:.1f}%"


def _result(outcome: Outcome) -> str:
    return "PASS" if outcome.passed else "FAIL"


def markdown_table(outcomes: list[Outcome]) -> str:
    lines = [
        "| Criterion | Target | Measured | 95% interval | Result |",
        "|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        criterion = outcome.criterion
        lines.append(
            f"| {criterion.label} | {format_target(criterion)} | {format_value(criterion, outcome.measure)} "
            f"| {format_interval(outcome)} | {_result(outcome)} |"
        )
    return "\n".join(lines)


def terminal_table(outcomes: list[Outcome]) -> str:
    width = max(len(outcome.criterion.label) for outcome in outcomes)
    rows = []
    for outcome in outcomes:
        criterion = outcome.criterion
        rows.append(
            f"  {_result(outcome):4}  {criterion.label:<{width}}  "
            f"{format_value(criterion, outcome.measure):>7}  (target {format_target(criterion)})"
        )
    return "\n".join(rows)


def report_dict(outcomes: list[Outcome], meta: dict[str, Any]) -> dict[str, Any]:
    criteria = []
    for outcome in outcomes:
        entry = asdict(outcome.criterion)
        entry.update(
            value=None if math.isnan(outcome.measure.value) else outcome.measure.value,
            hits=outcome.measure.hits,
            total=outcome.measure.total,
            interval=list(outcome.interval) if outcome.interval else None,
            passed=outcome.passed,
        )
        criteria.append(entry)
    return {**meta, "criteria": criteria, "passed": all_passed(outcomes)}


def outcomes_from_report(report: dict[str, Any]) -> list[Outcome]:
    outcomes = []
    for entry in report["criteria"]:
        criterion = Criterion(
            id=entry["id"],
            label=entry["label"],
            metric=entry["metric"],
            op=entry["op"],
            target=entry["target"],
            unit=entry["unit"],
        )
        value = math.nan if entry["value"] is None else entry["value"]
        measure = Measure(value=value, hits=entry["hits"], total=entry["total"])
        interval = tuple(entry["interval"]) if entry["interval"] else None
        outcomes.append(Outcome(criterion, measure, entry["passed"], interval))
    return outcomes


def write_reports(report: dict[str, Any], out_dir: str | Path, details_markdown: str = "") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "acceptance.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    verdict = "All criteria passed." if report["passed"] else "At least one criterion failed."
    body = [f"# {report.get('title', 'Acceptance test')}", "", results_block(report), "", verdict]
    if details_markdown:
        body += ["", details_markdown.strip()]
    (out / "acceptance.md").write_text("\n".join(body) + "\n", encoding="utf-8")


def results_block(report: dict[str, Any]) -> str:
    table = markdown_table(outcomes_from_report(report))
    facts = [f"{report['items']} made-up test cases"]
    if report.get("model"):
        facts.append(f"{report['model']} on {report.get('machine', 'a laptop')}")
    if report.get("recorded_at"):
        facts.append(f"recorded {report['recorded_at']}")
    if report.get("median_seconds_per_item") is not None:
        facts.append(f"median {report['median_seconds_per_item']:.1f} s per item")
    return f"{table}\n\n{', '.join(facts)}."


def update_readme(readme: str | Path, block: str, check: bool = False) -> bool:
    """Replace the README's results block. Returns True when the block differed."""
    path = Path(readme)
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise ValueError(f"{path} needs the markers {START} and {END}")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    new_text = f"{head}{START}\n{block}\n{END}{tail}"
    changed = new_text != text
    if changed and not check:
        path.write_text(new_text, encoding="utf-8")
    return changed
