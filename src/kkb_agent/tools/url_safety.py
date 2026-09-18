"""Safety boundary for fetching untrusted URL content."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

import httpx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

AddressResolver = Callable[[str, int], Iterable[str]]
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class URLSafetyError(RuntimeError):
    """Base error for content rejected at the URL safety boundary."""


class UnsafeURLError(URLSafetyError):
    """The URL targets a scheme or network address that is not allowed."""


class RedirectLimitError(URLSafetyError):
    """The response exceeded the configured redirect count."""


class URLFetchError(URLSafetyError):
    """The remote response could not be fetched safely."""


class FileSizeLimitError(URLSafetyError):
    """The response exceeded the configured byte count."""


class PDFValidationError(URLSafetyError):
    """A declared PDF could not be inspected safely."""


class EncryptedPDFError(PDFValidationError):
    """An encrypted PDF was rejected."""


class PDFPageLimitError(PDFValidationError):
    """A PDF exceeded the configured page count."""


@dataclass(frozen=True)
class URLSafetyLimits:
    """Deterministic resource limits applied by :class:`SafeURLFetcher`."""

    max_redirects: int = 5
    max_bytes: int = 10 * 1024 * 1024
    max_pdf_pages: int = 50

    def __post_init__(self) -> None:
        for name, value in (
            ("max_redirects", self.max_redirects),
            ("max_bytes", self.max_bytes),
            ("max_pdf_pages", self.max_pdf_pages),
        ):
            if type(value) is not int or value < (0 if name == "max_redirects" else 1):
                raise ValueError(f"{name} has an invalid limit")


@dataclass(frozen=True)
class UntrustedContent:
    """Immutable bytes returned as data, never as planner instructions."""

    requested_url: str
    final_url: str
    content_type: str | None
    body: bytes
    redirect_count: int
    page_count: int | None = None
    trust_level: Literal["untrusted"] = "untrusted"


def _system_resolver(host: str, port: int) -> tuple[str, ...]:
    addresses = (item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
    return tuple(dict.fromkeys(addresses))


class SafeURLFetcher:
    """Fetch public HTTP(S) content while enforcing URL and resource limits."""

    def __init__(
        self,
        *,
        limits: URLSafetyLimits | None = None,
        resolver: AddressResolver = _system_resolver,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if transport is not None and client is not None:
            raise ValueError("transport and client cannot both be supplied")
        self.limits = limits or URLSafetyLimits()
        self._resolver = resolver
        self._owns_client = client is None
        self._client = client or httpx.Client(
            follow_redirects=False,
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SafeURLFetcher:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def fetch(self, url: str) -> UntrustedContent:
        """Fetch one URL after validating the initial target and every redirect hop."""

        requested_url = url
        current = self._validated_url(url)
        redirect_count = 0

        while True:
            # Resolve immediately before every request, including every redirect target.
            self._validate_public_target(current)
            try:
                with self._client.stream("GET", current, follow_redirects=False) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise URLFetchError("Redirect response is missing a Location header")
                        if redirect_count >= self.limits.max_redirects:
                            raise RedirectLimitError(
                                f"URL exceeded the redirect limit of {self.limits.max_redirects}"
                            )
                        redirect_count += 1
                        current = self._validated_url(str(response.url.join(location)))
                        continue

                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise URLFetchError(
                            f"URL returned HTTP {exc.response.status_code}"
                        ) from exc

                    body = self._read_limited_body(response)
                    content_type = self._content_type(response)
            except URLSafetyError:
                raise
            except httpx.HTTPError as exc:
                raise URLFetchError("URL request failed") from exc

            page_count = self._pdf_page_count(body) if content_type == "application/pdf" else None
            return UntrustedContent(
                requested_url=requested_url,
                final_url=str(current),
                content_type=content_type,
                body=body,
                redirect_count=redirect_count,
                page_count=page_count,
            )

    def validate_public_url(self, url: str) -> str:
        """Validate and canonicalize a public result URL without fetching it."""
        parsed = self._validated_url(url).copy_with(fragment=None)
        self._validate_public_target(parsed)
        return str(parsed)

    def _validated_url(self, value: str) -> httpx.URL:
        try:
            parsed = httpx.URL(value)
            port = parsed.port
        except (httpx.InvalidURL, ValueError) as exc:
            raise UnsafeURLError("URL is invalid") from exc
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeURLError("Only HTTP(S) URLs are allowed")
        if not parsed.host:
            raise UnsafeURLError("URL must include a hostname")
        if parsed.userinfo:
            raise UnsafeURLError("URL credentials are not allowed")
        if port is not None and not 1 <= port <= 65535:
            raise UnsafeURLError("URL port is invalid")
        return parsed

    def _validate_public_target(self, url: httpx.URL) -> None:
        host = url.host
        if host is None:  # pragma: no cover - guaranteed by _validated_url
            raise UnsafeURLError("URL must include a hostname")
        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            resolved = tuple(self._resolver(host, port))
        except (OSError, ValueError) as exc:
            raise UnsafeURLError("URL hostname could not be resolved safely") from exc
        if not resolved:
            raise UnsafeURLError("URL hostname did not resolve to an address")
        for raw_address in resolved:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as exc:
                raise UnsafeURLError("URL hostname resolved to an invalid address") from exc
            if address.is_private or address.is_loopback or address.is_link_local:
                raise UnsafeURLError("Private, loopback and link-local targets are not allowed")
            if not address.is_global:
                raise UnsafeURLError("URL target must use a globally routable address")

    def _read_limited_body(self, response: httpx.Response) -> bytes:
        raw_length = response.headers.get("content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError as exc:
                raise URLFetchError("Response Content-Length is invalid") from exc
            if declared_length < 0:
                raise URLFetchError("Response Content-Length is invalid")
            if declared_length > self.limits.max_bytes:
                raise FileSizeLimitError(
                    f"Response exceeds the byte limit of {self.limits.max_bytes}"
                )

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self.limits.max_bytes:
                raise FileSizeLimitError(
                    f"Response exceeds the byte limit of {self.limits.max_bytes}"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _content_type(response: httpx.Response) -> str | None:
        value = response.headers.get("content-type")
        return value.split(";", 1)[0].strip().lower() if value else None

    def _pdf_page_count(self, body: bytes) -> int:
        try:
            reader = PdfReader(BytesIO(body))
            if reader.is_encrypted:
                raise EncryptedPDFError("Encrypted PDF content is not accepted")
            page_count = len(reader.pages)
        except EncryptedPDFError:
            raise
        except (PdfReadError, OSError, ValueError) as exc:
            raise PDFValidationError("PDF content is malformed and cannot be inspected") from exc
        if page_count > self.limits.max_pdf_pages:
            raise PDFPageLimitError(f"PDF exceeds the page limit of {self.limits.max_pdf_pages}")
        return page_count
