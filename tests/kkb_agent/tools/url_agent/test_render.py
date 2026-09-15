import os

import pytest

from kkb_agent.tools.url_agent import (
    DocumentKind,
    PlaywrightRenderer,
    RendererUnavailableError,
    create_content_type_router,
    read_page,
)
from kkb_agent.tools.url_safety import UntrustedContent

# Mirrors what the live xtumy page actually returns: the metadata table is complete in
# the static response, while the values table has its header row and nothing beneath it.
STATIC_ONLY = b"""<!doctype html><html><body>
  <table><tr><td>Endeks Kodu</td><td>XTUMY</td></tr>
         <tr><td>ISIN Kodu</td><td>TRAIMKB01380</td></tr></table>
  <table><tr><td></td><td>Guncel Endeks Degerleri</td><td>Onceki Kapanis</td></tr></table>
</body></html>"""

RENDERED = b"""<!doctype html><html><body>
  <table><tr><td>Endeks Kodu</td><td>XTUMY</td></tr>
         <tr><td>ISIN Kodu</td><td>TRAIMKB01380</td></tr></table>
  <table>
    <tr><td></td><td>Guncel Endeks Degerleri</td><td>Onceki Kapanis</td></tr>
    <tr><td>BIST TUM-100</td><td>12.345,67</td><td>12.300,10</td></tr>
  </table>
</body></html>"""


class FakeFetcher:
    def __init__(self, body: bytes = STATIC_ONLY, content_type: str = "text/html"):
        self.body = body
        self.content_type = content_type
        self.calls = 0

    def fetch(self, url: str) -> UntrustedContent:
        self.calls += 1
        return UntrustedContent(
            requested_url=url,
            final_url=url,
            content_type=self.content_type,
            body=self.body,
            redirect_count=0,
        )


class FakeRenderer:
    def __init__(self, html: bytes = RENDERED):
        self.html = html
        self.urls: list[str] = []

    def render(self, url: str, *, timeout_ms: int | None = None) -> str:
        self.urls.append(url)
        self.timeout_ms = timeout_ms
        return self.html.decode()


def has_values(document) -> bool:
    """Sufficient once the current-values table has a data row beneath its header.

    Deliberately specific: the metadata table is fully populated statically, so any
    looser check ("the page mentions a number") would be satisfied by an ISIN code and
    the browser would never run.
    """
    for table in document.tables:
        heading = " ".join(table.header) + " " + " ".join(table.rows[0] if table.rows else ())
        if "Guncel Endeks" in heading or "Güncel Endeks" in heading:
            return len(table.rows) > 1
    return False


def test_static_is_used_when_it_already_carries_what_was_asked_for():
    fetcher = FakeFetcher(body=RENDERED)
    renderer = FakeRenderer()

    result = read_page(
        "https://x.example/endeks/xtumy",
        fetcher=fetcher,
        router=create_content_type_router(),
        renderer=renderer,
        is_sufficient=has_values,
    )

    assert result.decision.rendered is False
    assert "already carried" in result.decision.reason
    assert renderer.urls == []  # the browser was never launched


def test_the_browser_runs_only_when_the_static_response_falls_short():
    fetcher = FakeFetcher(body=STATIC_ONLY)
    renderer = FakeRenderer()

    result = read_page(
        "https://x.example/endeks/xtumy",
        fetcher=fetcher,
        router=create_content_type_router(),
        renderer=renderer,
        is_sufficient=has_values,
    )

    assert result.decision.rendered is True
    assert "rendered in a browser" in result.decision.reason
    assert "12.345,67" in result.document.text
    assert renderer.urls == ["https://x.example/endeks/xtumy"]


def test_the_renderer_is_pointed_at_the_url_the_safe_fetch_resolved():
    """The browser must never be handed unvalidated input: it gets the final URL that
    SafeURLFetcher already checked and followed."""

    class RedirectingFetcher(FakeFetcher):
        def fetch(self, url):
            self.calls += 1
            return UntrustedContent(
                requested_url=url,
                final_url="https://x.example/after-redirect",
                content_type="text/html",
                body=STATIC_ONLY,
                redirect_count=1,
            )

    renderer = FakeRenderer()
    read_page(
        "https://x.example/before",
        fetcher=RedirectingFetcher(),
        router=create_content_type_router(),
        renderer=renderer,
        is_sufficient=has_values,
    )

    assert renderer.urls == ["https://x.example/after-redirect"]


def test_without_a_sufficiency_check_nothing_is_rendered():
    renderer = FakeRenderer()
    result = read_page(
        "https://x.example/",
        fetcher=FakeFetcher(),
        router=create_content_type_router(),
        renderer=renderer,
        is_sufficient=None,
    )

    assert result.decision.rendered is False
    assert renderer.urls == []


def test_a_non_html_document_is_never_rendered():
    fetcher = FakeFetcher(body=b"%PDF-1.4\n", content_type="application/pdf")

    def pdf_handler(fetched):
        from kkb_agent.tools.url_agent import URLDocument, content_sha256

        return URLDocument(
            requested_url=fetched.requested_url,
            final_url=fetched.final_url,
            content_type=fetched.content_type,
            kind=DocumentKind.PDF,
            extraction_method="stub",
            byte_count=len(fetched.body),
            content_sha256=content_sha256(fetched.body),
        )

    renderer = FakeRenderer()
    result = read_page(
        "https://x.example/report.pdf",
        fetcher=fetcher,
        router=create_content_type_router(pdf_handler=pdf_handler),
        renderer=renderer,
        is_sufficient=lambda _document: False,
    )

    assert result.decision.rendered is False
    assert "cannot be improved by rendering" in result.decision.reason
    assert renderer.urls == []


def test_needing_a_browser_without_one_fails_honestly():
    with pytest.raises(RendererUnavailableError, match="no renderer was supplied"):
        read_page(
            "https://x.example/endeks/xtumy",
            fetcher=FakeFetcher(),
            router=create_content_type_router(),
            renderer=None,
            is_sufficient=has_values,
        )


@pytest.mark.network
@pytest.mark.skipif(
    not os.environ.get("KKB_NETWORK_TESTS"),
    reason="launches a real browser against a live page; set KKB_NETWORK_TESTS=1 to run",
)
def test_xtumy_index_values_appear_only_after_rendering():
    """The comparison the SCRUM-54 probe could not run, now that Chromium is installed."""

    from kkb_agent.tools.url_safety import SafeURLFetcher

    url = "https://www.borsaistanbul.com/endeks/xtumy"
    router = create_content_type_router()

    with SafeURLFetcher() as fetcher:
        static = router.route(fetcher.fetch(url))
        assert not has_values(static), "static response now has values; re-check SCRUM-63"

        result = read_page(
            url,
            fetcher=fetcher,
            router=router,
            renderer=PlaywrightRenderer(),
            is_sufficient=has_values,
        )

    assert result.decision.rendered is True
    assert has_values(result.document), "rendering did not populate the value table either"
