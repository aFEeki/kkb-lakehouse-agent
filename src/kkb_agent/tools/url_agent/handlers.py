"""Deterministic extraction handlers for content types that need no model call."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from kkb_agent.tools.url_agent.models import (
    ContentExtractionError,
    DocumentKind,
    ExtractedLink,
    ExtractedTable,
    URLDocument,
)
from kkb_agent.tools.url_safety import UntrustedContent

_SKIPPED_HTML_TAGS = ("script", "style", "noscript", "template")


@dataclass(frozen=True)
class ExtractionLimits:
    """Bounds applied to untrusted documents so one source cannot exhaust a turn."""

    max_text_chars: int = 200_000
    max_tables: int = 50
    max_table_rows: int = 2_000
    max_table_columns: int = 200
    max_links: int = 500

    def __post_init__(self) -> None:
        for name in (
            "max_text_chars",
            "max_tables",
            "max_table_rows",
            "max_table_columns",
            "max_links",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} has an invalid limit")


DEFAULT_LIMITS = ExtractionLimits()


def content_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _truncate_text(text: str, limits: ExtractionLimits) -> tuple[str, tuple[str, ...]]:
    if len(text) <= limits.max_text_chars:
        return text, ()
    return text[: limits.max_text_chars], (
        f"Text truncated to the {limits.max_text_chars} character limit.",
    )


def extract_html(
    content: UntrustedContent, *, limits: ExtractionLimits = DEFAULT_LIMITS
) -> URLDocument:
    """Extract visible text, tables and absolute links from static HTML bytes."""

    try:
        soup = BeautifulSoup(content.body, "lxml")
    except Exception as exc:  # pragma: no cover - lxml raises parser-specific errors
        raise ContentExtractionError("HTML content could not be parsed") from exc

    for tag in soup.find_all(_SKIPPED_HTML_TAGS):
        tag.decompose()

    text, notes = _truncate_text(soup.get_text(separator="\n", strip=True), limits)
    tables = _html_tables(soup, limits)
    links = _html_links(soup, content.final_url, limits)
    if len(tables) == limits.max_tables:
        notes += (f"Tables truncated to the {limits.max_tables} table limit.",)
    if len(links) == limits.max_links:
        notes += (f"Links truncated to the {limits.max_links} link limit.",)

    return URLDocument(
        requested_url=content.requested_url,
        final_url=content.final_url,
        content_type=content.content_type,
        kind=DocumentKind.HTML,
        extraction_method="beautifulsoup-lxml",
        text=text,
        tables=tables,
        links=links,
        byte_count=len(content.body),
        content_sha256=content_sha256(content.body),
        notes=notes,
    )


def _html_tables(soup: BeautifulSoup, limits: ExtractionLimits) -> tuple[ExtractedTable, ...]:
    tables: list[ExtractedTable] = []
    for element in soup.find_all("table")[: limits.max_tables]:
        caption_tag = element.find("caption")
        caption = caption_tag.get_text(strip=True) if caption_tag else None
        header: tuple[str, ...] = ()
        rows: list[tuple[str, ...]] = []
        for row in element.find_all("tr")[: limits.max_table_rows]:
            cells = row.find_all(["th", "td"])[: limits.max_table_columns]
            values = tuple(cell.get_text(" ", strip=True) for cell in cells)
            if not values:
                continue
            if not header and all(cell.name == "th" for cell in cells):
                header = values
                continue
            rows.append(values)
        tables.append(ExtractedTable(caption=caption or None, header=header, rows=tuple(rows)))
    return tuple(tables)


def _html_links(
    soup: BeautifulSoup, base_url: str, limits: ExtractionLimits
) -> tuple[ExtractedLink, ...]:
    links: list[ExtractedLink] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(ExtractedLink(text=anchor.get_text(" ", strip=True), url=absolute))
        if len(links) == limits.max_links:
            break
    return tuple(links)


def extract_text(
    content: UntrustedContent, *, limits: ExtractionLimits = DEFAULT_LIMITS
) -> URLDocument:
    """Decode a plain-text or CSV body, recording which encoding actually worked."""

    decoded: str | None = None
    encoding_used = ""
    # The fetcher drops the charset parameter, so the encoding is re-established here.
    # cp1254 is the common Turkish legacy fallback and never fails, so it is tried last.
    for encoding in ("utf-8", "cp1254"):
        try:
            decoded = content.body.decode(encoding)
            encoding_used = encoding
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:  # pragma: no cover - cp1254 decodes any byte sequence
        raise ContentExtractionError("Text content could not be decoded")

    text, notes = _truncate_text(decoded, limits)
    return URLDocument(
        requested_url=content.requested_url,
        final_url=content.final_url,
        content_type=content.content_type,
        kind=DocumentKind.TEXT,
        extraction_method=f"decode-{encoding_used}",
        text=text,
        byte_count=len(content.body),
        content_sha256=content_sha256(content.body),
        notes=notes,
    )


def extract_excel(
    content: UntrustedContent, *, limits: ExtractionLimits = DEFAULT_LIMITS
) -> URLDocument:
    """Extract every worksheet of an .xlsx or .xls workbook as a text table."""

    if _is_ole2(content.body):
        tables = _legacy_xls_tables(content.body, limits)
        method = "xlrd"
    else:
        tables = _xlsx_tables(content.body, limits)
        method = "openpyxl"

    notes: tuple[str, ...] = ()
    if len(tables) == limits.max_tables:
        notes += (f"Sheets truncated to the {limits.max_tables} table limit.",)
    return URLDocument(
        requested_url=content.requested_url,
        final_url=content.final_url,
        content_type=content.content_type,
        kind=DocumentKind.EXCEL,
        extraction_method=method,
        tables=tables,
        byte_count=len(content.body),
        content_sha256=content_sha256(content.body),
        notes=notes,
    )


def _xlsx_tables(body: bytes, limits: ExtractionLimits) -> tuple[ExtractedTable, ...]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(BytesIO(body), read_only=True, data_only=True)
    except Exception as exc:
        raise ContentExtractionError("Workbook content is malformed and cannot be read") from exc
    try:
        tables = []
        for sheet in workbook.worksheets[: limits.max_tables]:
            rows = []
            for row in sheet.iter_rows(max_row=limits.max_table_rows, values_only=True):
                values = tuple(
                    "" if cell is None else str(cell) for cell in row[: limits.max_table_columns]
                )
                if any(values):
                    rows.append(values)
            tables.append(ExtractedTable(caption=sheet.title, rows=tuple(rows)))
        return tuple(tables)
    finally:
        workbook.close()


def _legacy_xls_tables(body: bytes, limits: ExtractionLimits) -> tuple[ExtractedTable, ...]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=body)
    except Exception as exc:
        raise ContentExtractionError("Workbook content is malformed and cannot be read") from exc
    tables = []
    for sheet in book.sheets()[: limits.max_tables]:
        rows = []
        for index in range(min(sheet.nrows, limits.max_table_rows)):
            values = tuple(
                "" if cell is None else str(cell)
                for cell in sheet.row_values(index)[: limits.max_table_columns]
            )
            if any(values):
                rows.append(values)
        tables.append(ExtractedTable(caption=sheet.name, rows=tuple(rows)))
    return tuple(tables)


def _is_ole2(body: bytes) -> bool:
    return body[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
