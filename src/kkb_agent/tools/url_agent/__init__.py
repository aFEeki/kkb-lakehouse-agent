"""URL agent: route fetched content to the handler for its actual media type."""

from kkb_agent.tools.url_agent.handlers import (
    DEFAULT_LIMITS,
    ExtractionLimits,
    content_sha256,
    extract_excel,
    extract_html,
    extract_text,
)
from kkb_agent.tools.url_agent.models import (
    ContentExtractionError,
    DocumentKind,
    ExtractedLink,
    ExtractedTable,
    UnimplementedContentTypeError,
    UnsupportedContentTypeError,
    URLAgentError,
    URLDocument,
)
from kkb_agent.tools.url_agent.router import (
    GENERIC_MEDIA_TYPES,
    MEDIA_TYPE_KINDS,
    ContentHandler,
    ContentTypeRouter,
    create_content_type_router,
    sniff_kind,
)

__all__ = [
    "DEFAULT_LIMITS",
    "GENERIC_MEDIA_TYPES",
    "MEDIA_TYPE_KINDS",
    "ContentExtractionError",
    "ContentHandler",
    "ContentTypeRouter",
    "DocumentKind",
    "ExtractedLink",
    "ExtractedTable",
    "ExtractionLimits",
    "URLAgentError",
    "URLDocument",
    "UnimplementedContentTypeError",
    "UnsupportedContentTypeError",
    "content_sha256",
    "create_content_type_router",
    "extract_excel",
    "extract_html",
    "extract_text",
    "sniff_kind",
]
