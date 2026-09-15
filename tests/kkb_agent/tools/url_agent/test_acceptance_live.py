"""Acceptance checks against real published sources.

These hit the live internet, so they are opt-in: set `KKB_NETWORK_TESTS=1` to run them.
CI and the default `pytest -q` run skip them, which keeps every push from touching a
publisher we depend on for the demo.

Recorded expectations are what the source actually returned on 2026-09-15. A publisher is
free to change its pages; a failure here means "go and look", not "the code is broken".
"""

from __future__ import annotations

import os
import zlib
from io import BytesIO
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest

from kkb_agent.config import Settings
from kkb_agent.llm.client import MIAClient
from kkb_agent.tools.url_agent import (
    DocumentKind,
    MIAOCRBackend,
    create_content_type_router,
    discover_document,
    extract_pdf,
)
from kkb_agent.tools.url_safety import SafeURLFetcher, UntrustedContent

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

# A different published document from the one the handlers were built against, so the OCR
# checks are not measuring a file anybody tuned anything to.
GOLD_IMPORT_PDF = "https://www.borsaistanbul.com/dosyalar/kmtp/veriler/ith_au.pdf"

EVDS_CSV = (
    "https://evds3.tcmb.gov.tr/igmevdsms-dis/series=TP.DK.USD.S"
    "&startDate=01-01-2026&endDate=10-01-2026&type=csv"
)


REPO_ROOT = Path(__file__).resolve().parents[4]


def live_settings() -> Settings:
    """Read the developer's real `.env`, on purpose.

    `conftest.py` isolates every test from it deliberately, and that default is right:
    a unit test that quietly depends on someone's credentials is a test that passes on
    one machine. These acceptance checks are the one place that must not be isolated --
    proving the real key reaches the real service is the whole point of them -- so they
    name the file explicitly rather than relying on ambient state.
    """

    return Settings(_env_file=REPO_ROOT / ".env")


def _configured(value) -> bool:
    key = value.get_secret_value().strip()
    return bool(key) and key != "API_KEYINIZ"


needs_mia = pytest.mark.skipif(
    not _configured(live_settings().mia_api_key), reason="MIA_API_KEY is not configured"
)


@pytest.fixture
def fetcher():
    with SafeURLFetcher() as client:
        yield client


class RenderedPage(NamedTuple):
    png: bytes
    raw_rgb: bytes
    width: int
    height: int


@pytest.fixture(scope="module")
def gold_import_page() -> RenderedPage:
    """The first page of a real published PDF, rendered once for the OCR checks."""
    import pypdfium2

    from kkb_agent.tools.url_agent.pdf import RENDER_SCALE, encode_png

    with SafeURLFetcher() as client:
        body = client.fetch(GOLD_IMPORT_PDF).body
    document = pypdfium2.PdfDocument(BytesIO(body))
    try:
        pixels = document[0].render(scale=RENDER_SCALE, rev_byteorder=True).to_numpy()
    finally:
        document.close()
    height, width, _ = pixels.shape
    return RenderedPage(encode_png(pixels), pixels.tobytes(), width, height)


def _image_only_pdf(pixels: bytes, width: int, height: int) -> bytes:
    """Wrap raw RGB pixels in a one-page PDF that carries no text layer at all."""
    compressed = zlib.compress(pixels, 6)
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    image_id = add(
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n%s\nendstream"
        % (width, height, len(compressed), compressed)
    )
    stream = b"q %d 0 0 %d 0 0 cm /Im0 Do Q" % (width, height)
    content_id = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    pages_id = len(objects) + 2
    page_id = add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] "
        b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
        % (pages_id, width, height, image_id, content_id)
    )
    pages_obj = add(b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % page_id)
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_obj)

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % index + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets[1:]:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\n" % (len(objects) + 1, catalog_id))
    out.write(b"startxref\n%d\n%%%%EOF\n" % xref)
    return out.getvalue()


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


def test_evds_serves_its_csv_export_and_the_text_handler_reads_it():
    """A real text/csv source: TCMB's own API, with the project's EVDS key."""

    settings = live_settings()
    if not _configured(settings.evds_api_key):
        pytest.skip("EVDS_API_KEY is not configured")
    key = settings.evds_api_key.get_secret_value().strip()

    response = httpx.get(EVDS_CSV, headers={"key": key}, timeout=60)
    assert response.status_code == 200

    document = create_content_type_router().route(
        UntrustedContent(
            requested_url=EVDS_CSV,
            final_url=EVDS_CSV,
            content_type=response.headers.get("content-type", "").split(";")[0] or None,
            body=response.content,
            redirect_count=0,
        )
    )

    assert document.kind is DocumentKind.TEXT
    assert document.extraction_method == "decode-utf-8"
    # Observed 2026-09-15: the header row, and the USD rate published for 2026-01-02.
    assert document.text.splitlines()[0] == "Tarih,TP_DK_USD_S,UNIXTIME"
    assert "02-01-2026,42.92290000" in document.text


@needs_mia
def test_a_real_document_page_as_an_image_is_read_by_ocr(gold_import_page):
    """The image path, on a page of a published document rather than a made-up picture."""

    backend = MIAOCRBackend(MIAClient(live_settings()))
    document = create_content_type_router(ocr=backend).route(
        UntrustedContent(
            requested_url="upload://ith_au-page-1.png",
            final_url="upload://ith_au-page-1.png",
            content_type="image/png",
            body=gold_import_page.png,
            redirect_count=0,
        )
    )

    assert document.kind is DocumentKind.IMAGE
    assert document.extraction_method == "unlimited-ocr"
    assert any("OCR" in note for note in document.notes)
    # Observed 2026-09-15, byte-identical across repeated runs at temperature 0.
    assert "ALTIN" in document.text.upper()
    assert "49.216,41" in document.text  # 2026 total, gold imports
    assert "7.791,56" in document.text  # January 2026
    # Layout tags must not survive into the text a reader or the narrative layer sees.
    assert "<|det|>" not in document.text
    assert "<|ref|>" not in document.text


@needs_mia
def test_a_scanned_pdf_falls_back_to_ocr_and_recovers_the_same_figures(gold_import_page):
    """A real page with its text layer removed: what a scanned bulletin looks like.

    No published source among the demo URLs ships a scanned PDF -- all 21 PDFs on the
    precious-metals page carry a text layer -- so the scanned case is built from a real
    page rather than from invented content.
    """

    scanned = _image_only_pdf(
        gold_import_page.raw_rgb, gold_import_page.width, gold_import_page.height
    )

    document = extract_pdf(
        UntrustedContent(
            requested_url="upload://ith_au-scanned.pdf",
            final_url="upload://ith_au-scanned.pdf",
            content_type="application/pdf",
            body=scanned,
            redirect_count=0,
        ),
        ocr=MIAOCRBackend(MIAClient(live_settings())),
    )

    assert document.kind is DocumentKind.PDF
    assert document.extraction_method == "unlimited-ocr"  # pypdf found nothing to read
    assert document.page_count == 1
    assert document.pages[0].extraction_method == "unlimited-ocr"
    assert "49.216,41" in document.text
    assert any("no text layer" in note for note in document.notes)


def test_no_public_excel_source_exists_among_the_demo_urls():
    """Recorded gap, not an oversight.

    SCRUM-64 asks for an acceptance test per content type against a real source. The
    published demo URLs link 21 PDFs and no workbook; kmp_au.xlsx and kmp_au.xls both
    return 404, and TCMB's EVDS API rejects `type=xlsx` with "Invalid type". The Excel
    path is therefore covered by unit tests over real openpyxl/xlrd output plus the
    real-world case of an HTML table published as .xls, which is what actually turns up
    in the wild. Revisit if a published workbook appears.
    """

    with SafeURLFetcher() as client:
        document = create_content_type_router().route(client.fetch(METALS_LANDING))

    workbooks = [
        link.url
        for link in document.links
        if link.url.split("?")[0].lower().endswith((".xlsx", ".xls"))
    ]
    assert workbooks == [], f"a workbook appeared; wire it into the acceptance tests: {workbooks}"
