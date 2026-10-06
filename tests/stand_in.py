"""Stand-in models for tests. They answer like Ollama does, so the agent's own code runs unchanged.

StandInModel investigates like a careful engineer who already knows the answer. It follows the order a
runbook gives: the lineage above the alerting pipeline, the warnings of the root-cause pipeline, its
metrics, the change log, then the runbooks and past incidents for the error it found. Its RCA comes from
the answer key, and each claim is copied from a source it retrieved. It measures what the tools and the
checks allow (the ceiling of the deterministic layer), not what a model can work out.

AlertBlamer is the opposite: it never looks past the alerting pipeline, and blames it for what it sees
there. It shows what the confidence line does with an investigation that stops at the symptom.
"""

import json
import re
from pathlib import Path

from incident_rca_agent.categories import CATEGORIES, category_name, shows

ALERT_ID = re.compile(r"\[ALERT\] (ALR-\d+)")
SOURCE = re.compile(r"^\[([A-Z]+-[\w.\-]+)\] (.*)$")
LOG = re.compile(r"^(\S+ \S+) (INFO|WARN|ERROR) (\w+): (.*)$")


def sources_in(text: str) -> dict[str, list[str]]:
    """Every source shown in the conversation: its id and its lines (a metric summary has several)."""
    found: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        match = SOURCE.match(line)
        if match:
            current = match.group(1)
            found.setdefault(current, [match.group(2)])
        elif current and current.startswith("M-") and line.startswith("- "):
            found[current].append(line[2:])
        else:
            current = None
    return found


def log_parts(text: str) -> tuple[str, str, str, str] | None:
    """(time, level, pipeline, message) of a log line, or None for any other source."""
    match = LOG.match(text)
    return match.groups() if match else None


class _Model:
    def __init__(self, data_dir: Path, split: str):
        self.labels = {}
        for line in (data_dir / split / "labels.jsonl").read_text(encoding="utf-8").splitlines():
            label = json.loads(line)
            alert = json.loads((data_dir / split / label["dir"] / "alert.json").read_text(encoding="utf-8"))
            self.labels[alert["id"]] = label
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        messages = request["messages"]
        conversation = "\n".join(str(message.get("content") or "") for message in messages)
        label = self.labels[ALERT_ID.search(conversation).group(1)]
        if "format" in request:
            content, calls = json.dumps(self.rca(label, sources_in(conversation)), ensure_ascii=False), []
        else:
            done = sum(message["role"] == "tool" for message in messages)
            plan = self.plan(label, sources_in(conversation))
            calls = [{"name": name, "arguments": arguments} for name, arguments in plan[done : done + 1]]
            content = "" if calls else "I have what I need for the RCA."
        return {"content": content, "tool_calls": calls, "prompt_tokens": 2400, "output_tokens": 80}


class StandInModel(_Model):
    def plan(self, label, sources):
        root, alert = label["root_cause_pipeline"], label["alert_pipeline"]
        error = self.error(label, sources) or category_name(label["category"])
        return [
            ("get_lineage", {"pipeline": alert, "direction": "upstream"}),
            ("search_logs", {"pipeline": root, "level": "WARN"}),
            ("get_metric_summary", {"pipeline": root, "metric": "all"}),
            ("list_changes", {}),
            ("search_runbooks", {"query": error}),
            ("search_past_incidents", {"query": f"{category_name(label['category'])} {error}"}),
        ]

    @staticmethod
    def error(label, sources):
        for line_id in label["evidence"]["logs"]:
            if line_id in sources:
                return log_parts(sources[line_id][0])[3]
        return ""

    def rca(self, label, sources):
        root, alert, category = label["root_cause_pipeline"], label["alert_pipeline"], label["category"]
        claims = []
        for line_id in [line_id for line_id in label["evidence"]["logs"] if line_id in sources][:2]:
            moment, _, _, message = log_parts(sources[line_id][0])
            claims.append({"text": f"{root} logged at {moment[11:]}: {message}", "source_ids": [line_id]})
        for source_id, lines in sources.items():
            if source_id.startswith("M-") and f"All metrics of {root}," in lines[0]:
                for metric in label["evidence"]["metrics"]:
                    row = next(row for row in lines if row.startswith(f"{metric} ("))
                    point = row.split("; ", 1)[1].split(", ")[0]
                    claims.append({"text": f"{metric} of {root}: {point}", "source_ids": [source_id]})
                    break
        for change_id in label["evidence"]["changes"]:
            if change_id in sources:
                claims.append({"text": sources[change_id][0], "source_ids": [change_id]})
        if alert != root and "LIN-1" in sources:
            claims.append({"text": f"{alert} depends on {root}, upstream in the lineage", "source_ids": ["LIN-1"]})
        actions = []
        for section in label["references"]["runbook_sections"][::-1]:
            if section in sources:
                actions.append({"text": sources[section][0].split(". ", 1)[0], "source_ids": [section]})
        for past in label["references"]["past_incidents"]:
            if past in sources:
                actions.append({"text": f"Compare with the past incident {past}", "source_ids": [past]})
                break
        return {
            "claims": claims,
            "root_cause_category": category,
            "root_cause_pipeline": root,
            "summary": f"{category_name(category)} in {root}; {alert} is affected.",
            "affected_pipelines": label["affected_pipelines"],
            "actions": actions[:4],
        }


class AlertBlamer(_Model):
    def plan(self, label, sources):
        alert = label["alert_pipeline"]
        return [
            ("get_lineage", {"pipeline": alert, "direction": "upstream"}),
            ("search_logs", {"pipeline": alert, "level": "WARN"}),
            ("get_metric_summary", {"pipeline": alert, "metric": "all"}),
            ("list_changes", {}),
        ]

    def rca(self, label, sources):
        alert = label["alert_pipeline"]
        claims, changes, seen = [], [], []
        for source_id, lines in sources.items():
            parts = log_parts(lines[0])
            if parts and parts[2] == alert and parts[1] != "INFO":
                claims.append({"text": f"{alert} logged at {parts[0][11:]}: {parts[3]}", "source_ids": [source_id]})
                seen.append(parts[3])
            elif source_id.startswith("CHG-") and f" {alert} by " in lines[0]:
                claims.append({"text": lines[0], "source_ids": [source_id]})
                changes.append(source_id)
            elif source_id.startswith("M-"):
                rows = [row for row in lines if "change point at" in row]
                if rows:
                    point = rows[0].split("; ", 1)[1].split(", ")[0]
                    claims.append({"text": f"{rows[0].split(' ')[0]} of {alert}: {point}", "source_ids": [source_id]})
        guess = "bad_config_deploy" if changes else "late_upstream_data"
        for category in CATEGORIES:
            if any(shows(category, message) for message in seen):
                guess = category
                break
        return {
            "claims": claims[:6],
            "root_cause_category": guess,
            "root_cause_pipeline": alert,
            "summary": f"{category_name(guess)} in {alert}.",
            "affected_pipelines": [alert],
            "actions": [],
        }
