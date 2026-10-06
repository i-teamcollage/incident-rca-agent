"""One incident from alert to RCA: the tool loop, one structured call, then the checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .categories import UNKNOWN
from .confidence import Verdict, decide
from .grounding import ClaimCheck, check_claim
from .incident import Alert, Incident, Knowledge, Platform, show_time
from .investigate import Investigation, investigate
from .llm import LLM, InvalidOutput
from .rca import write_rca
from .tools import Session


@dataclass
class IncidentResult:
    incident_id: str
    alert: Alert
    category: str
    pipeline: str
    summary: str
    claims: list[ClaimCheck]
    affected: list[str]
    actions: list[ClaimCheck]
    verdict: Verdict
    investigation: Investigation
    seconds: float
    error: str | None = None

    @property
    def confident(self) -> bool:
        return self.verdict.confident

    @property
    def status(self) -> str:
        return "confident" if self.verdict.confident else "needs an engineer"

    @property
    def tool_calls(self) -> int:
        return len(self.investigation.uses)


def analyse(incident_id: str, incident: Incident, llm: LLM, platform: Platform, knowledge: Knowledge) -> IncidentResult:
    session = Session(incident, platform, knowledge)
    investigation = investigate(llm, session, label=incident_id)
    try:
        reply = write_rca(llm, investigation, platform.names, label=incident_id)
    except InvalidOutput as error:
        verdict = Verdict(False, ["the model's RCA could not be read, even after a retry"])
        return IncidentResult(
            incident_id, incident.alert, UNKNOWN, UNKNOWN, "", [], [], [], verdict, investigation,
            investigation.seconds, str(error),
        )  # fmt: skip
    data = reply.data
    claims = [check_claim(item["text"], item["source_ids"], session.sources) for item in data["claims"]]
    actions = [check_claim(item["text"], item["source_ids"], session.sources) for item in data["actions"]]
    category, pipeline = data["root_cause_category"], data["root_cause_pipeline"]
    verdict = decide(category, pipeline, claims, session.sources, platform, incident.alert.pipeline)
    return IncidentResult(
        incident_id=incident_id,
        alert=incident.alert,
        category=category,
        pipeline=pipeline,
        summary=data["summary"],
        claims=claims,
        affected=list(dict.fromkeys(data["affected_pipelines"])),
        actions=actions,
        verdict=verdict,
        investigation=investigation,
        seconds=round(investigation.seconds + reply.seconds, 3),
    )


def _claim_record(claim: ClaimCheck) -> dict[str, Any]:
    return {"text": claim.text, "source_ids": claim.source_ids, "grounded": claim.grounded, "problems": claim.problems}


def as_record(result: IncidentResult) -> dict[str, Any]:
    """Everything about one incident's RCA, for out/<incident>.json."""
    alert = result.alert
    return {
        "incident": result.incident_id,
        "alert": {
            "id": alert.id,
            "pipeline": alert.pipeline,
            "check": alert.check,
            "severity": alert.severity,
            "fired_at": show_time(alert.fired_at) + " UTC",
            "message": alert.message,
        },
        "status": result.status,
        "root_cause_category": result.category,
        "root_cause_pipeline": result.pipeline,
        "summary": result.summary,
        "claims": [_claim_record(claim) for claim in result.claims],
        "affected_pipelines": result.affected,
        "actions": [_claim_record(action) for action in result.actions],
        "confidence": {
            "confident": result.verdict.confident,
            "reasons": result.verdict.reasons,
            "grounded_claims": result.verdict.grounded,
            "claims": result.verdict.claims,
            "supporting_kinds": result.verdict.supporting_kinds,
        },
        "investigation": {
            "tool_calls": [
                {"tool": use.name, "arguments": use.arguments, "by_model": use.by_model, "source_ids": use.source_ids}
                for use in result.investigation.uses
            ],
            "model_calls": result.investigation.model_calls,
            "stopped": result.investigation.stopped,
            "calls_not_run": result.investigation.skipped,
        },
        "seconds": result.seconds,
        "error": result.error,
    }
