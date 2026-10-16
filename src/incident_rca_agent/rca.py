"""The one structured call that writes the RCA from what the investigation retrieved.

It is a separate call from the tool loop: a JSON schema and no tools. The schema only allows the ten
failure types (or unknown), pipelines from the lineage graph, and source ids the investigation was
actually shown. The evidence is given as one transcript of every tool call and its result, so the
answer depends on the evidence, not on how a chat template renders tool messages.
"""

from __future__ import annotations

import json
from typing import Any

from .categories import CATEGORIES, CATEGORY_IDS, UNKNOWN
from .investigate import Investigation, ToolUse
from .llm import LLM, Reply
from .schema import validate
from .tools import TOOLS

_TYPES = "\n".join(f"  - {category}: {description}" for category, (_, description) in CATEGORIES.items())

SYSTEM = f"""You write the root-cause analysis (RCA) of a data-platform alert, from the evidence an \
investigation retrieved. Answer with JSON only.

Fields, in this order:
- claims: 3 to 6 facts from the evidence that show the cause. Each claim is one short plain sentence, without \
markdown, with source_ids: the ids in square brackets of the sources it comes from (for example L-0142, M-1, \
CHG-4410). Copy pipeline names, numbers and times exactly as they appear in the cited sources, and never \
compute, round or convert a number (no percentages or durations of your own): a claim with a name or number \
that is not in its sources does not count. Include the warning or error line of the root-cause pipeline that \
shows the error itself (not a line where a pipeline skips or waits for upstream data), and a metric summary or \
a change of that pipeline.
- root_cause_category: one of the failure types below, or unknown when the evidence does not show the cause.
{_TYPES}
- root_cause_pipeline: the upstream-most pipeline where the failure starts. Name the alerting pipeline only when \
the failure starts there. Pipelines that skipped runs or waited for upstream data are symptoms, not causes.
- summary: two or three plain sentences for the on-call channel, without markdown: what broke, where, and what \
it caused.
- affected_pipelines: the root-cause pipeline and the pipelines downstream of it that were hurt, up to the \
alerting pipeline.
- actions: 2 to 4 next steps for the engineer, one short sentence each. Each has text and source_ids, citing \
the runbook section or past incident it follows when there is one."""


def rca_schema(pipelines: list[str], source_ids: list[str]) -> dict[str, Any]:
    citations: dict[str, Any] = {"type": "string", "enum": list(source_ids)} if source_ids else {"type": "string"}
    item = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "source_ids": {"type": "array", "items": citations}},
        "required": ["text", "source_ids"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "claims": {"type": "array", "items": item, "maxItems": 6},
            "root_cause_category": {"type": "string", "enum": list(CATEGORY_IDS)},
            "root_cause_pipeline": {"type": "string", "enum": [*pipelines, UNKNOWN]},
            "summary": {"type": "string"},
            "affected_pipelines": {
                "type": "array",
                "items": {"type": "string", "enum": list(pipelines)},
                "maxItems": 12,
            },
            "actions": {"type": "array", "items": item, "maxItems": 4},
        },
        "required": [
            "claims",
            "root_cause_category",
            "root_cause_pipeline",
            "summary",
            "affected_pipelines",
            "actions",
        ],
        "additionalProperties": False,
    }


_PARAMETERS = {tool["function"]["name"]: list(tool["function"]["parameters"]["properties"]) for tool in TOOLS}


def call_text(use: ToolUse) -> str:
    """The call as text, arguments in the tool's own order: a stored run keeps them with sorted keys."""
    if isinstance(use.arguments, dict):
        order = _PARAMETERS.get(use.name, [])
        keys = sorted(use.arguments, key=lambda key: (order.index(key) if key in order else len(order), key))
        shown = ", ".join(f"{key}={json.dumps(use.arguments[key], ensure_ascii=False)}" for key in keys)
    else:
        shown = str(use.arguments)
    return f"{use.name}({shown})"


def transcript(investigation: Investigation) -> str:
    return "\n\n".join(
        f"{number}. {call_text(use)}\n{use.result}" for number, use in enumerate(investigation.uses, start=1)
    )


def build_messages(investigation: Investigation) -> list[dict[str, str]]:
    user = (
        "The investigation of this alert made these tool calls, in this order. "
        "Cite the ids in square brackets.\n\n"
        f"{transcript(investigation)}\n\nWrite the RCA as JSON."
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def write_rca(llm: LLM, investigation: Investigation, pipelines: list[str], label: str) -> Reply:
    schema = rca_schema(pipelines, investigation.source_ids())
    return llm.structured(
        build_messages(investigation),
        schema=schema,
        validate=lambda data: validate(data, schema),
        label=f"{label} rca",
    )
