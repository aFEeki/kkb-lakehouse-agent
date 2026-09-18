"""Provider-neutral web search with a self-hosted SearxNG adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from kkb_agent.tools.url_safety import SafeURLFetcher, URLSafetyError

DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 10
DEFAULT_TIMEOUT_SECONDS = 10.0


class WebSearchError(RuntimeError):
    """Base web-search domain error."""


class WebSearchValidationError(WebSearchError, ValueError):
    """The provider-neutral request or provider configuration is invalid."""


class WebSearchUnavailableError(WebSearchError):
    """The configured provider could not be reached."""


class WebSearchTimeoutError(WebSearchUnavailableError):
    """The configured provider exceeded its bounded timeout."""


class WebSearchResponseError(WebSearchError):
    """The provider returned an unusable response."""


@dataclass(frozen=True)
class WebSearchRequest:
    query: str
    limit: int = DEFAULT_RESULT_LIMIT
    language: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise WebSearchValidationError("Search query must not be blank")
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_RESULT_LIMIT:
            raise WebSearchValidationError(
                f"Search result limit must be between 1 and {MAX_RESULT_LIMIT}"
            )
        if self.language is not None and (
            not isinstance(self.language, str) or not self.language.strip()
        ):
            raise WebSearchValidationError("Search language must be a non-blank string or None")


@dataclass(frozen=True)
class WebSearchItem:
    """One untrusted hit; provider omissions remain explicit as None."""

    title: str | None
    url: str
    snippet: str | None


@dataclass(frozen=True)
class WebSearchEvidence:
    """Citation-ready evidence pointing to one concrete result."""

    title: str | None
    url: str


@dataclass(frozen=True)
class WebSearchResult:
    query: str
    items: tuple[WebSearchItem, ...]
    evidence: tuple[WebSearchEvidence, ...]


class WebSearchProvider(Protocol):
    def search(self, request: WebSearchRequest) -> WebSearchResult: ...


class WebSearchTool:
    """Application-facing tool that depends only on the provider protocol."""

    def __init__(self, provider: WebSearchProvider) -> None:
        self._provider = provider

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_RESULT_LIMIT,
        language: str | None = None,
    ) -> WebSearchResult:
        return self._provider.search(WebSearchRequest(query=query, limit=limit, language=language))


class SearxNGSearchProvider:
    """Normalize SearxNG JSON without exposing provider payloads to callers."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
        url_fetcher: SafeURLFetcher | None = None,
    ) -> None:
        try:
            parsed = httpx.URL(base_url)
        except (httpx.InvalidURL, ValueError) as exc:
            raise WebSearchValidationError("SearxNG base URL is invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.host or parsed.userinfo:
            raise WebSearchValidationError("SearxNG base URL must be HTTP(S) without credentials")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise WebSearchValidationError("Search timeout must be positive")

        self._base_url = str(parsed).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=float(timeout))
        self._owns_fetcher = url_fetcher is None
        self._url_fetcher = url_fetcher or SafeURLFetcher()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
        if self._owns_fetcher:
            self._url_fetcher.close()

    def __enter__(self) -> SearxNGSearchProvider:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def search(self, request: WebSearchRequest) -> WebSearchResult:
        params = {"q": request.query.strip(), "format": "json", "safesearch": "1"}
        if request.language is not None:
            params["language"] = request.language.strip()
        try:
            response = self._client.get(f"{self._base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise WebSearchTimeoutError("Search provider timed out") from exc
        except httpx.HTTPError as exc:
            raise WebSearchUnavailableError("Search provider is unavailable") from exc
        except (TypeError, ValueError) as exc:
            raise WebSearchResponseError("Search provider returned malformed JSON") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise WebSearchResponseError("Search provider returned an invalid result payload")

        items: list[WebSearchItem] = []
        seen_urls: set[str] = set()
        for raw in payload["results"]:
            if not isinstance(raw, dict):
                raise WebSearchResponseError("Search provider returned an invalid result item")
            raw_url = raw.get("url")
            if not isinstance(raw_url, str):
                continue
            try:
                normalized_url = self._url_fetcher.validate_public_url(raw_url)
            except URLSafetyError:
                continue
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            items.append(
                WebSearchItem(
                    title=_optional_text(raw.get("title")),
                    url=normalized_url,
                    snippet=_optional_text(raw.get("content")),
                )
            )
            if len(items) == request.limit:
                break

        normalized = tuple(items)
        return WebSearchResult(
            query=request.query.strip(),
            items=normalized,
            evidence=tuple(WebSearchEvidence(item.title, item.url) for item in normalized),
        )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None
