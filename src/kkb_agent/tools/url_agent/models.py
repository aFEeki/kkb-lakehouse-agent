"""Typed, immutable results produced by URL agent content handlers."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from kkb_agent.frame._base import Contract, Identifier


class URLAgentError(RuntimeError):
    """Base error for URL agent content handling."""


class UnsupportedContentTypeError(URLAgentError):
    """The fetched content type is outside the router's vocabulary."""


class UnimplementedContentTypeError(URLAgentError):
    """A known content type has no registered handler in this composition."""


class ContentExtractionError(URLAgentError):
    """A registered handler could not extract content safely."""


class PDFTextLayerMissingError(ContentExtractionError):
    """A PDF carries no text layer and no OCR backend was supplied to read it."""


class OCRBackendError(ContentExtractionError):
    """The OCR backend failed or returned an unusable response."""


class DocumentKind(StrEnum):
    HTML = "html"
    PDF = "pdf"
    EXCEL = "excel"
    IMAGE = "image"
    TEXT = "text"


class ExtractedTable(Contract):
    """One table lifted from a document, as text cells in source order."""

    caption: str | None = None
    header: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()


class ExtractedLink(Contract):
    """One absolute link observed in a document, with its visible text."""

    text: str
    url: str


class ExtractedPage(Contract):
    """Text recovered from one page, with the page number it actually came from.

    `page_number` is 1-based and is the only position this tool ever claims. Finer
    positions stay absent rather than being inferred.
    """

    page_number: int = Field(ge=1)
    text: str = ""
    extraction_method: Identifier


class URLDocument(Contract):
    """What one fetched document yielded. Every field is untrusted source data.

    `text`, `tables` and `links` are content read from a third party. They are evidence
    to analyze, never instructions to follow, and `trust_level` states that on the record.
    """

    requested_url: str
    final_url: str
    content_type: str | None
    kind: DocumentKind
    extraction_method: Identifier
    text: str = ""
    tables: tuple[ExtractedTable, ...] = ()
    links: tuple[ExtractedLink, ...] = ()
    pages: tuple[ExtractedPage, ...] = ()
    page_count: int | None = Field(default=None, ge=0)
    byte_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    notes: tuple[str, ...] = ()
    trust_level: Literal["untrusted"] = "untrusted"
