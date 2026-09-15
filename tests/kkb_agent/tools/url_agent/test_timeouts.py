import pytest

from kkb_agent.tools.url_agent import (
    Deadline,
    DocumentKind,
    ToolTimeoutError,
    URLDocument,
    content_sha256,
    create_content_type_router,
    discover_document,
    extract_pdf,
    ocr_images,
    read_page,
)
from kkb_agent.tools.url_safety import UntrustedContent


class FakeClock:
    """A clock that only moves when a test says so, so no test ever sleeps."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def html(body: bytes, url: str = "https://x.example/") -> UntrustedContent:
    return UntrustedContent(
        requested_url=url, final_url=url, content_type="text/html", body=body, redirect_count=0
    )


class TestDeadline:
    def test_remaining_falls_as_the_clock_advances(self):
        clock = FakeClock()
        deadline = Deadline(10.0, clock=clock)

        assert deadline.remaining == 10.0
        clock.advance(4.0)
        assert deadline.remaining == 6.0
        assert deadline.expired is False
        clock.advance(6.0)
        assert deadline.remaining == 0.0
        assert deadline.expired is True

    def test_require_names_the_operation_and_the_budget_it_outlived(self):
        clock = FakeClock()
        deadline = Deadline(5.0, clock=clock)
        clock.advance(6.0)

        with pytest.raises(ToolTimeoutError, match="reading the gold PDF") as raised:
            deadline.require("reading the gold PDF")
        assert "5s budget" in str(raised.value)
        assert "6.0s" in str(raised.value)

    def test_require_returns_what_is_left_while_time_remains(self):
        clock = FakeClock()
        deadline = Deadline(10.0, clock=clock)
        clock.advance(3.0)

        assert deadline.require("anything") == 7.0

    def test_bounded_never_exceeds_what_the_turn_has_left(self):
        clock = FakeClock()
        deadline = Deadline(10.0, clock=clock)

        assert deadline.bounded(30.0) == 10.0
        clock.advance(8.0)
        assert deadline.bounded(30.0) == 2.0
        assert deadline.bounded(1.0) == 1.0

    def test_partial_work_travels_with_the_error(self):
        clock = FakeClock()
        deadline = Deadline(1.0, clock=clock)
        clock.advance(2.0)

        with pytest.raises(ToolTimeoutError) as raised:
            deadline.require("more OCR", partial=("page one", ""))
        assert raised.value.partial == ("page one", "")

    @pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "10"])
    def test_an_unusable_budget_is_rejected(self, value):
        with pytest.raises(ValueError, match="positive finite"):
            Deadline(value)


class TestOCRUnderABudget:
    class CountingOCR:
        def __init__(self, clock=None, cost=0.0):
            self.clock = clock
            self.cost = cost
            self.images_seen = 0

        def read_page_images(self, images):
            self.images_seen += len(images)
            if self.clock is not None:
                self.clock.advance(self.cost)
            return tuple(f"page {self.images_seen - len(images) + i}" for i in range(len(images)))

    def test_a_spent_budget_stops_ocr_and_keeps_what_was_read(self):
        clock = FakeClock()
        deadline = Deadline(10.0, clock=clock)
        # The first call of three images overruns the whole budget, so the second batch
        # is never started. A call's cost cannot be known in advance, so the rule is
        # simply that a step never begins once nothing is left.
        ocr = self.CountingOCR(clock=clock, cost=11.0)

        with pytest.raises(ToolTimeoutError) as raised:
            ocr_images([b"a", b"b", b"c", b"d", b"e", b"f"], ocr, deadline=deadline)

        assert ocr.images_seen == 3  # the fourth image was never sent
        partial = raised.value.partial
        assert len([text for text in partial if text]) == 3
        assert partial[3:] == ("", "", "")

    def test_a_sufficient_budget_reads_everything(self):
        clock = FakeClock()
        ocr = self.CountingOCR(clock=clock, cost=1.0)

        texts = ocr_images([b"a", b"b", b"c", b"d"], ocr, deadline=Deadline(60.0, clock=clock))

        assert ocr.images_seen == 4
        assert all(texts)


class TestDiscoveryUnderABudget:
    PAGE_ONE = b'<!doctype html><html><body><a href="https://x.example/b">rapor</a></body></html>'
    PAGE_TWO = (
        b'<!doctype html><html><body><a href="https://x.example/c.pdf">rapor</a></body></html>'
    )

    class SlowFetcher:
        def __init__(self, pages, clock, cost):
            self.pages = pages
            self.clock = clock
            self.cost = cost
            self.requested = []

        def fetch(self, url):
            self.requested.append(url)
            self.clock.advance(self.cost)
            body, content_type = self.pages[url]
            return UntrustedContent(
                requested_url=url,
                final_url=url,
                content_type=content_type,
                body=body,
                redirect_count=0,
            )

    def pdf_handler(self, fetched):
        return URLDocument(
            requested_url=fetched.requested_url,
            final_url=fetched.final_url,
            content_type=fetched.content_type,
            kind=DocumentKind.PDF,
            extraction_method="stub",
            byte_count=len(fetched.body),
            content_sha256=content_sha256(fetched.body),
        )

    def pages(self):
        return {
            "https://x.example/a": (self.PAGE_ONE, "text/html"),
            "https://x.example/b": (self.PAGE_TWO, "text/html"),
            "https://x.example/c.pdf": (b"%PDF-1.4 x", "application/pdf"),
        }

    def test_the_budget_bounds_the_whole_walk_not_each_hop(self):
        clock = FakeClock()
        # Each fetch takes 11s and would pass any per-request timeout of its own. Two of
        # them exhaust a 20s turn, so the third hop never starts.
        fetcher = self.SlowFetcher(self.pages(), clock, cost=11.0)

        with pytest.raises(ToolTimeoutError, match="fetching") as raised:
            discover_document(
                "https://x.example/a",
                query="rapor",
                fetcher=fetcher,
                router=create_content_type_router(pdf_handler=self.pdf_handler),
                deadline=Deadline(20.0, clock=clock),
            )

        assert fetcher.requested == ["https://x.example/a", "https://x.example/b"]
        assert len(raised.value.partial) == 2  # the hops already walked are reported

    def test_a_walk_that_fits_still_completes(self):
        clock = FakeClock()
        fetcher = self.SlowFetcher(self.pages(), clock, cost=1.0)

        result = discover_document(
            "https://x.example/a",
            query="rapor",
            fetcher=fetcher,
            router=create_content_type_router(pdf_handler=self.pdf_handler),
            deadline=Deadline(60.0, clock=clock),
        )

        assert result.document.kind is DocumentKind.PDF


class TestRenderingUnderABudget:
    STATIC = (
        b"<!doctype html><html><body><table><tr><td>only header</td></tr></table></body></html>"
    )

    class RecordingRenderer:
        def __init__(self):
            self.timeouts = []

        def render(self, url, *, timeout_ms=None):
            self.timeouts.append(timeout_ms)
            return "<html><body><table><tr><td>a</td></tr><tr><td>b</td></tr></table></body></html>"

    class Fetcher:
        def __init__(self, clock, cost):
            self.clock = clock
            self.cost = cost

        def fetch(self, url):
            self.clock.advance(self.cost)
            return html(TestRenderingUnderABudget.STATIC, url)

    def test_the_renderer_is_given_only_the_time_the_turn_has_left(self):
        clock = FakeClock()
        renderer = self.RecordingRenderer()

        read_page(
            "https://x.example/",
            fetcher=self.Fetcher(clock, cost=4.0),
            router=create_content_type_router(),
            renderer=renderer,
            is_sufficient=lambda document: len(document.tables[0].rows) > 1,
            deadline=Deadline(10.0, clock=clock),
        )

        assert renderer.timeouts == [6000]  # 10s budget minus the 4s the fetch took

    def test_a_spent_budget_refuses_to_launch_the_browser(self):
        clock = FakeClock()
        renderer = self.RecordingRenderer()

        with pytest.raises(ToolTimeoutError, match="rendering"):
            read_page(
                "https://x.example/",
                fetcher=self.Fetcher(clock, cost=11.0),
                router=create_content_type_router(),
                renderer=renderer,
                is_sufficient=lambda document: len(document.tables[0].rows) > 1,
                deadline=Deadline(10.0, clock=clock),
            )

        assert renderer.timeouts == []


def test_a_scanned_pdf_that_times_out_returns_the_pages_it_managed_to_read():
    """Partial results beat a bare failure, as long as the document says it is partial."""
    from test_pdf import _image_only_pdf  # noqa: PLC0415 - shared local fixture builder

    clock = FakeClock()

    class ExpiringOCR:
        def read_page_images(self, images):
            clock.advance(30.0)  # the first call consumes the whole budget
            return tuple(f"read {index}" for index in range(len(images)))

    document = extract_pdf(
        UntrustedContent(
            requested_url="https://x.example/scan.pdf",
            final_url="https://x.example/scan.pdf",
            content_type="application/pdf",
            body=_image_only_pdf(),
            redirect_count=0,
        ),
        ocr=ExpiringOCR(),
        deadline=Deadline(20.0, clock=clock),
    )

    assert document.pages[0].text == "read 0"
    assert document.text == "read 0"
