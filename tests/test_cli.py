import json
import shutil
from pathlib import Path

from stand_in import AlertBlamer, StandInModel

from incident_rca_agent.cli import main

REPO = Path(__file__).resolve().parents[1]
README = "# Sample\n\n<!-- results:start -->\nnot yet measured\n<!-- results:end -->\n"
TEST_CALLS = 25 * 8  # per incident: six tool steps, the step that stops, and the RCA


def workspace(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "data", tmp_path / "data", ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    shutil.copy(REPO / "acceptance.toml", tmp_path / "acceptance.toml")
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    return tmp_path


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_record_then_replay_gives_the_same_report_without_a_model(tmp_path):
    root = workspace(tmp_path)
    model = StandInModel(root / "data", "test")
    record_code = main(["accept", "--record"], root=root, transport=model)
    recorded = read(root / "reports" / "acceptance.json")
    assert model.calls == TEST_CALLS
    assert (root / "runs" / "ministral-3-8b" / "calls.jsonl").exists()
    run_info = read(root / "runs" / "ministral-3-8b" / "run.json")
    assert run_info["items"] == 25 and run_info["split"] == "test"

    replay_code = main(["accept"], root=root)
    replayed = read(root / "reports" / "acceptance.json")
    assert replay_code == record_code
    assert replayed["criteria"] == recorded["criteria"]
    assert replayed["recorded_at"] == run_info["recorded_at"]
    assert "Tool calls per incident" in (root / "reports" / "acceptance.md").read_text(encoding="utf-8")


def test_each_incident_gets_its_rca_a_slack_message_and_a_preview(tmp_path):
    root = workspace(tmp_path)
    main(["accept", "--record"], root=root, transport=StandInModel(root / "data", "test"))
    out = root / "out" / "test"
    record = read(out / "t-001.json")
    assert {"status", "root_cause_category", "root_cause_pipeline", "claims", "actions", "confidence"} <= set(record)
    slack = read(out / "t-001.slack.json")
    assert slack["blocks"][0]["type"] == "header" and slack["text"]
    assert (out / "t-001.md").read_text(encoding="utf-8").startswith("## ")
    assert len(list(out.glob("*.slack.json"))) == 25


def test_readme_block_follows_the_report_and_check_mode_detects_drift(tmp_path):
    root = workspace(tmp_path)
    main(["accept", "--record"], root=root, transport=StandInModel(root / "data", "test"))
    assert main(["readme", "--check"], root=root) == 1
    assert main(["readme"], root=root) == 0
    assert main(["readme", "--check"], root=root) == 0
    assert "Root cause correct (right cause and right pipeline)" in (root / "README.md").read_text(encoding="utf-8")


def test_demo_replays_three_examples(tmp_path, capsys):
    root = workspace(tmp_path)
    main(["accept", "--record"], root=root, transport=StandInModel(root / "data", "test"))
    capsys.readouterr()
    assert main(["demo"], root=root) == 0
    shown = capsys.readouterr().out
    assert shown.count("## ") == 3
    assert "answer key:" in shown


def test_run_investigates_one_incident_folder(tmp_path, capsys):
    root = workspace(tmp_path)
    folder = root / "data" / "test" / "incidents" / "t-002"
    assert main(["run", str(folder)], root=root, transport=StandInModel(root / "data", "test")) == 0
    assert capsys.readouterr().out.startswith("## ")
    assert read(root / "out" / "run" / "t-002.json")["incident"] == "t-002"
    assert read(root / "out" / "run" / "t-002.slack.json")["blocks"]


def test_replay_without_a_stored_run_explains_what_to_do(tmp_path, capsys):
    root = workspace(tmp_path)
    assert main(["accept"], root=root) == 2
    assert "make record" in capsys.readouterr().err
    assert main(["demo"], root=root) == 2


def test_the_deterministic_layer_lets_a_perfect_investigator_pass_on_the_practice_split(tmp_path):
    # The ceiling: a stand-in that knows the answer and cites only what it retrieved.
    root = workspace(tmp_path)
    main(["accept", "--split", "practice", "--record"], root=root, transport=StandInModel(root / "data", "practice"))
    report = read(root / "reports" / "practice" / "acceptance.json")
    measured = {criterion["id"]: (criterion["hits"], criterion["total"]) for criterion in report["criteria"]}
    assert measured == {"rca_accuracy": (15, 15), "confident_precision": (15, 15), "grounding": (62, 62)}


def test_an_investigation_that_stops_at_the_alerting_pipeline_is_never_posted_as_confident(tmp_path):
    root = workspace(tmp_path)
    labels = [read_line for read_line in map(json.loads, (root / "data" / "practice" / "labels.jsonl").open())]
    main(["accept", "--split", "practice", "--record"], root=root, transport=AlertBlamer(root / "data", "practice"))
    for label in labels:
        record = read(root / "out" / "practice" / f"{label['id']}.json")
        right = (record["root_cause_category"], record["root_cause_pipeline"]) == (
            label["category"],
            label["root_cause_pipeline"],
        )
        if label["alert_pipeline"] != label["root_cause_pipeline"]:
            assert record["status"] == "needs an engineer", label["id"]
        if record["status"] == "confident":
            assert right, label["id"]


def test_a_recording_cut_short_resumes_where_it_stopped_and_ends_as_a_full_run(tmp_path):
    root = workspace(tmp_path)
    main(["accept", "--record"], root=root, transport=StandInModel(root / "data", "test"))
    full = read(root / "reports" / "acceptance.json")
    calls = root / "runs" / "ministral-3-8b" / "calls.jsonl"
    lines = calls.read_text(encoding="utf-8").splitlines(keepends=True)
    calls.write_text("".join(lines[: len(lines) // 2]), encoding="utf-8")  # the run stopped half-way

    model = StandInModel(root / "data", "test")
    main(["accept", "--resume"], root=root, transport=model)
    assert model.calls == len(lines) - len(lines) // 2
    fingerprints = [json.loads(line)["fingerprint"] for line in calls.read_text(encoding="utf-8").splitlines()]
    assert fingerprints == [json.loads(line)["fingerprint"] for line in lines]
    assert read(root / "reports" / "acceptance.json")["criteria"] == full["criteria"]
    assert main(["accept"], root=root) == (0 if full["passed"] else 1)
