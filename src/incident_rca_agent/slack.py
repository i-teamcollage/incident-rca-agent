"""The RCA as a Slack message (Block Kit JSON) and as a Markdown preview.

Nothing is sent: the message is written to out/<incident>.slack.json, ready for a Slack app or webhook.
A confident RCA leads with the root cause. Any other RCA leads with "Needs an engineer", then the best
guess, why it is not confident, and what was found.
"""

from __future__ import annotations

from typing import Any

from .categories import category_name
from .grounding import ClaimCheck
from .incident import show_time
from .pipeline import IncidentResult

HEADER_LIMIT = 150
SECTION_LIMIT = 3000
BULLET = chr(0x2022)
FOOTER = "incident-rca-agent, a sample on made-up data"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 4].rstrip() + " ..."


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _headline(result: IncidentResult) -> str:
    if result.confident:
        return f"Root cause: {category_name(result.category)} in {result.pipeline}"
    return f"Needs an engineer: {result.alert.check} alert on {result.alert.pipeline}"


def _best_guess(result: IncidentResult) -> str:
    if result.category == "unknown" and result.pipeline == "unknown":
        return "none"
    return f"{category_name(result.category)} in `{result.pipeline}`"


def _cited(item: ClaimCheck, code: str) -> str:
    ids = " ".join(f"{code}{source_id}{code}" for source_id in item.source_ids)
    unverified = "" if item.grounded else " _(not verified)_"
    return f"{ids}{unverified}".strip()


def _verdict_line(result: IncidentResult) -> str:
    verdict = result.verdict
    grounded = f"{verdict.grounded} of {verdict.claims} claims grounded"
    if verdict.confident:
        return f"Confident: {grounded}; the cause is supported by {_and(verdict.supporting_kinds)} sources."
    return f"Needs an engineer: {grounded}."


def _alert_line(result: IncidentResult, bold: str) -> str:
    alert = result.alert
    return (
        f"{bold}Alert{bold} {alert.id} on `{alert.pipeline}` ({alert.check}), fired {show_time(alert.fired_at)} UTC: "
        f"{alert.message}"
    )


def block_kit(result: IncidentResult) -> dict[str, Any]:
    def section(text: str) -> dict[str, Any]:
        return {"type": "section", "text": {"type": "mrkdwn", "text": _fit(text, SECTION_LIMIT)}}

    def items(entries: list[ClaimCheck], numbered: bool = False) -> str:
        rows = []
        for number, entry in enumerate(entries, start=1):
            mark = f"{number}." if numbered else BULLET
            rows.append(f"{mark} {_fit(_escape(entry.text), 600)}  {_cited(entry, '`')}".rstrip())
        return "\n".join(rows)

    headline = _headline(result)
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": _fit(headline, HEADER_LIMIT)}},
        section(_escape(_alert_line(result, "*"))),
    ]
    summary = _escape(result.summary) if result.summary else ""
    if result.confident:
        if summary:
            blocks.append(section(summary))
        blocks.append(section("*Evidence*\n" + items(result.claims)))
    else:
        blocks.append(
            section(f"*Best guess, not confirmed*\n{_best_guess(result)}" + (f"\n{summary}" if summary else ""))
        )
        reasons = "\n".join(f"{BULLET} {_escape(reason)}" for reason in result.verdict.reasons)
        blocks.append(section(f"*Why it is not confident*\n{reasons}"))
        if result.claims:
            blocks.append(section("*What was found*\n" + items(result.claims)))
    if result.affected:
        blocks.append(section("*Affected pipelines*\n" + ", ".join(f"`{name}`" for name in result.affected)))
    if result.actions:
        blocks.append(section("*Next steps*\n" + items(result.actions, numbered=True)))
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"{_verdict_line(result)} {FOOTER}."}]})
    fallback = f"{headline} (alert {result.alert.id} on {result.alert.pipeline})"
    return {"text": fallback, "blocks": blocks}


def markdown(result: IncidentResult) -> str:
    def items(entries: list[ClaimCheck], numbered: bool = False) -> list[str]:
        rows = []
        for number, entry in enumerate(entries, start=1):
            mark = f"{number}." if numbered else "-"
            rows.append(f"{mark} {entry.text} {_cited(entry, '`')}".rstrip())
        return rows

    lines = [f"## {_headline(result)}", "", _alert_line(result, "**"), ""]
    if not result.confident:
        lines += [f"**Best guess, not confirmed:** {_best_guess(result)}", ""]
    if result.summary:
        lines += [result.summary, ""]
    if result.confident:
        lines += ["**Evidence**", "", *items(result.claims), ""]
    else:
        lines += ["**Why it is not confident**", "", *(f"- {reason}" for reason in result.verdict.reasons), ""]
        if result.claims:
            lines += ["**What was found**", "", *items(result.claims), ""]
    if result.affected:
        lines += ["**Affected pipelines:** " + ", ".join(f"`{name}`" for name in result.affected), ""]
    if result.actions:
        lines += ["**Next steps**", "", *items(result.actions, numbered=True), ""]
    lines.append(f"_{_verdict_line(result)} {FOOTER}._")
    return "\n".join(lines) + "\n"
