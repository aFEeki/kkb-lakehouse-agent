"""Read a page through a browser, but only when a static fetch is not enough."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from kkb_agent.frame._base import Contract
from kkb_agent.tools.url_agent.models import DocumentKind, URLAgentError, URLDocument
from kkb_agent.tools.url_agent.router import ContentTypeRouter
from kkb_agent.tools.url_safety import UntrustedContent

DEFAULT_RENDER_TIMEOUT_MS = 15_000


class RenderError(URLAgentError):
    """The page could not be rendered in a browser."""


class RendererUnavailableError(RenderError):
    """Rendering was required but no browser is installed in this environment."""


class Renderer(Protocol):
    """Returns the DOM of a page after its scripts have run."""

    def render(self, url: str) -> str: ...


class RenderDecision(Contract):
    """Whether a browser was used, and the reason, for the execution trace."""

    rendered: bool
    reason: str


class PageResult(Contract):
    """The document that was read, and how it was obtained."""

    document: URLDocument
    decision: RenderDecision


@dataclass
class PlaywrightRenderer:
    """Render with headless Chromium, waiting for content rather than for a clock.

    `wait_for_selector` is the honest signal that the page has finished populating. When
    no selector is given, the renderer waits for the network to go idle. There is no
    `sleep` anywhere: a fixed delay is either too short and flaky or too long and a failed
    demo.
    """

    timeout_ms: int = DEFAULT_RENDER_TIMEOUT_MS
    wait_for_selector: str | None = None

    def render(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RendererUnavailableError(
                "Playwright is not installed; install the 'browser' extra and run "
                "`python -m playwright install chromium`"
            ) from exc

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                    if self.wait_for_selector:
                        page.wait_for_selector(self.wait_for_selector, timeout=self.timeout_ms)
                    else:
                        page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
                    return page.content()
                finally:
                    browser.close()
        except RenderError:
            raise
        except Exception as exc:
            raise RenderError(f"Rendering {url!r} failed: {exc}") from exc


def read_page(
    url: str,
    *,
    fetcher,
    router: ContentTypeRouter,
    renderer: Renderer | None = None,
    is_sufficient: Callable[[URLDocument], bool] | None = None,
) -> PageResult:
    """Read one page, rendering it only when the static response falls short.

    The static fetch always runs first: it is faster, it costs no browser, and for most
    pages it is complete. It also performs the SCRUM-61 safety checks, and the browser is
    then pointed at the URL that fetch already resolved and validated, rather than at
    unvalidated input.

    `is_sufficient` decides whether the static document already answers the question. It
    is the caller's predicate because only the caller knows what it came for -- the tool
    must not guess that a page is "probably incomplete".
    """

    static = router.route(fetcher.fetch(url))

    if is_sufficient is None:
        return PageResult(
            document=static,
            decision=RenderDecision(rendered=False, reason="no sufficiency check was requested"),
        )
    if is_sufficient(static):
        return PageResult(
            document=static,
            decision=RenderDecision(
                rendered=False, reason="the static response already carried what was requested"
            ),
        )
    if static.kind is not DocumentKind.HTML:
        return PageResult(
            document=static,
            decision=RenderDecision(
                rendered=False,
                reason=f"a {static.kind.value} document cannot be improved by rendering",
            ),
        )
    if renderer is None:
        raise RendererUnavailableError(
            f"{static.final_url!r} did not carry the requested content statically and no "
            "renderer was supplied"
        )

    html = renderer.render(static.final_url)
    rendered = router.route(
        UntrustedContent(
            requested_url=static.requested_url,
            final_url=static.final_url,
            content_type="text/html",
            body=html.encode("utf-8"),
            redirect_count=0,
        )
    )
    return PageResult(
        document=rendered,
        decision=RenderDecision(
            rendered=True,
            reason="the static response was missing the requested content, so the page was "
            "rendered in a browser",
        ),
    )
