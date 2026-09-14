"""SCRUM-29 (I8) - every series says what it was built from and when it was fetched.

Provenance is per source, not per series. A monthly series is assembled from 66 files, so
a file-level hash would not identify it; what a reader drilling into a number needs is
which acquisition produced it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from kkb_agent.catalog.build import bronze_provenance


def manifest(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return path


def row(sha: str, fetched: str) -> dict:
    return {"sha256": sha, "fetched_at": fetched, "http_status": 200}


class TestBronzeProvenance:
    def test_digest_and_freshness_come_off_the_manifest(self, tmp_path: Path):
        m = manifest(
            tmp_path / "m.jsonl",
            [
                row("aaa", "2026-09-13T10:00:00+00:00"),
                row("bbb", "2026-09-13T20:40:00+00:00"),
            ],
        )
        p = bronze_provenance(m, "bddk_aylik")

        assert p.files == 2
        assert p.retrieved_at == datetime(2026, 9, 13, 20, 40, tzinfo=UTC)
        assert len(p.digest) == 64

    def test_the_digest_does_not_depend_on_crawl_order(self, tmp_path: Path):
        """A re-crawl fetching the same bytes in a different order has to produce the
        same digest, or the hash answers "same run" rather than "same data"."""
        first = manifest(
            tmp_path / "a.jsonl",
            [row("aaa", "2026-09-13T10:00:00+00:00"), row("bbb", "2026-09-13T11:00:00+00:00")],
        )
        second = manifest(
            tmp_path / "b.jsonl",
            [row("bbb", "2026-09-14T09:00:00+00:00"), row("aaa", "2026-09-14T08:00:00+00:00")],
        )
        assert bronze_provenance(first, "x").digest == bronze_provenance(second, "x").digest

    def test_changed_bytes_change_the_digest(self, tmp_path: Path):
        before = manifest(tmp_path / "a.jsonl", [row("aaa", "2026-09-13T10:00:00+00:00")])
        after = manifest(tmp_path / "b.jsonl", [row("zzz", "2026-09-13T10:00:00+00:00")])
        assert bronze_provenance(before, "x").digest != bronze_provenance(after, "x").digest

    def test_freshness_is_the_newest_fetch_not_the_last_line(self, tmp_path: Path):
        m = manifest(
            tmp_path / "m.jsonl",
            [row("aaa", "2026-09-13T20:40:00+00:00"), row("bbb", "2026-09-13T10:00:00+00:00")],
        )
        assert bronze_provenance(m, "x").retrieved_at.hour == 20

    def test_a_missing_manifest_is_none_rather_than_an_empty_digest(self, tmp_path: Path):
        """An empty digest would read as "built from nothing verified", which is a
        different and misleading claim from "no manifest to check"."""
        assert bronze_provenance(tmp_path / "absent.jsonl", "x") is None

    def test_a_manifest_with_no_hashes_is_none(self, tmp_path: Path):
        m = manifest(
            tmp_path / "m.jsonl", [{"http_status": 500, "fetched_at": "2026-09-13T10:00:00+00:00"}]
        )
        assert bronze_provenance(m, "x") is None

    def test_a_corrupt_line_does_not_lose_the_whole_manifest(self, tmp_path: Path):
        path = tmp_path / "m.jsonl"
        path.write_text(
            json.dumps(row("aaa", "2026-09-13T10:00:00+00:00"))
            + "\n{ not json\n"
            + json.dumps(row("bbb", "2026-09-13T11:00:00+00:00"))
            + "\n",
            encoding="utf-8",
        )
        assert bronze_provenance(path, "x").files == 2
