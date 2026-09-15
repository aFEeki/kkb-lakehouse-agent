"""Read a file a human handed over, through the same parsers a fetch would use."""

from __future__ import annotations

from pydantic import Field

from kkb_agent.frame._base import Contract, Identifier, Timestamp
from kkb_agent.tools.url_agent.handlers import content_sha256
from kkb_agent.tools.url_agent.models import URLAgentError, URLDocument
from kkb_agent.tools.url_agent.router import ContentTypeRouter
from kkb_agent.tools.url_safety import UntrustedContent

DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Stated on the document itself, not only on the wrapper, so a consumer that reads nothing
# but `notes` still cannot present this as something the system fetched.
UPLOAD_NOTE = (
    "Operator-supplied upload: this document was handed to the system by a person, not "
    "fetched from a live source."
)


class UploadError(URLAgentError):
    """The uploaded file cannot be accepted."""


class UploadTooLargeError(UploadError):
    """The uploaded file exceeds the configured byte limit."""


class UploadProvenance(Contract):
    """Who supplied a file, when, and exactly which bytes."""

    filename: str = Field(min_length=1)
    uploaded_by: Identifier
    uploaded_at: Timestamp
    byte_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    declared_content_type: str | None = None


class UploadedDocument(Contract):
    """An extracted document plus the record that a human, not a fetch, produced it."""

    document: URLDocument
    provenance: UploadProvenance


def read_upload(
    filename: str,
    body: bytes,
    *,
    router: ContentTypeRouter,
    uploaded_by: str,
    uploaded_at,
    declared_content_type: str | None = None,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> UploadedDocument:
    """Extract an uploaded file exactly as if it had been fetched.

    The bytes go through the same `ContentTypeRouter`, so a PDF still gets pypdf and its
    OCR fallback, an Excel workbook still gets openpyxl, and an unreadable type is still
    refused rather than guessed at. `declared_content_type` is whatever the upload claimed
    and may be absent: the router falls back to the leading bytes, which for an upload is
    the more trustworthy signal anyway, since a filename extension is chosen by whoever
    named the file.

    `uploaded_at` is a parameter rather than a clock read inside, so provenance is
    supplied by the caller that actually observed it and stays reproducible in tests.
    """

    if not isinstance(body, (bytes, bytearray)):
        raise UploadError("Upload body must be bytes")
    body = bytes(body)
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    if len(body) > max_bytes:
        raise UploadTooLargeError(
            f"Upload {filename!r} is {len(body)} bytes, over the limit of {max_bytes}"
        )
    if not body:
        raise UploadError(f"Upload {filename!r} is empty")

    digest = content_sha256(body)
    reference = f"upload://{filename}"
    document = router.route(
        UntrustedContent(
            requested_url=reference,
            final_url=reference,
            content_type=declared_content_type,
            body=body,
            redirect_count=0,
        )
    )
    document = URLDocument.model_validate(
        {**document.model_dump(), "notes": (UPLOAD_NOTE, *document.notes)}
    )
    return UploadedDocument(
        document=document,
        provenance=UploadProvenance(
            filename=filename,
            uploaded_by=uploaded_by,
            uploaded_at=uploaded_at,
            byte_count=len(body),
            content_sha256=digest,
            declared_content_type=declared_content_type,
        ),
    )
