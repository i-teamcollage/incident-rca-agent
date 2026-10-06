"""The confidence line: post an RCA as confident, or say it needs an engineer.

Posted as confident only when every check passes:

- the category is one of the ten failure types, not unknown;
- at least 80% of the claims are grounded;
- the root-cause pipeline is the alerting pipeline or upstream of it in the lineage graph;
- at least two different kinds of source support the cause: a log line, and a metric summary or a change.

A source supports the cause only when a grounded claim cites it and it shows a problem at the root-cause
pipeline itself:

- a log line: a warning or an error of that pipeline that carries the signature of the failure type the
  model named (HTTP 429 for a rate limit, exit code 137 for out of memory). A line in which the pipeline
  skips, waits for or blames one of its upstream pipelines is a symptom of that upstream pipeline, so it
  supports no cause here;
- a metric summary of that pipeline with a change point;
- a change that names that pipeline.

The log line is required because it is the only kind of source that says what failed. A dropped row count
and a deploy on the same pipeline look like a cause, but a pipeline that waits for upstream data shows the
same drop. Otherwise the RCA goes out as "needs an engineer", with what was found and these reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .categories import UNKNOWN, category_name, shows
from .grounding import ClaimCheck
from .incident import Platform
from .tools import Source

GROUNDED_SHARE = 0.8


@dataclass
class Verdict:
    confident: bool
    reasons: list[str] = field(default_factory=list)
    grounded: int = 0
    claims: int = 0
    supporting_kinds: list[str] = field(default_factory=list)


def blames_upstream(source: Source, platform: Platform) -> bool:
    """Does this text name a pipeline upstream of its own pipeline? Then it shows a symptom of that one."""
    named = platform.mentioned(source.text) - source.pipelines
    return any(platform.is_upstream(name, own) for name in sorted(named) for own in sorted(source.pipelines))


def supports(source: Source, category: str, pipeline: str, platform: Platform) -> bool:
    if not source.abnormal or pipeline not in source.pipelines:
        return False
    if source.kind == "log":
        return shows(category, source.text) and not blames_upstream(source, platform)
    return source.kind in ("metric", "change")


def supporting_kinds(
    category: str, pipeline: str, claims: list[ClaimCheck], sources: dict[str, Source], platform: Platform
) -> list[str]:
    kinds = set()
    for claim in claims:
        if not claim.grounded:
            continue
        for source_id in claim.source_ids:
            source = sources.get(source_id)
            if source is not None and supports(source, category, pipeline, platform):
                kinds.add(source.kind)
    return sorted(kinds)


def decide(
    category: str,
    pipeline: str,
    claims: list[ClaimCheck],
    sources: dict[str, Source],
    platform: Platform,
    alert_pipeline: str,
) -> Verdict:
    reasons = []
    if category == UNKNOWN:
        reasons.append("the cause is unknown")
    grounded = sum(claim.grounded for claim in claims)
    if not claims:
        reasons.append("the RCA makes no claims")
    elif grounded < GROUNDED_SHARE * len(claims) - 1e-9:
        reasons.append(f"only {grounded} of {len(claims)} claims are grounded ({GROUNDED_SHARE:.0%} needed)")
    if pipeline == UNKNOWN:
        reasons.append("no root-cause pipeline was named")
    elif pipeline not in platform:
        reasons.append(f"{pipeline} is not a pipeline in the lineage graph")
    elif pipeline != alert_pipeline and not platform.is_upstream(pipeline, alert_pipeline):
        reasons.append(f"{pipeline} is not upstream of the alerting pipeline {alert_pipeline}")
    kinds = supporting_kinds(category, pipeline, claims, sources, platform)
    if category != UNKNOWN and pipeline in platform:
        if "log" not in kinds:
            reasons.append(f"no cited warning or error from {pipeline} shows {category_name(category)}")
        if not set(kinds) - {"log"}:
            reasons.append(f"no cited metric summary or change about {pipeline} backs the cause up")
    return Verdict(not reasons, reasons, grounded, len(claims), kinds)
