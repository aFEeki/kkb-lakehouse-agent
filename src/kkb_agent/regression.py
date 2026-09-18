"""Offline regression harness for the published three-turn analysis."""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from kkb_agent.agent.turn1 import BALANCE_KEY
from kkb_agent.agent.turn2 import CPI_KEY
from kkb_agent.agent.turn2 import QUESTION as TURN_TWO_QUESTION
from kkb_agent.agent.turn3 import HPI_KEY
from kkb_agent.agent.turn3 import QUESTION as TURN_THREE_QUESTION
from kkb_agent.api.contracts import AskRequest, ResultEvent
from kkb_agent.api.frame_store import AnalysisFrameStore
from kkb_agent.api.turn1_runner import TurnOneAskRunner
from kkb_agent.catalog.schema import CATALOG_DDL, OBSERVATIONS_DDL

ANALYSIS_ID = "published-three-turn-regression"
TURN_ONE_QUESTION = "2021-2025 arasında konut kredileri ve faiz oranlarını aylık göster."
ABSOLUTE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class RegressionDifference:
    path: str
    expected: Any
    actual: Any
    absolute_difference: float | None = None
    allowed_tolerance: float | None = None


def build_snapshot_database(snapshot_path: Path, database_path: Path) -> None:
    """Materialize the immutable JSON snapshot as a disposable DuckDB catalog."""
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(CATALOG_DDL)
        connection.execute(OBSERVATIONS_DDL)
        for series in snapshot["series"]:
            connection.execute(
                "INSERT INTO series_catalog (series_id, source, source_ref, name_tr, "
                "raw_label, measure_type, sector_scope, currency_basis, unit_raw, "
                "unit_normalized, scale_factor, cumulative_mode, native_freq, "
                "aggregation_rule, observations, nonzero_observations, source_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'none', ?, ?, 60, 60, ?)",
                [
                    series["series_id"],
                    series["source"],
                    series["series_id"],
                    series["name_tr"],
                    series["name_tr"],
                    series["measure_type"],
                    series["sector_scope"],
                    series["currency_basis"],
                    series["unit_raw"],
                    series["unit_normalized"],
                    series["native_freq"],
                    series["aggregation_rule"],
                    snapshot["source_hash"],
                ],
            )
        series_ids = [series["series_id"] for series in snapshot["series"]]
        for row in snapshot["observations"]:
            for series_id, value in zip(series_ids, row[1:], strict=True):
                connection.execute(
                    "INSERT INTO series_observations VALUES (?, ?, ?, ?)",
                    [series_id, row[0], value, value],
                )
    finally:
        connection.close()


async def _result_frame(runner: TurnOneAskRunner, version: int, question: str):
    events = [
        event
        async for event in runner.run(
            AskRequest(analysis_id=ANALYSIS_ID, version=version, question=question)
        )
    ]
    failures = [event for event in events if event.type == "error"]
    if failures:
        raise RuntimeError(f"Published turn failed: {failures[0].payload.code}")
    if not events or events[-1].type != "completion" or events[-1].payload.outcome != "succeeded":
        raise RuntimeError("Published turn did not complete successfully")
    result = next((event for event in events if isinstance(event, ResultEvent)), None)
    if result is None:
        raise RuntimeError("Published turn emitted no result")
    return result.payload.frame


async def _stale_version_is_rejected(runner: TurnOneAskRunner, version: int) -> bool:
    events = [
        event
        async for event in runner.run(
            AskRequest(analysis_id=ANALYSIS_ID, version=version, question=TURN_THREE_QUESTION)
        )
    ]
    return (
        [event.type for event in events] == ["error", "completion"]
        and events[0].payload.code == "ANALYSIS_VERSION_NOT_FOUND"
        and events[-1].frame_version == version
    )


def _column(frame, key):
    return next(column for column in frame.columns if column.key == key)


def _source_ids(column) -> list[str]:
    identifiers = [source.reference for source in column.lineage.sources]
    for parent in column.lineage.parents:
        identifiers.extend(_source_ids_from_lineage(parent.lineage))
    return identifiers


def _source_ids_from_lineage(lineage) -> list[str]:
    identifiers = [source.reference for source in lineage.sources]
    for parent in lineage.parents:
        identifiers.extend(_source_ids_from_lineage(parent.lineage))
    return identifiers


def _column_contract(column) -> dict[str, Any]:
    observed = [value for value in column.values if value is not None]
    return {
        "key": column.key,
        "first": observed[0],
        "last": observed[-1],
        "missing_count": column.missing_count,
        "source_ids": sorted(set(_source_ids(column))),
        "transformations": [
            {
                "name": transformation.name,
                "parameters": {item.key: item.value for item in transformation.parameters},
            }
            for transformation in column.lineage.transformations
        ],
    }


def _finding_contract(finding) -> dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "frame_version": finding.frame_version,
        "producing_tool": finding.producing_tool,
        "supporting_column_keys": list(finding.supporting_column_keys),
        "supersedes": finding.supersedes,
        "statement": finding.statement,
    }


def _turn_contract(frame, shared_spine) -> dict[str, Any]:
    return {
        "analysis_id": frame.frame_id,
        "version": frame.version,
        "row_count": len(frame.spine.values),
        "spine_matches_shared": frame.spine == shared_spine,
        "columns": [_column_contract(column) for column in frame.columns],
        "operations": [operation.kind.value for operation in frame.operations],
        "findings": [_finding_contract(finding) for finding in frame.findings],
    }


async def _execute(database_path: Path) -> dict[str, Any]:
    store = AnalysisFrameStore()
    runner = TurnOneAskRunner(database_path, planner=None, frame_store=store)
    frame1 = await _result_frame(runner, 0, TURN_ONE_QUESTION)
    frame2 = await _result_frame(runner, frame1.version, TURN_TWO_QUESTION)
    stale_version_rejected = await _stale_version_is_rejected(runner, frame1.version)
    frame3 = await _result_frame(runner, frame2.version, TURN_THREE_QUESTION)
    real_key = next(key for key in (c.key for c in frame2.columns) if "__deflated_by_" in key)
    real = _column(frame2, real_key)
    nominal = _column(frame2, BALANCE_KEY)
    cpi = _column(frame2, CPI_KEY)
    hpi = _column(frame3, HPI_KEY)
    return {
        "snapshot": "published-three-turn-fixed-v1",
        "spine": [value.isoformat() for value in frame1.spine.values],
        "stale_version_rejected": stale_version_rejected,
        "turns": [_turn_contract(frame, frame1.spine) for frame in (frame1, frame2, frame3)],
        "analytics": {
            "nominal_change_pct": (nominal.values[-1] - nominal.values[0])
            / abs(nominal.values[0])
            * 100,
            "cpi_change_pct": (cpi.values[-1] - cpi.values[0]) / abs(cpi.values[0]) * 100,
            "real_change_pct": (real.values[-1] - real.values[0]) / abs(real.values[0]) * 100,
            "hpi_change_pct": (hpi.values[-1] - hpi.values[0]) / abs(hpi.values[0]) * 100,
            "deflation_base_period": frame2.spine.values[0].isoformat(),
        },
    }


def run_published_regression(snapshot_path: Path) -> dict[str, Any]:
    """Execute the stateful production runner against a temporary snapshot database."""
    with tempfile.TemporaryDirectory(prefix="kkb-regression-") as directory:
        database = Path(directory) / "snapshot.duckdb"
        build_snapshot_database(snapshot_path, database)
        return asyncio.run(_execute(database))


def compare_contracts(
    expected: Any,
    actual: Any,
    *,
    tolerance: float = ABSOLUTE_TOLERANCE,
    path: str = "$",
) -> tuple[RegressionDifference, ...]:
    """Recursively compare curated contracts with one explicit float tolerance."""
    differences: list[RegressionDifference] = []
    if isinstance(expected, bool) or isinstance(actual, bool):
        if expected != actual:
            differences.append(RegressionDifference(path, expected, actual))
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        delta = abs(float(expected) - float(actual))
        if not math.isfinite(delta) or delta > tolerance:
            differences.append(RegressionDifference(path, expected, actual, delta, tolerance))
    elif isinstance(expected, Mapping) and isinstance(actual, Mapping):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}"
            if key not in expected:
                differences.append(RegressionDifference(child, "<absent>", actual[key]))
            elif key not in actual:
                differences.append(RegressionDifference(child, expected[key], "<absent>"))
            else:
                differences.extend(
                    compare_contracts(expected[key], actual[key], tolerance=tolerance, path=child)
                )
    elif isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
        if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes)):
            differences.append(RegressionDifference(path, expected, actual))
        else:
            if len(expected) != len(actual):
                differences.append(
                    RegressionDifference(f"{path}.length", len(expected), len(actual))
                )
            for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
                differences.extend(
                    compare_contracts(left, right, tolerance=tolerance, path=f"{path}[{index}]")
                )
    elif expected != actual:
        differences.append(RegressionDifference(path, expected, actual))
    return tuple(differences)
