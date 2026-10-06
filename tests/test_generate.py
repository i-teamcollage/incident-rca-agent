import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))

import generate  # noqa: E402

from incident_rca_agent.categories import CATEGORIES, shows  # noqa: E402
from incident_rca_agent.confidence import blames_upstream  # noqa: E402
from incident_rca_agent.incident import LAYERS, Incident, Knowledge, Platform  # noqa: E402
from incident_rca_agent.tools import Session, Source  # noqa: E402

PLATFORM = Platform.from_data(generate.build_platform())
RUNBOOKS = generate.build_runbooks()
POSTMORTEMS = generate.build_postmortems()
KNOWLEDGE = Knowledge.from_data(RUNBOOKS, POSTMORTEMS)
SPLITS = {name: generate.build_split(*settings) for name, settings in generate.SPLITS.items()}
ALL = [item for items in SPLITS.values() for item in items]
DST_DAYS = {date(2025, 10, 26), date(2026, 3, 29)}


def load(item) -> Incident:
    files = item["files"]
    return Incident.from_data(
        item["id"],
        json.loads(files["alert.json"]),
        files["logs.txt"],
        json.loads(files["metrics.json"]),
        json.loads(files["changes.json"]),
    )


INCIDENTS = {item["id"]: load(item) for item in ALL}


def test_platform_has_thirty_pipelines_in_six_layers():
    layers = Counter(pipeline.layer for pipeline in PLATFORM.pipelines.values())
    assert len(PLATFORM.names) == 30
    assert [layers[layer] for layer in LAYERS] == [6, 6, 6, 5, 4, 3]


def test_lineage_flows_from_sources_to_exports():
    order = {layer: index for index, layer in enumerate(LAYERS)}
    for name, pipeline in PLATFORM.pipelines.items():
        inputs = PLATFORM.inputs(name)
        assert (pipeline.layer == "source") == (not inputs), name
        assert (pipeline.layer == "export") == (not PLATFORM.outputs(name)), name
        for upstream in inputs:
            assert order[PLATFORM.pipelines[upstream].layer] <= order[pipeline.layer], (upstream, name)
        assert name not in {other for other, _ in PLATFORM.upstream(name)}


def test_external_hosts_use_reserved_names():
    for pipeline in PLATFORM.pipelines.values():
        if pipeline.host:
            assert pipeline.host.endswith((".example", ".internal")), pipeline.host
    for incident in INCIDENTS.values():
        for line in incident.logs:
            for host in re.findall(r"\b[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+\.(?:[a-z]+)\b", line.message):
                if not re.fullmatch(r"[a-z_]+\.[a-z_]+", host):  # warehouse tables such as raw.orders
                    assert host.endswith((".example", ".internal")), (incident.name, host)


def test_fifteen_runbooks_with_section_ids_and_one_per_failure_type():
    assert len(RUNBOOKS) == 15
    section_ids = [section["id"] for runbook in RUNBOOKS for section in runbook["sections"]]
    assert len(section_ids) == len(set(section_ids))
    assert all(re.fullmatch(r"RB-\d{2}\.\d", section_id) for section_id in section_ids)
    assert set(generate.RUNBOOK_OF) == set(CATEGORIES)
    assert len(set(generate.RUNBOOK_OF.values())) == len(CATEGORIES)


def test_twenty_five_past_post_mortems_cover_every_failure_type():
    assert len(POSTMORTEMS) == 25
    assert len({postmortem["id"] for postmortem in POSTMORTEMS}) == 25
    per_type = Counter(postmortem["category"] for postmortem in POSTMORTEMS)
    assert set(per_type) == set(CATEGORIES)
    assert min(per_type.values()) >= 2
    for postmortem in POSTMORTEMS:
        assert set(postmortem["pipelines"]) <= set(PLATFORM.names), postmortem["id"]
        assert postmortem["date"] < "2026-01-01"


def test_splits_have_the_promised_sizes_and_every_failure_type():
    assert [len(SPLITS["practice"]), len(SPLITS["test"])] == [15, 25]
    for items in SPLITS.values():
        assert {item["label"]["category"] for item in items} == set(CATEGORIES)


def test_test_split_includes_red_herring_variants():
    herrings = Counter(item["label"]["red_herring"] for item in SPLITS["test"] if item["label"]["red_herring"])
    assert sum(herrings.values()) >= 8
    assert len(herrings) == 3
    assert sum(1 for item in SPLITS["practice"] if item["label"]["red_herring"]) >= 3


def test_each_incident_has_two_hundred_to_five_hundred_log_lines_in_order():
    for incident in INCIDENTS.values():
        assert 200 <= len(incident.logs) <= 500, (incident.name, len(incident.logs))
        assert [line.id for line in incident.logs] == [f"L-{n:04d}" for n in range(1, len(incident.logs) + 1)]
        assert [line.time for line in incident.logs] == sorted(line.time for line in incident.logs)
        assert all(incident.window_start <= line.time <= incident.window_end for line in incident.logs)
        assert {line.level for line in incident.logs} <= {"INFO", "WARN", "ERROR"}
        assert {line.pipeline for line in incident.logs} == set(incident.runs), incident.name


def test_metrics_hold_three_days_of_runs_before_the_window():
    for incident in INCIDENTS.values():
        for runs in incident.runs.values():
            times = runs.times()
            window = [moment for moment in times if moment >= incident.window_start]
            assert len(window) == 6, (incident.name, runs.pipeline)
            assert len(times) == 78
            assert {len(series) for series in runs.values.values()} == {78}
            assert times[-1] <= incident.window_end


def test_the_root_cause_is_the_alerting_pipeline_or_upstream_of_it():
    downstream = 0
    for item in ALL:
        label = item["label"]
        root, alert = label["root_cause_pipeline"], label["alert_pipeline"]
        assert root == alert or PLATFORM.is_upstream(root, alert), item["id"]
        assert label["alert_on_root"] == (root == alert)
        assert INCIDENTS[item["id"]].alert.pipeline == alert
        downstream += root != alert
    assert downstream >= 0.6 * len(ALL)


def test_affected_pipelines_run_from_the_root_to_the_alert():
    for item in ALL:
        label = item["label"]
        root, alert, affected = label["root_cause_pipeline"], label["alert_pipeline"], label["affected_pipelines"]
        assert affected[0] == root and alert in affected, item["id"]
        for name in affected[1:]:
            assert PLATFORM.is_upstream(root, name), (item["id"], name)
            assert name == alert or PLATFORM.is_upstream(name, alert), (item["id"], name)


def test_labelled_evidence_exists_and_is_about_the_root_cause():
    for item in ALL:
        label, incident = item["label"], INCIDENTS[item["id"]]
        root = label["root_cause_pipeline"]
        lines = {line.id: line for line in incident.logs}
        assert label["evidence"]["logs"], item["id"]
        for line_id in label["evidence"]["logs"]:
            assert lines[line_id].pipeline == root and lines[line_id].level in ("WARN", "ERROR"), (item["id"], line_id)
        changes = {change.id: change for change in incident.changes}
        for change_id in label["evidence"]["changes"]:
            assert root in PLATFORM.mentioned(changes[change_id].text()), (item["id"], change_id)
        assert label["evidence"]["metrics"], item["id"]
        session = Session(incident, PLATFORM, KNOWLEDGE)
        for metric in label["evidence"]["metrics"]:
            summary = session.run("get_metric_summary", {"pipeline": root, "metric": metric}).text
            assert "Change point" in summary, (item["id"], metric)


def test_references_point_at_the_right_runbook_and_past_incidents():
    sections = {section["id"] for runbook in RUNBOOKS for section in runbook["sections"]}
    categories = {postmortem["id"]: postmortem["category"] for postmortem in POSTMORTEMS}
    for item in ALL:
        label = item["label"]
        references = label["references"]
        assert references["runbook_sections"] and set(references["runbook_sections"]) <= sections
        assert all(s.startswith(generate.RUNBOOK_OF[label["category"]] + ".") for s in references["runbook_sections"])
        assert references["past_incidents"]
        assert all(categories[pm] == label["category"] for pm in references["past_incidents"])


def test_every_incident_has_unrelated_changes():
    for item in ALL:
        incident = INCIDENTS[item["id"]]
        unrelated = [change for change in incident.changes if change.id not in item["label"]["evidence"]["changes"]]
        assert len(unrelated) >= 4, item["id"]
        assert [change.time for change in incident.changes] == sorted(change.time for change in incident.changes)


def test_red_herrings_are_what_they_say():
    for item in ALL:
        label, incident = item["label"], INCIDENTS[item["id"]]
        herring = label["red_herring"]
        evidence = set(label["evidence"]["changes"])
        if herring == "deploy_on_alert_pipeline":
            assert any(
                change.target == label["alert_pipeline"] and change.id not in evidence for change in incident.changes
            ), item["id"]
        elif herring == "failure_in_other_pipeline":
            outside = set(incident.runs) - set(label["affected_pipelines"])
            assert any(line.level == "ERROR" and line.pipeline in outside for line in incident.logs), item["id"]
        elif herring == "misleading_metric":
            outside = set(incident.runs) - set(label["affected_pipelines"])
            session = Session(incident, PLATFORM, KNOWLEDGE)
            flagged = [
                name
                for name in outside
                if "Change point" in session.run("get_metric_summary", {"pipeline": name, "metric": "duration"}).text
            ]
            assert flagged, item["id"]
        else:
            assert herring is None


def test_daylight_saving_incidents_fall_on_clock_change_days():
    for item in ALL:
        day = INCIDENTS[item["id"]].alert.fired_at.date()
        if item["label"]["category"] == "daylight_saving_bug":
            assert day in DST_DAYS, item["id"]
        else:
            assert day not in DST_DAYS, item["id"]


def test_logged_row_counts_match_the_metrics():
    pattern = re.compile(r"finished in (\d+) s, (\d+) rows written")
    for incident in INCIDENTS.values():
        for line in incident.logs:
            found = pattern.search(line.message)
            if found:
                runs = incident.runs[line.pipeline]
                assert int(found.group(2)) in runs.values["rows_out"][-6:], (incident.name, line.id)
                assert int(found.group(1)) in runs.values["duration"][-6:], (incident.name, line.id)


def test_the_alerting_pipeline_really_is_in_trouble():
    for item in ALL:
        incident = INCIDENTS[item["id"]]
        runs = incident.runs[incident.alert.pipeline]
        session = Session(incident, PLATFORM, KNOWLEDGE)
        summary = session.run("get_metric_summary", {"pipeline": incident.alert.pipeline})
        assert session.sources[summary.source_ids[0]].abnormal or set(runs.status[-6:]) - {"ok"}, item["id"]


def test_the_same_seed_gives_the_same_incidents():
    again = generate.build_split(*generate.SPLITS["practice"])
    assert [item["files"] for item in again] == [item["files"] for item in SPLITS["practice"]]
    assert [item["label"] for item in again] == [item["label"] for item in SPLITS["practice"]]


def test_check_mode_finds_no_difference_with_the_committed_data():
    assert generate.main(["--check"]) == 0


def test_every_pipeline_runs_after_the_pipelines_it_reads():
    minutes = {name: generate.SPEC[name].minute for name in PLATFORM.names}
    for upstream, downstream in generate.build_platform()["edges"]:
        assert minutes[upstream] < minutes[downstream], (upstream, downstream)


def test_alert_ids_are_unique_in_each_split():
    for items in SPLITS.values():
        ids = [INCIDENTS[item["id"]].alert.id for item in items]
        assert len(ids) == len(set(ids))


def test_labelled_log_evidence_shows_the_failure_type():
    for item in ALL:
        label, incident = item["label"], INCIDENTS[item["id"]]
        lines = {line.id: line for line in incident.logs}
        for line_id in label["evidence"]["logs"]:
            assert shows(label["category"], lines[line_id].message), (item["id"], line_id)


def test_away_from_the_root_no_line_can_pass_for_a_cause():
    # A warning or error that shows a failure type is on the root-cause pipeline, or blames an upstream pipeline.
    for item in ALL:
        label, incident = item["label"], INCIDENTS[item["id"]]
        for line in incident.logs:
            if line.level == "INFO" or not any(shows(category, line.message) for category in CATEGORIES):
                continue
            if line.pipeline == label["root_cause_pipeline"]:
                continue
            source = Source(line.id, "log", line.text(), frozenset({line.pipeline}), True)
            assert blames_upstream(source, PLATFORM), (item["id"], line.text())


def test_the_labelled_evidence_never_looks_like_a_symptom():
    for item in ALL:
        label, incident = item["label"], INCIDENTS[item["id"]]
        lines = {line.id: line for line in incident.logs}
        for line_id in label["evidence"]["logs"]:
            line = lines[line_id]
            source = Source(line.id, "log", line.text(), frozenset({line.pipeline}), True)
            assert not blames_upstream(source, PLATFORM), (item["id"], line.text())
