"""Command line.

python -m incident_rca_agent demo                three incidents from the stored run
python -m incident_rca_agent accept              the acceptance test on the test split
python -m incident_rca_agent accept --record     run it live on Ollama and store the run
python -m incident_rca_agent accept --resume     carry on with a stored run that stopped half-way
python -m incident_rca_agent run INCIDENT_DIR    investigate one incident live
python -m incident_rca_agent readme              refresh the results table in README.md
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import date
from pathlib import Path

from .acceptance import (
    evaluate,
    load_criteria,
    report_dict,
    results_block,
    terminal_table,
    update_readme,
    write_reports,
)
from .categories import category_name
from .incident import Incident, Knowledge, Platform
from .llm import DEFAULT_MODEL, LLM, ModelUnavailable, ReplayMiss, machine_description, ollama_info, run_dir_name
from .metrics import rca_correct, score
from .pipeline import IncidentResult, analyse, as_record
from .slack import block_kit, markdown

ROOT = Path(__file__).resolve().parents[2]
TITLE = "Incident RCA agent: acceptance test"
NUM_CTX = 16384


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.data = self.root / "data"
        self.reports = self.root / "reports"
        self.readme = self.root / "README.md"
        self.acceptance = self.root / "acceptance.toml"

    def runs_root(self, split: str) -> Path:
        return self.root / "runs" if split == "test" else self.root / "runs" / split

    def out(self, split: str) -> Path:
        return self.root / "out" / split

    def labels(self, split: str) -> list[dict]:
        lines = (self.data / split / "labels.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def incident(self, split: str, label: dict) -> Incident:
        return Incident.load(self.data / split / label["dir"])

    def platform(self) -> Platform:
        return Platform.load(self.data / "shared" / "lineage.json")

    def knowledge(self) -> Knowledge:
        return Knowledge.load(self.data / "shared")


def _mode(args: argparse.Namespace) -> str:
    if getattr(args, "resume", False):
        return "resume"
    if getattr(args, "record", False):
        return "record"
    if getattr(args, "live", False):
        return "live"
    return os.environ.get("AGENT_MODE", "replay")


def _run_split(ws: Workspace, split: str, llm: LLM) -> tuple[list[dict], dict[str, IncidentResult]]:
    platform, knowledge, labels, results = ws.platform(), ws.knowledge(), ws.labels(split), {}
    for number, label in enumerate(labels, start=1):
        result = analyse(label["id"], ws.incident(split, label), llm, platform, knowledge)
        results[label["id"]] = result
        if llm.mode != "replay":
            found = f"{category_name(result.category)} in {result.pipeline}"
            print(
                f"  [{number:>3}/{len(labels)}] {label['id']}  {result.status:<17} {found:<48} "
                f"{result.tool_calls} tools  {result.seconds:6.1f} s",
                flush=True,
            )
    return labels, results


def _write_one(out: Path, result: IncidentResult) -> None:
    out.mkdir(parents=True, exist_ok=True)
    name = result.incident_id
    (out / f"{name}.json").write_text(json.dumps(as_record(result), indent=2, ensure_ascii=False) + "\n", "utf-8")
    (out / f"{name}.slack.json").write_text(json.dumps(block_kit(result), indent=2, ensure_ascii=False) + "\n", "utf-8")
    (out / f"{name}.md").write_text(markdown(result), encoding="utf-8")


def _write_outputs(ws: Workspace, split: str, results: dict[str, IncidentResult]) -> None:
    for result in results.values():
        _write_one(ws.out(split), result)


def _run_info_path(ws: Workspace, split: str, model: str) -> Path:
    return ws.runs_root(split) / run_dir_name(model) / "run.json"


def _write_run_info(ws: Workspace, split: str, llm: LLM, results: dict[str, IncidentResult], local_model: bool) -> None:
    seconds = [result.seconds for result in results.values()]
    info = {
        "recorded_at": date.today().isoformat(),
        "model": llm.model,
        **(ollama_info(llm.model) if local_model else {}),
        "machine": machine_description(),
        "split": split,
        "items": len(results),
        "median_seconds_per_item": round(statistics.median(seconds), 2) if seconds else None,
        "item_seconds": {incident_id: result.seconds for incident_id, result in results.items()},
    }
    path = _run_info_path(ws, split, llm.model)
    path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def cmd_accept(args: argparse.Namespace, ws: Workspace, transport=None) -> int:
    mode = _mode(args)
    llm = LLM(model=args.model, mode=mode, runs_root=ws.runs_root(args.split), num_ctx=NUM_CTX, transport=transport)
    if mode in ("record", "resume"):
        llm.start_recording()
    print(f"Acceptance test, {args.split} split, model {args.model}, mode {mode}")
    try:
        labels, results = _run_split(ws, args.split, llm)
    except (ReplayMiss, ModelUnavailable) as error:
        print(f"\n{error}", file=sys.stderr)
        return 2
    _write_outputs(ws, args.split, results)
    if mode in ("record", "resume"):
        llm.finish_recording()
        _write_run_info(ws, args.split, llm, results, local_model=transport is None)
    info_path = _run_info_path(ws, args.split, args.model)
    info = json.loads(info_path.read_text(encoding="utf-8")) if mode != "live" and info_path.exists() else {}

    measures, details = score(labels, results)
    outcomes = evaluate(load_criteria(ws.acceptance), measures)
    seconds = [result.seconds for result in results.values() if result.seconds]
    meta = {
        "title": TITLE,
        "split": args.split,
        "items": len(labels),
        "model": args.model,
        "machine": info.get("machine") or machine_description(),
        "recorded_at": info.get("recorded_at"),
        "ollama_version": info.get("ollama_version"),
        "model_digest": info.get("model_digest"),
        "median_seconds_per_item": round(statistics.median(seconds), 1) if seconds else None,
    }
    report = report_dict(outcomes, meta)
    reports = ws.reports if args.split == "test" else ws.reports / args.split
    write_reports(report, reports, details)
    print()
    print(terminal_table(outcomes))
    print(f"\n{'PASSED' if report['passed'] else 'FAILED'}: details in {reports}/acceptance.md")
    return 0 if report["passed"] else 1


def _title(label: dict, result: IncidentResult) -> str:
    key = f"{category_name(label['category'])} in {label['root_cause_pipeline']}"
    verdict = "right" if rca_correct(label, result) else "wrong"
    herring = f", red herring: {label['red_herring'].replace('_', ' ')}" if label.get("red_herring") else ""
    return f"=== {label['id']} (answer key: {key}; the agent's answer is {verdict}{herring})"


def cmd_demo(args: argparse.Namespace, ws: Workspace, transport=None) -> int:
    llm = LLM(model=args.model, mode=_mode(args), runs_root=ws.runs_root("test"), num_ctx=NUM_CTX, transport=transport)
    try:
        labels, results = _run_split(ws, "test", llm)
    except (ReplayMiss, ModelUnavailable) as error:
        print(error, file=sys.stderr)
        return 2
    picks: list[dict] = []
    rules = [
        lambda label, result: result.confident and rca_correct(label, result),
        lambda label, result: not result.confident,
        lambda label, result: bool(label.get("red_herring")),
    ]
    for rule in rules:
        pick = next((label for label in labels if label not in picks and rule(label, results[label["id"]])), None)
        if pick is not None:
            picks.append(pick)
    picks += [label for label in labels if label not in picks][: 3 - len(picks)]  # always three examples
    for label in picks:
        result = results[label["id"]]
        print(_title(label, result))
        print(markdown(result))
    return 0


def cmd_run(args: argparse.Namespace, ws: Workspace, transport=None) -> int:
    mode = "replay" if args.replay else "live"
    llm = LLM(model=args.model, mode=mode, runs_root=ws.runs_root("test"), num_ctx=NUM_CTX, transport=transport)
    folder = Path(args.incident_dir)
    shared = Path(args.shared) if args.shared else ws.data / "shared"
    try:
        result = analyse(
            folder.name,
            Incident.load(folder),
            llm,
            Platform.load(shared / "lineage.json"),
            Knowledge.load(shared),
        )
    except (ReplayMiss, ModelUnavailable) as error:
        print(error, file=sys.stderr)
        return 2
    _write_one(ws.out("run"), result)
    print(markdown(result))
    return 0


def cmd_readme(args: argparse.Namespace, ws: Workspace, transport=None) -> int:
    report = json.loads((ws.reports / "acceptance.json").read_text(encoding="utf-8"))
    changed = update_readme(ws.readme, results_block(report), check=args.check)
    if args.check:
        print("README results are out of date: run `make readme`" if changed else "README results are up to date")
        return 1 if changed else 0
    print("README results updated" if changed else "README results already up to date")
    return 0


def main(argv: list[str] | None = None, *, root: Path = ROOT, transport=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m incident_rca_agent", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    model = os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
    for name in ("demo", "accept"):
        command = commands.add_parser(name)
        command.add_argument("--live", action="store_true", help="ask the model instead of the stored run")
        command.add_argument("--record", action="store_true", help="ask the model and store the run")
        command.add_argument("--model", default=model)
        if name == "accept":
            command.add_argument(
                "--resume", action="store_true", help="like --record, reusing the answers a run that stopped stored"
            )
            command.add_argument("--split", default="test", choices=["test", "practice"])
    run = commands.add_parser("run")
    run.add_argument("incident_dir", help="a folder with alert.json, logs.txt, metrics.json and changes.json")
    run.add_argument("--shared", help="the folder with lineage.json, runbooks.json and postmortems.json")
    run.add_argument("--replay", action="store_true")
    run.add_argument("--model", default=model)
    readme = commands.add_parser("readme")
    readme.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    handler = {"demo": cmd_demo, "accept": cmd_accept, "run": cmd_run, "readme": cmd_readme}[args.command]
    return handler(args, Workspace(root), transport)
