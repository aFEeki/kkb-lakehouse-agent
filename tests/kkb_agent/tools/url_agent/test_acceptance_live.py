"""Acceptance checks against real published sources.

These hit the live internet, so they are opt-in: set `KKB_NETWORK_TESTS=1` to run them.
CI and the default `pytest -q` run skip them, which keeps every push from touching a
publisher we depend on for the demo.

Recorded expectations are what the source actually returned on 2026-09-15. A publisher is
free to change its pages; a failure here means "go and look", not "the code is broken".
"""

from __future__ import annotations

import os

import pytest

from kkb_agent.tools.url_agent import (
    DocumentKind,
    create_content_type_router,
    discover_document,
)
from kkb_agent.tools.url_safety import SafeURLFetcher

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not os.environ.get("KKB_NETWORK_TESTS"),
        reason="live-source acceptance test; set KKB_NETWORK_TESTS=1 to run",
    ),
]

METALS_LANDING = (
    "https://www.borsaistanbul.com/veriler/"
    "kiymetli-madenler-ve-kiymetli-taslar-piyasasi/piyasa-verileri"
)
GOLD_PDF = "https://www.borsaistanbul.com/dosyalar/kmtp/veriler/kmp_au.pdf"


@pytest.fixture
def fetcher():
    with SafeURLFetcher() as client:
        yield client


def test_gold_trading_pdf_is_found_from_the_landing_page_and_read(fetcher):
    """The published demo scenario, end to end: landing page -> gold PDF -> numbers."""

    result = discover_document(
        METALS_LANDING,
        query="altın işlemleri",
        fetcher=fetcher,
        router=create_content_type_router(),
    )

    landing, target = result.hops
    assert landing.kind is DocumentKind.HTML
    assert landing.followed_url == GOLD_PDF
    assert "link text matched" in landing.reason

    document = result.document
    assert document.kind is DocumentKind.PDF
    assert document.final_url == GOLD_PDF
    # A text layer is present, so OCR must not be needed for this source.
    assert document.extraction_method == "pypdf"
    assert target.kind is DocumentKind.PDF

    # Observed 2026-09-15: a monthly gold trading table in TL, USD and EUR.
    text = document.text
    assert "ALTIN" in text.upper()
    assert "TOPLAM" in text.upper()
    for month in ("Ocak", "Şubat", "Mart"):
        assert month in text
    # Turkish thousands separators must survive extraction unmangled.
    assert "93.824.682.381" in text


def test_the_index_page_serves_metadata_statically_but_not_its_values(fetcher):
    """Evidence for SCRUM-63: does xtumy need a browser, or not?

    Observed 2026-09-15 by static fetch: the index *metadata* table is fully present, but
    the "Güncel Endeks Değerleri" table has its header row and no value row. The current
    values are therefore filled in by JavaScript, which is the half of the comparison the
    SCRUM-54 probe could not run. Rendering the DOM to confirm where the values come from
    still belongs to SCRUM-63.
    """

    document = create_content_type_router().route(
        fetcher.fetch("https://www.borsaistanbul.com/endeks/xtumy")
    )

    assert document.kind is DocumentKind.HTML

    metadata = {row[0]: row[1] for row in document.tables[0].rows if len(row) == 2}
    assert metadata["Endeks Kodu"] == "XTUMY"
    assert metadata["Endeks Adı"] == "BIST TUM-100"
    assert metadata["ISIN Kodu"] == "TRAIMKB01380"

    values_table = document.tables[1]
    assert values_table.rows == (("", "Güncel Endeks Değerleri", "Önceki Kapanış"),), (
        "the static response now carries a value row; re-check whether SCRUM-63 still "
        "needs a browser for this page"
    )
