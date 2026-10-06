import json
import math

import pytest

from incident_rca_agent.acceptance import (
    Criterion,
    Measure,
    count,
    evaluate,
    load_criteria,
    markdown_table,
    outcomes_from_report,
    rate,
    report_dict,
    results_block,
    terminal_table,
    update_readme,
    wilson,
    write_reports,
)

BANK = Criterion(
    id="bank_auto_precision",
    label="Bank details accepted automatically that are correct",
    metric="bank_auto_precision",
    op=">=",
    target=0.99,
)
WRONG = Criterion(
    id="wrong_closes",
    label="Tickets wrongly closed",
    metric="wrong_closes",
    op="==",
    target=0,
    unit="count",
)


def test_wilson_interval_for_a_perfect_score_has_a_lower_bound_below_one():
    low, high = wilson(80, 80)
    assert low == pytest.approx(0.9542, abs=1e-3)
    assert high == pytest.approx(1.0)


def test_wilson_interval_is_symmetric_around_one_half():
    assert wilson(5, 10) == pytest.approx((0.2366, 0.7634), abs=1e-3)


def test_wilson_interval_is_none_without_items():
    assert wilson(0, 0) is None


def test_rate_divides_hits_by_total():
    measure = rate(99, 100)
    assert measure.value == pytest.approx(0.99)
    assert (measure.hits, measure.total) == (99, 100)


def test_rate_without_items_is_not_a_number():
    assert math.isnan(rate(0, 0).value)


def test_target_reached_exactly_passes():
    [outcome] = evaluate([BANK], {"bank_auto_precision": rate(99, 100)})
    assert outcome.passed


def test_below_target_fails():
    [outcome] = evaluate([BANK], {"bank_auto_precision": rate(98, 100)})
    assert not outcome.passed


def test_no_items_fails_instead_of_passing_silently():
    [outcome] = evaluate([BANK], {"bank_auto_precision": rate(0, 0)})
    assert not outcome.passed


def test_count_must_equal_target():
    assert evaluate([WRONG], {"wrong_closes": count(0)})[0].passed
    assert not evaluate([WRONG], {"wrong_closes": count(1)})[0].passed


def test_missing_metric_names_the_metric():
    with pytest.raises(KeyError, match="bank_auto_precision"):
        evaluate([BANK], {})


def test_markdown_table_shows_target_value_and_result():
    outcomes = evaluate([BANK, WRONG], {"bank_auto_precision": rate(98, 100), "wrong_closes": count(0)})
    table = markdown_table(outcomes)
    assert "| Bank details accepted automatically that are correct | ≥ 99% | 98.0% |" in table
    assert "FAIL" in table
    assert "| Tickets wrongly closed | = 0 | 0 |" in table
    assert "PASS" in table


def test_markdown_table_shows_not_available_without_items():
    outcomes = evaluate([BANK], {"bank_auto_precision": rate(0, 0)})
    assert "n/a" in markdown_table(outcomes)


def test_load_criteria_reads_toml(tmp_path):
    path = tmp_path / "acceptance.toml"
    path.write_text(
        '[[criteria]]\nid = "x"\nlabel = "X rate"\nmetric = "x"\nop = ">="\ntarget = 0.5\n',
        encoding="utf-8",
    )
    assert load_criteria(path) == [Criterion(id="x", label="X rate", metric="x", op=">=", target=0.5)]


def test_unknown_operator_is_rejected(tmp_path):
    path = tmp_path / "acceptance.toml"
    path.write_text(
        '[[criteria]]\nid = "x"\nlabel = "X"\nmetric = "x"\nop = ">"\ntarget = 0.5\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="operator"):
        load_criteria(path)


README = "# Title\n\n<!-- results:start -->\nold\n<!-- results:end -->\n\nRest\n"


def test_update_readme_replaces_only_the_results_block(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    changed = update_readme(readme, "new table")
    assert changed
    assert readme.read_text(encoding="utf-8") == (
        "# Title\n\n<!-- results:start -->\nnew table\n<!-- results:end -->\n\nRest\n"
    )


def test_update_readme_check_mode_reports_drift_without_writing(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    assert update_readme(readme, "new table", check=True)
    assert readme.read_text(encoding="utf-8") == README


def test_update_readme_reports_no_change_when_in_sync(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    assert not update_readme(readme, "old")


def test_update_readme_requires_the_markers(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n", encoding="utf-8")
    with pytest.raises(ValueError, match="results:start"):
        update_readme(readme, "x")


def test_measure_defaults_have_no_interval():
    assert Measure(value=3.0).hits is None


META = {
    "title": "Finance document agent: acceptance test",
    "items": 80,
    "model": "ministral-3:8b",
    "machine": "Apple M4, 16 GB",
    "recorded_at": "2026-10-04",
    "median_seconds_per_item": 17.24,
}


def test_report_round_trip_keeps_the_table():
    outcomes = evaluate([BANK, WRONG], {"bank_auto_precision": rate(98, 100), "wrong_closes": count(0)})
    report = report_dict(outcomes, META)
    assert report["passed"] is False
    assert markdown_table(outcomes_from_report(report)) == markdown_table(outcomes)


def test_report_round_trip_survives_missing_values():
    outcomes = evaluate([BANK], {"bank_auto_precision": rate(0, 0)})
    report = report_dict(outcomes, META)
    assert report["criteria"][0]["value"] is None
    assert "n/a" in markdown_table(outcomes_from_report(report))


def test_results_block_states_what_was_measured_and_how():
    outcomes = evaluate([BANK], {"bank_auto_precision": rate(99, 100)})
    block = results_block(report_dict(outcomes, META))
    assert block.endswith(
        "80 made-up test cases, ministral-3:8b on Apple M4, 16 GB, recorded 2026-10-04, median 17.2 s per item."
    )


def test_write_reports_creates_json_and_markdown(tmp_path):
    outcomes = evaluate([BANK], {"bank_auto_precision": rate(99, 100)})
    write_reports(report_dict(outcomes, META), tmp_path, details_markdown="## Per field\n\nrows")
    saved = json.loads((tmp_path / "acceptance.json").read_text(encoding="utf-8"))
    assert saved["criteria"][0]["hits"] == 99
    markdown = (tmp_path / "acceptance.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Finance document agent: acceptance test")
    assert "All criteria passed." in markdown
    assert markdown.rstrip().endswith("rows")


def test_terminal_table_lists_each_result():
    outcomes = evaluate([BANK, WRONG], {"bank_auto_precision": rate(98, 100), "wrong_closes": count(0)})
    lines = terminal_table(outcomes).splitlines()
    assert lines[0].split()[0] == "FAIL"
    assert lines[1].split()[0] == "PASS"
    assert "(target ≥ 99%)" in lines[0]
