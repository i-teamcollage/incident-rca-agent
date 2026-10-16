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
single word DONE: the RCA is written in the next step, from the evidence the tools returned.

Answer with tool calls only. Do not write a plan, an explanation or a summary: nobody reads it, and the RCA \
step sees only the tool results."""


KNOWLEDGE_TOOLS = ("search_runbooks", "search_past_incidents")  # about other days, not this incident
EVIDENCE_TOOLS = ("search_logs", "get_metric_summary")  # what the confidence line needs to see


def reminder(calls_left: int) -> str:
    """Sent once when the model stops before it has searched the logs and read a metric summary."""
    return (
        f"You have {calls_left} tool calls left. Before DONE, use search_logs with level WARN and "
        "get_metric_summary with metric all on the pipeline where the failure starts: the RCA needs its warning or "
        "error line and its metric summary."
    )


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
    reminded = False
    while len(found.uses) < MAX_TOOL_CALLS:
        reply = llm.chat(messages, tools=TOOLS, label=f"{label} step {found.model_calls + 1}")
        found.model_calls += 1
        found.seconds += reply.seconds
        if not reply.tool_calls:
            missing = [name for name in EVIDENCE_TOOLS if name not in {use.name for use in found.uses}]
            if missing and not reminded:
                reminded = True
                messages.append({"role": "assistant", "content": reply.text})
                messages.append({"role": "user", "content": reminder(MAX_TOOL_CALLS - len(found.uses))})
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
