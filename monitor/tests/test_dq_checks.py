"""Every data-quality check does its job: clean data passes, each planted fault is caught."""
import pytest

import dq_checks as q


def failing(results):
    return {r.name for r in results if not r.passed}


def test_clean_data_passes_every_check(data, today):
    results, errors = q.run_checks(data, today)
    assert failing(results) == set(), [(r.name, r.observed) for r in results if not r.passed]
    assert all(len(e) == 0 for e in errors.values())


@pytest.mark.parametrize("fault, expected", [
    ("dupes", {"Grain"}),
    ("stale", {"Freshness"}),
    ("dropout", {"Completeness at latest date"}),
    ("gap", {"Continuity"}),
    ("bad_values", {"Contract: sector rows", "Extremes named"}),
    ("spike", {"Latest move within control limits"}),
])
def test_each_planted_fault_is_caught(data, today, fault, expected):
    results, _ = q.run_checks(q.apply_fault(data, fault), today)
    assert expected <= failing(results), f"{fault}: expected {expected}, got {failing(results)}"


def test_spike_is_a_warning_not_a_block(data, today):
    results, _ = q.run_checks(q.apply_fault(data, "spike"), today)
    r = next(r for r in results if r.name == "Latest move within control limits")
    assert r.status == "warn" and r.evidence[0]["series"] == "Banking & Finance"


def test_pydantic_contract_names_row_and_field(data, today):
    _, errors = q.run_checks(q.apply_fault(data, "bad_values"), today)
    err = errors["sector"]
    assert set(err["field"]) == {"idx", "variable"}
    assert set(err["error"]) == {"finite_number", "greater_than", "literal_error"}


def test_lookup_gap_warns_without_blocking(data, today):
    d = {**data, "titles": data["titles"].iloc[1:]}
    results, _ = q.run_checks(d, today)
    lookup = next(r for r in results if r.name == "Title lookup coverage")
    assert lookup.status == "warn"
    assert all(r.passed for r in results if r.severity == "block")


def test_faults_never_mutate_the_original(data):
    before = len(data["sector"])
    for fault in q.FAULTS:
        q.apply_fault(data, fault)
    assert len(data["sector"]) == before
