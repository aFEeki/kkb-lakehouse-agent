"""PDF text extraction, falling back to OCR only when there is no text layer."""

from __future__ import annotations

import struct
import zlib
from io import BytesIO

import numpy as np
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from kkb_agent.tools.url_agent.handlers import DEFAULT_LIMITS, ExtractionLimits, content_sha256
from kkb_agent.tools.url_agent.models import (
    ContentExtractionError,
    DocumentKind,
    ExtractedPage,
    PDFTextLayerMissingError,
    URLDocument,
)
from kkb_agent.tools.url_agent.ocr import OCRBackend, OCRCache, ocr_images
from kkb_agent.tools.url_agent.timeouts import Deadline, ToolTimeoutError
from kkb_agent.tools.url_safety import UntrustedContent

RENDER_SCALE = 2.0


def extract_pdf(
    content: UntrustedContent,
    *,
    ocr: OCRBackend | None = None,
    cache: OCRCache | None = None,
    limits: ExtractionLimits = DEFAULT_LIMITS,
    deadline: Deadline | None = None,
) -> URLDocument:
    """Read a PDF with pypdf, using OCR only for a document with no text layer.

    Text extraction is exact, free and fast, so it is always attempted first. OCR runs
    only when every page came back empty, which is what a scanned document looks like.
    """

    try:
        reader = PdfReader(BytesIO(content.body))
        raw_pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, OSError, ValueError) as exc:
        raise ContentExtractionError("PDF content is malformed and cannot be read") from exc

    if any(page.strip() for page in raw_pages):
        pages = tuple(
            ExtractedPage(page_number=number, text=text.strip(), extraction_method="pypdf")
            for number, text in enumerate(raw_pages, start=1)
        )
        return _document(content, pages, "pypdf", limits, notes=())

    if ocr is None:
        raise PDFTextLayerMissingError(
            f"{content.final_url!r} has no text layer across {len(raw_pages)} page(s) and no "
            "OCR backend was supplied to read it"
        )

    if deadline is not None:
        deadline.require(f"rendering {len(raw_pages)} PDF page(s) for OCR")
    images = _render_pages(content.body)

    notes = ("PDF had no text layer; text was read by OCR and may contain reading errors.",)
    try:
        texts = ocr_images(images, ocr, cache, deadline=deadline)
    except ToolTimeoutError as expired:
        # Pages already read are worth more than a bare failure, as long as the document
        # says plainly that it is incomplete.
        partial = expired.partial
        if not isinstance(partial, tuple) or not any(partial):
            raise
        texts = partial
        read = sum(1 for text in texts if text)
        notes += (
            f"Timed out during OCR: {read} of {len(texts)} page(s) were read; the rest are "
            "missing, not empty.",
        )

    pages = tuple(
        ExtractedPage(page_number=number, text=text, extraction_method="unlimited-ocr")
        for number, text in enumerate(texts, start=1)
    )
    return _document(content, pages, "unlimited-ocr", limits, notes=notes)


def _document(
    content: UntrustedContent,
    pages: tuple[ExtractedPage, ...],
    method: str,
    limits: ExtractionLimits,
    *,
    notes: tuple[str, ...],
) -> URLDocument:
    text = "\n\n".join(page.text for page in pages if page.text)
    if len(text) > limits.max_text_chars:
        text = text[: limits.max_text_chars]
        notes += (f"Text truncated to the {limits.max_text_chars} character limit.",)
    return URLDocument(
        requested_url=content.requested_url,
        final_url=content.final_url,
        content_type=content.content_type,
        kind=DocumentKind.PDF,
        extraction_method=method,
        text=text,
        pages=pages,
        page_count=len(pages),
        byte_count=len(content.body),
        content_sha256=content_sha256(content.body),
        notes=notes,
    )


def _render_pages(body: bytes) -> tuple[bytes, ...]:
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(BytesIO(body))
    except Exception as exc:
        raise ContentExtractionError("PDF pages could not be rendered for OCR") from exc
    try:
        images = []
        for index in range(len(document)):
            # rev_byteorder gives RGB rather than pdfium's native BGR.
            bitmap = document[index].render(scale=RENDER_SCALE, rev_byteorder=True)
            images.append(encode_png(bitmap.to_numpy()))
        return tuple(images)
    except Exception as exc:
        raise ContentExtractionError("PDF pages could not be rendered for OCR") from exc
    finally:
        document.close()


def encode_png(pixels: np.ndarray) -> bytes:
    """Encode an (H, W, C) uint8 array as PNG.

    Written here rather than via Pillow so rendering adds no dependency outside the
    libraries the project already declares.
    """

    if pixels.ndim != 3 or pixels.dtype != np.uint8:
        raise ContentExtractionError("Rendered page is not an 8-bit image array")
    height, width, channels = pixels.shape
    try:
        color_type = {1: 0, 3: 2, 4: 6}[channels]
    except KeyError as exc:
        raise ContentExtractionError(f"Rendered page has {channels} channels") from exc

    # Each scanline is prefixed with filter type 0 (None), then the whole stream is deflated.
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        raw.extend(row.tobytes())

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
