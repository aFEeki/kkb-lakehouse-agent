"""SCRUM-83 fixed-snapshot regression harness tests."""

from __future__ import annotations

import hashlib
import json
import socket
from copy import deepcopy
from pathlib import Path

import duckdb
import pytest

from kkb_agent.regression import (
    ABSOLUTE_TOLERANCE,
    build_snapshot_database,
    compare_contracts,
    run_published_regression,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "regression"
SNAPSHOT = FIXTURES / "published_three_turn_snapshot.json"
EXPECTED = FIXTURES / "published_three_turn_expected.json"


@pytest.fixture(scope="module")
def expected():
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def actual():
    return run_published_regression(SNAPSHOT)


def test_fixed_snapshot_materializes_deterministically(tmp_path):
    rows = []
    for name in ("one.duckdb", "two.duckdb"):
        database = tmp_path / name
        build_snapshot_database(SNAPSHOT, database)
        connection = duckdb.connect(str(database), read_only=True)
        rows.append(
            connection.execute(
                "SELECT series_id, period, value FROM series_observations "
                "ORDER BY series_id, period"
            ).fetchall()
        )
        connection.close()
    assert rows[0] == rows[1]
    assert len(rows[0]) == 240


def test_published_scenario_matches_curated_contract(actual, expected):
    assert compare_contracts(expected, actual) == ()


def test_spine_and_versions_are_locked(actual):
    assert len(actual["spine"]) == len(set(actual["spine"])) == 60
    assert (actual["spine"][0], actual["spine"][-1]) == ("2021-01-01", "2025-12-01")
    assert [turn["version"] for turn in actual["turns"]] == [2, 4, 5]
    assert all(turn["spine_matches_shared"] for turn in actual["turns"])
    assert actual["stale_version_rejected"] is True


def test_columns_series_lineage_and_supersession_are_locked(actual):
    columns = [[column["key"] for column in turn["columns"]] for turn in actual["turns"]]
    assert columns[1][: len(columns[0])] == columns[0]
    assert columns[2][: len(columns[1])] == columns[1]
    assert actual["turns"][0]["columns"][0]["source_ids"] == [
        "bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut"
    ]
    assert actual["turns"][0]["columns"][1]["source_ids"] == ["evds.TP.KTF12"]
    assert actual["turns"][0]["columns"][1]["transformations"] == [
        {"name": "collapse_frequency", "parameters": {"from": "W", "rule": "mean"}}
    ]
    assert actual["turns"][1]["columns"][-1]["source_ids"] == [
        "bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut",
        "evds.TP.GENENDEKS.T1",
    ]
    findings = actual["turns"][2]["findings"]
    assert findings[-1]["finding_id"] == "f-decline-hpi"
    assert findings[-1]["supersedes"] == "f-decline"
    assert any(finding["finding_id"] == "f-decline" for finding in findings[:-1])


def test_numeric_tolerance_is_small_explicit_and_readable():
    assert compare_contracts({"value": 1.0}, {"value": 1.0 + ABSOLUTE_TOLERANCE / 2}) == ()
    differences = compare_contracts({"value": 1.0}, {"value": 1.0 + ABSOLUTE_TOLERANCE * 2})
    assert len(differences) == 1
    assert differences[0].path == "$.value"
    assert differences[0].expected == 1.0
    assert differences[0].actual > 1.0
    assert differences[0].absolute_difference > differences[0].allowed_tolerance


@pytest.mark.parametrize(
    "path,value,reported_path",
    [
        (("analytics", "nominal_change_pct"), 60.0, "$.analytics.nominal_change_pct"),
        (("turns", 0, "columns"), [], "$.turns[0].columns.length"),
        (
            ("turns", 0, "columns", 0, "source_ids"),
            ["wrong.series"],
            "$.turns[0].columns[0].source_ids[0]",
        ),
    ],
)
def test_critical_drift_is_detected(expected, path, value, reported_path):
    actual = deepcopy(expected)
    target = actual
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    assert reported_path in {difference.path for difference in compare_contracts(expected, actual)}


def test_unstable_runtime_fields_are_absent_and_repeated_output_is_identical(actual):
    encoded = json.dumps(actual, sort_keys=True)
    assert "timestamp" not in encoded
    assert "occurred_at" not in encoded
    assert "elapsed" not in encoded
    assert run_published_regression(SNAPSHOT) == actual


def test_runner_uses_no_network_and_does_not_modify_snapshot(monkeypatch):
    before = hashlib.sha256(SNAPSHOT.read_bytes()).digest()

    def refuse_network(*args, **kwargs):
        raise AssertionError("regression runner attempted network access")

    monkeypatch.setattr(socket, "create_connection", refuse_network)
    run_published_regression(SNAPSHOT)
    assert hashlib.sha256(SNAPSHOT.read_bytes()).digest() == before


def test_cli_returns_nonzero_for_regression_and_never_updates_golden(tmp_path, monkeypatch, capsys):
    from scripts import run_regression

    changed = json.loads(EXPECTED.read_text(encoding="utf-8"))
    changed["analytics"]["hpi_change_pct"] = -1
    expected_copy = tmp_path / "expected.json"
    expected_copy.write_text(json.dumps(changed), encoding="utf-8")
    before = expected_copy.read_bytes()
    monkeypatch.setattr(run_regression, "EXPECTED", expected_copy)
    assert run_regression.main() == 1
    assert "absolute difference" in capsys.readouterr().out
    assert expected_copy.read_bytes() == before
