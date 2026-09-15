import pytest

from kkb_agent.tools.url_agent import (
    DocumentKind,
    DocumentNotFoundError,
    ExtractedLink,
    HopLimitError,
    create_content_type_router,
    discover_document,
    score_link,
    terms,
)
from kkb_agent.tools.url_safety import UntrustedContent

# Modelled on what the SCRUM-54 probe actually observed: the metals landing page lists one
# link per metal, the gold one pointing at a plain .pdf path with no date parameter.
METALS_PAGE = b"""<!doctype html><html><body>
  <h1>Kiymetli Madenler Piyasasi Verileri</h1>
  <ul>
    <li><a href="/dosyalar/kmtp/veriler/kmp_au.pdf">Alt&#305;n &#304;&#351;lemleri</a></li>
    <li><a href="/dosyalar/kmtp/veriler/kmp_ag.pdf">G&#252;m&#252;&#351; Islemleri</a></li>
    <li><a href="/dosyalar/kmtp/veriler/kmp_pl.pdf">Platin &#304;&#351;lemleri</a></li>
    <li><a href="/hakkimizda">Hakk&#305;m&#305;zda</a></li>
  </ul>
</body></html>"""

GOLD_PDF = b"%PDF-1.4 gold"


class FakeFetcher:
    """Serves canned responses by URL and records the order they were requested in."""

    def __init__(self, pages: dict[str, tuple[bytes, str]]):
        self.pages = pages
        self.requested: list[str] = []

    def fetch(self, url: str) -> UntrustedContent:
        self.requested.append(url)
        if url not in self.pages:
            raise AssertionError(f"unexpected fetch: {url}")
        body, content_type = self.pages[url]
        return UntrustedContent(
            requested_url=url,
            final_url=url,
            content_type=content_type,
            body=body,
            redirect_count=0,
        )


def fake_pdf_handler(fetched):
    from kkb_agent.tools.url_agent import URLDocument, content_sha256

    return URLDocument(
        requested_url=fetched.requested_url,
        final_url=fetched.final_url,
        content_type=fetched.content_type,
        kind=DocumentKind.PDF,
        extraction_method="stub",
        text="Altin islemleri agirlikli ortalama 4.512,30",
        byte_count=len(fetched.body),
        content_sha256=content_sha256(fetched.body),
    )


def metals_fetcher() -> FakeFetcher:
    return FakeFetcher(
        {
            "https://borsa.example/veriler/piyasa-verileri": (METALS_PAGE, "text/html"),
            "https://borsa.example/dosyalar/kmtp/veriler/kmp_au.pdf": (
                GOLD_PDF,
                "application/pdf",
            ),
        }
    )


def test_finds_the_gold_pdf_among_sibling_metal_links():
    fetcher = metals_fetcher()
    result = discover_document(
        "https://borsa.example/veriler/piyasa-verileri",
        query="altın işlemleri",
        fetcher=fetcher,
        router=create_content_type_router(pdf_handler=fake_pdf_handler),
    )

    assert result.document.kind is DocumentKind.PDF
    assert result.document.final_url.endswith("kmp_au.pdf")
    assert "4.512,30" in result.document.text
    assert fetcher.requested == [
        "https://borsa.example/veriler/piyasa-verileri",
        "https://borsa.example/dosyalar/kmtp/veriler/kmp_au.pdf",
    ]


def test_the_trace_records_which_link_was_taken_and_why():
    result = discover_document(
        "https://borsa.example/veriler/piyasa-verileri",
        query="altın işlemleri",
        fetcher=metals_fetcher(),
        router=create_content_type_router(pdf_handler=fake_pdf_handler),
    )

    landing, target = result.hops
    assert landing.kind is DocumentKind.HTML
    assert landing.followed_url.endswith("kmp_au.pdf")
    assert landing.followed_text == "Altın İşlemleri"
    assert "link text matched" in landing.reason
    assert "altın" in landing.reason and "işlemleri" in landing.reason
    assert landing.score > 0
    assert target.kind is DocumentKind.PDF
    assert target.followed_url is None


def test_a_different_metal_wins_when_that_is_what_was_asked_for():
    result = discover_document(
        "https://borsa.example/veriler/piyasa-verileri",
        query="gümüş",
        fetcher=FakeFetcher(
            {
                "https://borsa.example/veriler/piyasa-verileri": (METALS_PAGE, "text/html"),
                "https://borsa.example/dosyalar/kmtp/veriler/kmp_ag.pdf": (
                    b"%PDF-1.4 silver",
                    "application/pdf",
                ),
            }
        ),
        router=create_content_type_router(pdf_handler=fake_pdf_handler),
    )

    assert result.document.final_url.endswith("kmp_ag.pdf")


def test_a_url_that_is_already_the_document_needs_no_hop():
    fetcher = FakeFetcher(
        {"https://borsa.example/kmp_au.pdf": (GOLD_PDF, "application/pdf")},
    )
    result = discover_document(
        "https://borsa.example/kmp_au.pdf",
        query="altın",
        fetcher=fetcher,
        router=create_content_type_router(pdf_handler=fake_pdf_handler),
    )

    assert len(result.hops) == 1
    assert result.hops[0].followed_url is None
    assert fetcher.requested == ["https://borsa.example/kmp_au.pdf"]


def test_no_matching_link_refuses_and_says_how_many_it_considered():
    with pytest.raises(DocumentNotFoundError, match="4 link"):
        discover_document(
            "https://borsa.example/veriler/piyasa-verileri",
            query="bakır",
            fetcher=metals_fetcher(),
            router=create_content_type_router(pdf_handler=fake_pdf_handler),
        )


def test_discovery_stops_at_the_hop_limit():
    loop = (
        b'<!doctype html><html><body><a href="https://borsa.example/b">altin rapor</a>'
        b"</body></html>"
    )
    second = (
        b'<!doctype html><html><body><a href="https://borsa.example/c">altin rapor</a>'
        b"</body></html>"
    )
    third = b'<!doctype html><html><body><a href="https://borsa.example/d">altin</a></body></html>'
    fetcher = FakeFetcher(
        {
            "https://borsa.example/a": (loop, "text/html"),
            "https://borsa.example/b": (second, "text/html"),
            "https://borsa.example/c": (third, "text/html"),
        }
    )

    with pytest.raises(HopLimitError, match="within 2 hop"):
        discover_document(
            "https://borsa.example/a",
            query="altin rapor",
            fetcher=fetcher,
            router=create_content_type_router(pdf_handler=fake_pdf_handler),
            max_hops=2,
        )
    assert len(fetcher.requested) == 3  # start plus two hops, then it stops


def test_turkish_casefolding_is_applied_to_terms():
    # Turkish folds "I" to dotless "ı" and "İ" to "i", which str.lower() gets wrong.
    assert terms("Altın İşlemleri") == ("altın", "işlemleri")
    assert terms("ALTIN ISLEMLERI") == ("altın", "ıslemlerı")


def test_a_page_written_without_turkish_diacritics_still_matches():
    # Publishers routinely write "Altin Islemleri", and URL slugs are always ASCII.
    query = terms("altın işlemleri")
    with_diacritics, _ = score_link(
        ExtractedLink(text="Altın İşlemleri", url="https://x.example/a.pdf"), query
    )
    without_diacritics, reason = score_link(
        ExtractedLink(text="Altin Islemleri", url="https://x.example/a.pdf"), query
    )

    assert without_diacritics == with_diacritics
    assert "link text matched" in reason


def test_scoring_prefers_link_text_over_url_and_rewards_document_suffixes():
    query = terms("altın")
    in_text, text_reason = score_link(
        ExtractedLink(text="Altın İşlemleri", url="https://x.example/a"), query
    )
    in_url, url_reason = score_link(
        ExtractedLink(text="Belge", url="https://x.example/altin"), query
    )
    suffixed, _ = score_link(ExtractedLink(text="Belge", url="https://x.example/altin.pdf"), query)
    unrelated, unrelated_reason = score_link(
        ExtractedLink(text="Hakkımızda", url="https://x.example/about"), query
    )

    assert in_text > in_url
    assert suffixed > in_url
    assert unrelated == 0
    assert "link text matched" in text_reason
    assert "URL matched" in url_reason
    assert "nothing in the request matched" in unrelated_reason


def test_equal_scores_break_towards_the_first_link_on_the_page():
    page = (
        b'<!doctype html><html><body><a href="https://x.example/one.pdf">rapor</a>'
        b'<a href="https://x.example/two.pdf">rapor</a></body></html>'
    )
    fetcher = FakeFetcher(
        {
            "https://x.example/": (page, "text/html"),
            "https://x.example/one.pdf": (GOLD_PDF, "application/pdf"),
        }
    )

    result = discover_document(
        "https://x.example/",
        query="rapor",
        fetcher=fetcher,
        router=create_content_type_router(pdf_handler=fake_pdf_handler),
    )

    assert result.document.final_url.endswith("one.pdf")


def test_max_hops_must_be_a_non_negative_integer():
    with pytest.raises(ValueError, match="non-negative integer"):
        discover_document(
            "https://x.example/",
            query="rapor",
            fetcher=metals_fetcher(),
            router=create_content_type_router(),
            max_hops=-1,
        )
