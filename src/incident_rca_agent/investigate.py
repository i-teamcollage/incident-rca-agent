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

You can make at most {MAX_TOOL_CALLS - 1} more tool calls. A good order: get_lineage upstream of the alerting \
pipeline; search_logs on the suspect pipeline with level WARN; get_metric_summary on the suspect pipeline; \
list_changes; then search_runbooks and search_past_incidents with the error message you found. Look at the \
pipeline where the trouble starts, not only where it shows. When you know the cause, stop calling tools and \
reply with the single word DONE: the RCA is written in the next step, from the evidence the tools returned.

Answer with tool calls only. Do not write a plan, an explanation or a summary: nobody reads it, and the RCA \
step sees only the tool results."""


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


def investigate(llm: LLM, session: Session, label: str) -> Investigation:
    alert = session.run("get_alert", {})
    found = Investigation(uses=[ToolUse("get_alert", {}, alert.text, alert.source_ids, by_model=False)])
    opening = (
        f"An alert fired. Here it is, from get_alert:\n{alert.text}\n\n"
        f"Find the root cause with the tools. You have {MAX_TOOL_CALLS - 1} tool calls."
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": opening}]
    while len(found.uses) < MAX_TOOL_CALLS:
        reply = llm.chat(messages, tools=TOOLS, label=f"{label} step {found.model_calls + 1}")
        found.model_calls += 1
        found.seconds += reply.seconds
        if not reply.tool_calls:
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
