from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from kkb_agent.agent.handlers import LoadedSeries
from kkb_agent.agent.turn1 import BALANCE_KEY, RATE_KEY
from kkb_agent.agent.turn2 import CPI_KEY, CPI_SERIES_ID, build_turn2
from kkb_agent.agent.turn3 import (
    HPI_KEY,
    HPI_SERIES_ID,
    REVISED_FINDING_ID,
    SUPERSEDED_FINDING_ID,
    _aligned_changes,
    build_turn3,
)
from kkb_agent.api.contracts import AskRequest
from kkb_agent.api.frame_store import (
    AnalysisFrameNotFoundError,
    AnalysisFrameStore,
    StaleAnalysisFrameError,
)
from kkb_agent.api.turn1_runner import TurnOneAskRunner
from kkb_agent.frame import (
    AnalysisFrame,
    Column,
    Finding,
    Lineage,
    Operation,
    SourceReference,
    Spine,
)

DATES = (date(2021, 1, 1), date(2021, 2, 1), date(2021, 3, 1))


def source_column(key, label, values, measure_type):
    return Column(
        key=key,
        label=label,
        dtype="number",
        values=values,
        measure_type=measure_type,
        origin="source",
        lineage=Lineage(sources=[SourceReference(source_type="fixture", reference=key)]),
    )


def turn1_frame():
    operations = tuple(
        Operation(
            operation_id=f"add-{key}",
            kind="add_column",
            parameters={"series_reference": reference, "column_key": key},
            timestamp=datetime(2026, 1, index, tzinfo=UTC),
            source_version=index - 1,
            resulting_version=index,
        )
        for index, (key, reference) in enumerate(
            ((BALANCE_KEY, "balance"), (RATE_KEY, "rate")), start=1
        )
    )
    return AnalysisFrame(
        frame_id="analysis-a",
        version=2,
        spine=Spine(values=DATES),
        columns=(
            source_column(BALANCE_KEY, "Konut kredisi bakiyesi", (100, 120, 150), "stock"),
            source_column(RATE_KEY, "Konut kredisi faizi", (20, 18, 17), "rate"),
        ),
        findings=(
            Finding(
                finding_id=SUPERSEDED_FINDING_ID,
                statement="Turn 1 evidence",
                frame_version=2,
                supporting_column_keys=(BALANCE_KEY,),
                producing_tool="turn1.arithmetic",
            ),
        ),
        operations=operations,
    )


class CPISource:
    def fetch(self, reference):
        if reference not in {CPI_SERIES_ID, HPI_SERIES_ID}:
            return None
        is_hpi = reference == HPI_SERIES_ID
        return LoadedSeries(
            series_id=reference,
            name="Konut Fiyat Endeksi" if is_hpi else "TÜFE",
            values=dict(zip(DATES, (80, None, 120) if is_hpi else (100, 125, 150), strict=True)),
            native_freq="M",
            aggregation_rule="period_end",
            measure_type="index",
            unit_raw="endeks",
            unit_normalized="index",
            scale_factor=1.0,
            source="evds",
            source_hash="a" * 64,
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_turn_two_is_a_two_operation_successor_without_mutating_turn_one():
    before = turn1_frame()
    frozen = before.model_dump_json()
    result = build_turn2(before, CPISource()).frame

    assert before.model_dump_json() == frozen
    assert result.frame_id == before.frame_id
    assert result.version == before.version + 2
    assert result.spine.model_dump_json() == before.spine.model_dump_json()
    assert len(result.spine.values) == len(before.spine.values)
    assert result.columns[: len(before.columns)] == before.columns
    assert [column.key for column in result.columns[:3]] == [BALANCE_KEY, RATE_KEY, CPI_KEY]
    derived = result.columns[-1]
    assert "deflated_by" in derived.key
    assert "TÜFE" in derived.label and "2021-01-01" in derived.label
    assert [parent.column_key for parent in derived.lineage.parents] == [BALANCE_KEY, CPI_KEY]
    assert result.findings == before.findings
    assert result.operations[:2] == before.operations
    assert [operation.kind.value for operation in result.operations[-2:]] == [
        "add_column",
        "deflate_column",
    ]


def test_analysis_frame_store_uses_exact_versions_and_rejects_stale_versions():
    store = AnalysisFrameStore()
    first = turn1_frame()
    store.put(first)
    assert store.get(first.frame_id, first.version) == first
    with pytest.raises(AnalysisFrameNotFoundError):
        store.get("unknown", first.version)
    later = build_turn2(first, CPISource()).frame
    store.put(later)
    with pytest.raises(StaleAnalysisFrameError):
        store.get_current(first.frame_id, first.version)


def test_turn_three_adds_only_hpi_and_appends_a_computed_finding_revision():
    turn1 = turn1_frame()
    turn2 = build_turn2(turn1, CPISource()).frame
    frozen = turn2.model_dump_json()

    turn3 = build_turn3(turn2, CPISource()).frame

    assert turn2.model_dump_json() == frozen
    assert turn3.frame_id == turn2.frame_id
    assert turn3.version == turn2.version + 1
    assert turn3.spine.model_dump_json() == turn2.spine.model_dump_json()
    assert len(turn3.spine.values) == len(turn2.spine.values)
    assert turn3.columns[:-1] == turn2.columns
    assert turn3.columns[-1].key == HPI_KEY
    assert turn3.columns[-1].values == (80.0, None, 120.0)
    assert turn3.columns[-1].lineage.sources[0].reference == HPI_SERIES_ID
    assert turn3.operations[:-1] == turn2.operations
    assert turn3.operations[-1].kind.value == "add_column"
    assert [finding.finding_id for finding in turn3.findings[-2:]] == [
        SUPERSEDED_FINDING_ID,
        REVISED_FINDING_ID,
    ]
    revision = turn3.findings[-1]
    assert revision.supersedes == SUPERSEDED_FINDING_ID
    assert revision.frame_version == turn3.version
    assert HPI_KEY in revision.supporting_column_keys
    assert "120.00" in revision.statement
    assert revision.spine_range.model_dump() == {"start": 0, "stop": 3}


def test_turn_three_changes_use_the_same_common_ragged_edge_dates():
    real = source_column("real", "Real", (None, 100, 120, 150, None), "stock")
    hpi = source_column("hpi", "HPI", (70, None, 90, 100, 110), "index")
    start, end, hpi_first, hpi_last, hpi_change, real_change = _aligned_changes(real, hpi)
    assert (start, end) == (2, 3)
    assert (hpi_first, hpi_last) == (90, 100)
    assert hpi_change == pytest.approx(100 / 9)
    assert real_change == pytest.approx(25)


def test_runner_retrieves_stored_turn_two_and_streams_turn_three_result():
    import asyncio

    from kkb_agent.agent.turn3 import TurnThreeResult

    store = AnalysisFrameStore()
    turn2 = build_turn2(turn1_frame(), CPISource()).frame
    turn3 = build_turn3(turn2, CPISource()).frame
    store.put(turn2)
    runner = TurnOneAskRunner("unused.duckdb", frame_store=store)
    request = AskRequest(
        analysis_id=turn2.frame_id,
        version=turn2.version,
        question="Bu tabloyu bozmadan konut fiyat endeksini ekle.",
    )

    with patch(
        "kkb_agent.api.turn1_runner.build_turn3", return_value=TurnThreeResult(turn3)
    ) as build:

        async def collect():
            return [event async for event in runner.run(request)]

        events = asyncio.run(collect())

    assert build.call_args.args[0] == turn2
    assert [event.type for event in events[-2:]] == ["result", "completion"]
    assert events[-1].payload.outcome == "succeeded"
    assert events[-2].payload.frame == turn3
    assert store.get(turn3.frame_id, turn3.version) == turn3
