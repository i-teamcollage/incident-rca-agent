"""Score the RCAs against the answer key, using the rules in the spec.

- rca_accuracy: the category and the pipeline are both right, over every incident. A "needs an engineer"
  result counts with its best guess.
- confident_precision: of the RCAs posted as confident, the share that are right.
- grounding: of all claims in all RCAs, the share that pass the grounding check.

Also reported, without thresholds: the share posted as confident, tool calls per incident and minutes per
incident.
"""

from __future__ import annotations

import statistics
from collections import Counter

from .acceptance import Measure, rate
from .categories import category_name
from .pipeline import IncidentResult


def rca_correct(label: dict, result: IncidentResult) -> bool:
    return result.category == label["category"] and result.pipeline == label["root_cause_pipeline"]


def _share(hits: int, total: int) -> str:
    return f"{hits / total * 100:.1f}%" if total else "n/a"


def _number(value: float) -> str:
    return f"{value:.1f}".removesuffix(".0")


def score(labels: list[dict], results: dict[str, IncidentResult]) -> tuple[dict[str, Measure], str]:
    correct = confident = confident_correct = grounded = claims = unreadable = 0
    per_type_total: Counter[str] = Counter()
    per_type_correct: Counter[str] = Counter()
    rows, herrings = [], []
    for label in labels:
        result = results[label["id"]]
        right = rca_correct(label, result)
        correct += right
        confident += result.confident
        confident_correct += result.confident and right
        grounded += sum(claim.grounded for claim in result.claims)
        claims += len(result.claims)
        unreadable += result.error is not None
        per_type_total[label["category"]] += 1
        per_type_correct[label["category"]] += right
        expected = f"{category_name(label['category'])} in {label['root_cause_pipeline']}"
        answered = f"{category_name(result.category)} in {result.pipeline}"
        rows.append(
            f"| {label['id']} | {expected} | {answered} | {'correct' if right else 'wrong'} | {result.status} "
            f"| {result.verdict.grounded} of {result.verdict.claims} | {result.tool_calls} |"
        )
        if label.get("red_herring"):
            herring = label["red_herring"].replace("_", " ")
            herrings.append(f"- {label['id']} ({herring}): {'correct' if right else 'wrong'}, {result.status}")

    measures = {
        "rca_accuracy": rate(correct, len(labels)),
        "confident_precision": rate(confident_correct, confident),
        "grounding": rate(grounded, claims),
    }
    calls = [results[label["id"]].tool_calls for label in labels]
    minutes = [results[label["id"]].seconds / 60 for label in labels]
    lines = [
        "## Informational, no threshold",
        "",
        f"- Posted as confident: {confident} of {len(labels)} ({_share(confident, len(labels))})",
        f"- Tool calls per incident: median {_number(statistics.median(calls))}, mean "
        f"{_number(statistics.mean(calls))}, max {max(calls)} (the first call, get_alert, is made by the code)",
        f"- Minutes per incident: median {_number(statistics.median(minutes))}, max {_number(max(minutes))}, from the "
        "recorded run on the machine named above, without a graphics card (the client build's 30 to 60 seconds were "
        "measured on server hardware)",
        f"- Claims grounded: {grounded} of {claims}",
        f"- RCAs that could not be read, even after a retry: {unreadable}",
        "",
        "## Per failure type",
        "",
        "| Failure type | Incidents | Root cause correct |",
        "|---|---|---|",
        *(
            f"| {category_name(category)} | {per_type_total[category]} | {per_type_correct[category]} |"
            for category in sorted(per_type_total)
        ),
        "",
        "## Per incident",
        "",
        "| Incident | Answer key | Agent | Result | Posted as | Claims grounded | Tool calls |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## Incidents with a red herring",
        "",
        *(herrings or ["None in this split."]),
    ]
    return measures, "\n".join(lines)
