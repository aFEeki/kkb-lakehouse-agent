"""Dispatch fetched bytes to the handler for their actual media type."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from kkb_agent.tools.url_agent.handlers import extract_excel, extract_html, extract_text
from kkb_agent.tools.url_agent.models import (
    ContentExtractionError,
    DocumentKind,
    UnimplementedContentTypeError,
    UnsupportedContentTypeError,
    URLAgentError,
    URLDocument,
)
from kkb_agent.tools.url_safety import UntrustedContent

ContentHandler = Callable[[UntrustedContent], URLDocument]

# Declared media type to document kind. The request URL and its file extension are never
# consulted: a publisher that serves a PDF from a .aspx path must still route as a PDF.
MEDIA_TYPE_KINDS: Mapping[str, DocumentKind] = MappingProxyType(
    {
        "text/html": DocumentKind.HTML,
        "application/xhtml+xml": DocumentKind.HTML,
        "application/pdf": DocumentKind.PDF,
        "application/x-pdf": DocumentKind.PDF,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": DocumentKind.EXCEL,
        "application/vnd.ms-excel": DocumentKind.EXCEL,
        "image/png": DocumentKind.IMAGE,
        "image/jpeg": DocumentKind.IMAGE,
        "image/webp": DocumentKind.IMAGE,
        "image/gif": DocumentKind.IMAGE,
        "image/tiff": DocumentKind.IMAGE,
        "text/plain": DocumentKind.TEXT,
        "text/csv": DocumentKind.TEXT,
    }
)

# Types that carry no information about the payload, so the bytes themselves decide.
GENERIC_MEDIA_TYPES = frozenset({"application/octet-stream", "binary/octet-stream"})


def sniff_kind(body: bytes) -> DocumentKind | None:
    """Identify a document from its leading bytes, or return None when unrecognised."""

    if body.startswith(b"%PDF-"):
        return DocumentKind.PDF
    if body[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # OLE2 compound file, legacy .xls
        return DocumentKind.EXCEL
    if body.startswith(b"PK\x03\x04") and b"xl/" in body[:4096]:
        return DocumentKind.EXCEL
    if body.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")):
        return DocumentKind.IMAGE
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return DocumentKind.IMAGE
    head = body[:1024].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html", b"<?xml")):
        return DocumentKind.HTML
    return None


class ContentTypeRouter:
    """Route untrusted content by its actual media type, never by its file extension."""

    def __init__(self, handlers: Mapping[DocumentKind, ContentHandler]):
        checked: dict[DocumentKind, ContentHandler] = {}
        for kind, handler in handlers.items():
            if type(kind) is not DocumentKind:
                raise UnsupportedContentTypeError(f"Registry key {kind!r} is not a DocumentKind")
            if not callable(handler):
                raise TypeError(f"Handler for {kind.value!r} must be callable")
            checked[kind] = handler
        self._handlers = MappingProxyType(checked)

    @property
    def supported_kinds(self) -> frozenset[DocumentKind]:
        return frozenset(self._handlers)

    def resolve_kind(self, content: UntrustedContent) -> DocumentKind:
        """Decide what a response actually is, from its media type and then its bytes."""

        media_type = (content.content_type or "").strip().lower()
        if media_type and media_type not in GENERIC_MEDIA_TYPES:
            kind = MEDIA_TYPE_KINDS.get(media_type)
            if kind is not None:
                return kind
            sniffed = sniff_kind(content.body)
            if sniffed is not None:
                return sniffed
            raise UnsupportedContentTypeError(
                f"Content type {media_type!r} from {content.final_url!r} is not a document "
                "type this tool can read"
            )

        sniffed = sniff_kind(content.body)
        if sniffed is None:
            declared = media_type or "an absent content type"
            raise UnsupportedContentTypeError(
                f"{content.final_url!r} returned {declared} and its bytes match no known "
                "document format"
            )
        return sniffed

    def route(self, content: UntrustedContent) -> URLDocument:
        """Extract one fetched document, or fail with a reason instead of guessing."""

        if not isinstance(content, UntrustedContent):
            raise TypeError("content must be an UntrustedContent instance")

        kind = self.resolve_kind(content)
        handler = self._handlers.get(kind)
        if handler is None:
            raise UnimplementedContentTypeError(
                f"{kind.value!r} content from {content.final_url!r} is recognised but no "
                "handler is registered in this composition"
            )

        try:
            document = handler(content)
        except URLAgentError:
            raise
        except Exception as exc:
            raise ContentExtractionError(
                f"Handler for {kind.value!r} failed on {content.final_url!r}: {exc}"
            ) from exc

        if not isinstance(document, URLDocument):
            raise ContentExtractionError(f"Handler for {kind.value!r} did not return a URLDocument")
        if document.kind is not kind:
            raise ContentExtractionError(
                f"Handler for {kind.value!r} returned a {document.kind.value!r} document"
            )
        return document


def create_content_type_router(
    *,
    ocr: object | None = None,
    pdf_handler: ContentHandler | None = None,
    image_handler: ContentHandler | None = None,
) -> ContentTypeRouter:
    """Build a router with every handler this composition can currently serve.

    PDF handling is always registered: a PDF with a text layer is read by pypdf with no
    model call at all. Passing `ocr` additionally enables the scanned-PDF fallback; without
    it, a PDF with no text layer fails with `PDFTextLayerMissingError` instead of returning
    an empty document. Image extraction is OCR-only, so it is registered only when an OCR
    backend is supplied. `pdf_handler`/`image_handler` override both, for tests and for a
    future handler that needs different wiring.
    """

    from kkb_agent.tools.url_agent.ocr import extract_image
    from kkb_agent.tools.url_agent.pdf import extract_pdf

    handlers: dict[DocumentKind, ContentHandler] = {
        DocumentKind.HTML: extract_html,
        DocumentKind.EXCEL: extract_excel,
        DocumentKind.TEXT: extract_text,
        DocumentKind.PDF: pdf_handler or (lambda content: extract_pdf(content, ocr=ocr)),
    }
    if image_handler is not None:
        handlers[DocumentKind.IMAGE] = image_handler
    elif ocr is not None:
        handlers[DocumentKind.IMAGE] = lambda content: extract_image(content, ocr=ocr)
    return ContentTypeRouter(handlers)
