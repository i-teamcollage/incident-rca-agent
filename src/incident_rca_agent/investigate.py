"""The tool loop: the model investigates the alert with at most eight tool calls.

The code makes the first call itself (get_alert) so the model starts from the alert. The model then asks
for tools until it has enough evidence or the eighth call is used. The loop only gathers evidence; the
RCA is written afterwards in a separate structured call (rca.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .categories import CATEGORIES
from .llm import LLM
from .tool_messages import assistant_message, tool_result_message
from .tools import TOOLS, Session

MAX_TOOL_CALLS = 8

_TYPES = "\n".join(f"- {category}: {description}" for category, (_, description) in CATEGORIES.items())

SYSTEM = f"""You investigate alerts on a data platform of 30 pipelines. Data flows through layers: \
source (src_) -> ingestion (ing_) -> staging (stg_) -> model (dim_, fct_) -> mart (mart_) -> export (exp_).

Find the root cause of the alert:
- the root-cause pipeline: the upstream-most pipeline where the failure starts. Alerts usually fire on a \
downstream symptom, so the alerting pipeline is often not the root cause. Downstream pipelines that skip runs or \
wait for data are symptoms.
- the failure type, one of:
{_TYPES}

How to investigate, with at most {MAX_TOOL_CALLS - 1} more tool calls:
1. get_lineage upstream of the alerting pipeline. It shows how each pipeline's runs went. A pipeline skips its \
runs when an input failed or sent no data, so follow the skipped runs upstream: the suspect is the pipeline at \
the top of that chain, the one whose runs failed (it can be the alerting pipeline itself). When two chains are \
possible, follow the one with as many bad runs as the alerting pipeline. When no run failed or was skipped, \
start from the top of the lineage.
2. search_logs on the suspect with level WARN: it shows warnings and errors, and many failures start as a warning.
3. get_metric_summary on the suspect with metric all.
4. When the suspect's warnings only say that it waits for, skips because of, or got bad data from another \
pipeline, that pipeline is the new suspect: do steps 2 and 3 for it.
5. list_changes, then search_runbooks with the error message you found.
Go to the suspect directly instead of looking at every pipeline in between. When you have the warning or error \
line and the metric summary of the pipeline where the failure starts, stop calling tools and reply with the \
single word DONE (DONE is plain text, not a tool): the RCA is written in the next step, from the evidence the \
tools returned.

Answer with tool calls only. Do not write a plan, an explanation or a summary: nobody reads it, and the RCA \
step sees only the tool results."""


KNOWLEDGE_TOOLS = ("search_runbooks", "search_past_incidents")  # about other days, not this incident
# What the confidence line needs to see, how to ask for it, and what it gives the RCA.
EVIDENCE_TOOLS = {
    "search_logs": ("search_logs with level WARN", "its warning or error line"),
    "get_metric_summary": ("get_metric_summary with metric all", "its metric summary"),
}
LOW_ON_CALLS = 2


def _pipeline_of(arguments: Any) -> str:
    value = arguments.get("pipeline", "") if isinstance(arguments, dict) else ""
    return str(value or "").strip().strip("`'\"").lower()


def missing_evidence(investigation: Investigation) -> tuple[str, list[str]]:
    """The pipeline the model looked at last, and the evidence tools it has not used on that pipeline yet.

    The confidence line needs a log line and a metric summary (or a change) of one pipeline, the root cause.
    The code does not know which pipeline that is; the last one the model looked into is its own best lead.
    """
    looked = [
        (use.name, _pipeline_of(use.arguments))
        for use in investigation.uses
        if use.name in EVIDENCE_TOOLS and _pipeline_of(use.arguments)
    ]
    if not looked:
        return "", list(EVIDENCE_TOOLS)
    last = looked[-1][1]
    used = {name for name, pipeline in looked if pipeline == last}
    return last, [name for name in EVIDENCE_TOOLS if name not in used]


def reminder(calls_left: int, pipeline: str, missing: list[str]) -> str:
    """Sent once per pipeline, when the model stops or is down to its last calls without the tools in missing."""
    calls = f"{calls_left} tool call{'' if calls_left == 1 else 's'}"
    use = " and ".join(EVIDENCE_TOOLS[name][0] for name in missing)
    needs = " and ".join(EVIDENCE_TOOLS[name][1] for name in missing)
    where = "on the pipeline where the failure starts"
    if pipeline:
        where = f"on {pipeline}, if that is where the failure starts"
    return f"You have {calls} left. Before DONE, use {use} {where}: the RCA needs {needs}."


@dataclass
class ToolUse:
    name: str
    arguments: Any
    result: str
    source_ids: list[str]
    by_model: bool = True


@dataclass
class Investigation:
    uses: list[ToolUse] = field(default_factory=list)
    seconds: float = 0.0
    model_calls: int = 0
    stopped: str = ""
    skipped: int = 0  # calls asked for after the limit, not run

    def source_ids(self) -> list[str]:
        """Every source id the investigation was shown, in order, each once."""
        return list(dict.fromkeys(source_id for use in self.uses for source_id in use.source_ids))

    def evidence_ids(self) -> list[str]:
        """The ids of the incident's own evidence: everything but runbook sections and past incidents."""
        uses = [use for use in self.uses if use.name not in KNOWLEDGE_TOOLS]
        return list(dict.fromkeys(source_id for use in uses for source_id in use.source_ids))


def investigate(llm: LLM, session: Session, label: str) -> Investigation:
    alert = session.run("get_alert", {})
    found = Investigation(uses=[ToolUse("get_alert", {}, alert.text, alert.source_ids, by_model=False)])
    opening = (
        f"An alert fired. Here it is, from get_alert:\n{alert.text}\n\n"
        f"Find the root cause with the tools. You have {MAX_TOOL_CALLS - 1} tool calls."
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": opening}]
    reminded: set[str] = set()  # one reminder per pipeline ("" before the model has looked at any)
    while len(found.uses) < MAX_TOOL_CALLS:
        calls_left, (pipeline, missing) = MAX_TOOL_CALLS - len(found.uses), missing_evidence(found)
        if missing and pipeline not in reminded and calls_left <= LOW_ON_CALLS:
            reminded.add(pipeline)
            messages.append({"role": "user", "content": reminder(calls_left, pipeline, missing)})
        reply = llm.chat(messages, tools=TOOLS, label=f"{label} step {found.model_calls + 1}")
        found.model_calls += 1
        found.seconds += reply.seconds
        if not reply.tool_calls:
            if missing and pipeline not in reminded:
                reminded.add(pipeline)
                messages.append({"role": "assistant", "content": reply.text})
                messages.append({"role": "user", "content": reminder(calls_left, pipeline, missing)})
                continue
            found.stopped = "the model had enough evidence"
            break
        messages.append(assistant_message(reply))
        for call in reply.tool_calls:
            if len(found.uses) >= MAX_TOOL_CALLS:
                found.skipped += 1
                continue
            result = session.run(call.name, call.arguments)
            found.uses.append(ToolUse(call.name, call.arguments, result.text, result.source_ids))
            messages.append(tool_result_message(call.name, result.text))
    else:
        found.stopped = f"the limit of {MAX_TOOL_CALLS} tool calls was reached"
    found.seconds = round(found.seconds, 3)
    return found
