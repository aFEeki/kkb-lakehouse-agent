from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import httpx
import pytest

from kkb_agent.tools.url_safety import SafeURLFetcher
from kkb_agent.tools.web_search import (
    MAX_RESULT_LIMIT,
    SearxNGSearchProvider,
    WebSearchRequest,
    WebSearchResponseError,
    WebSearchResult,
    WebSearchTimeoutError,
    WebSearchTool,
    WebSearchUnavailableError,
    WebSearchValidationError,
)

PUBLIC_IP = "93.184.216.34"


def provider(handler):
    def resolve(host, _port):
        return (host,) if host == "127.0.0.1" else (PUBLIC_IP,)

    return SearxNGSearchProvider(
        "http://searxng:8080",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        url_fetcher=SafeURLFetcher(resolver=resolve),
    )


def json_response(results):
    return httpx.Response(200, json={"results": results})


def test_searxng_normalizes_fields_and_preserves_order():
    def handler(request):
        assert request.url.params["q"] == "konut kredisi"
        assert request.url.params["format"] == "json"
        assert request.url.params["language"] == "tr"
        return json_response(
            [
                {"title": "  Birinci  sonuç ", "url": "https://a.example/one", "content": "A  B"},
                {"title": "İkinci", "url": "https://b.example/two", "content": "C"},
            ]
        )

    with provider(handler) as search:
        result = search.search(WebSearchRequest(" konut kredisi ", language="tr"))

    assert [(item.title, item.url, item.snippet) for item in result.items] == [
        ("Birinci sonuç", "https://a.example/one", "A B"),
        ("İkinci", "https://b.example/two", "C"),
    ]
    assert asdict(result)["evidence"][0] == {
        "title": "Birinci sonuç",
        "url": "https://a.example/one",
    }


def test_limit_is_enforced_after_unsafe_and_duplicate_results_are_filtered():
    results = [
        {"title": "unsafe", "url": "http://127.0.0.1/private", "content": "x"},
        {"title": "one", "url": "https://a.example/result#first", "content": "x"},
        {"title": "duplicate", "url": "https://a.example/result#second", "content": "y"},
        {"title": "two", "url": "https://b.example/result", "content": "z"},
        {"title": "three", "url": "https://c.example/result", "content": "q"},
    ]
    with provider(lambda _request: json_response(results)) as search:
        result = search.search(WebSearchRequest("query", limit=2))

    assert [item.title for item in result.items] == ["one", "two"]
    assert [item.url for item in result.items] == [
        "https://a.example/result",
        "https://b.example/result",
    ]


def test_missing_optional_text_is_not_fabricated_and_missing_url_is_skipped():
    with provider(
        lambda _request: json_response(
            [{"url": "https://a.example/"}, {"title": "no url", "content": "ignored"}]
        )
    ) as search:
        result = search.search(WebSearchRequest("query"))

    assert len(result.items) == 1
    assert result.items[0].title is None
    assert result.items[0].snippet is None


def test_empty_results_are_a_successful_empty_result():
    with provider(lambda _request: json_response([])) as search:
        result = search.search(WebSearchRequest("query"))
    assert result.items == ()
    assert result.evidence == ()


@pytest.mark.parametrize("payload", [[], {}, {"results": {}}, {"results": ["bad"]}])
def test_malformed_provider_payload_has_a_typed_error(payload):
    with provider(lambda _request: httpx.Response(200, json=payload)) as search:
        with pytest.raises(WebSearchResponseError):
            search.search(WebSearchRequest("query"))


def test_malformed_json_has_a_typed_error():
    with provider(lambda _request: httpx.Response(200, content=b"not-json")) as search:
        with pytest.raises(WebSearchResponseError):
            search.search(WebSearchRequest("query"))


def test_timeout_has_a_typed_error():
    def handler(request):
        raise httpx.ReadTimeout("private transport details", request=request)

    with provider(handler) as search:
        with pytest.raises(WebSearchTimeoutError, match="timed out"):
            search.search(WebSearchRequest("query"))


def test_connection_failure_has_a_typed_error():
    def handler(request):
        raise httpx.ConnectError("private transport details", request=request)

    with provider(handler) as search:
        with pytest.raises(WebSearchUnavailableError, match="unavailable"):
            search.search(WebSearchRequest("query"))


@pytest.mark.parametrize(
    "request_data",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": MAX_RESULT_LIMIT + 1},
        {"query": "x", "limit": True},
        {"query": "x", "language": ""},
    ],
)
def test_invalid_requests_are_rejected(request_data):
    with pytest.raises(WebSearchValidationError):
        WebSearchRequest(**request_data)


def test_tool_depends_on_the_provider_protocol_and_returns_immutable_result():
    class FakeProvider:
        def __init__(self):
            self.request = None

        def search(self, request):
            self.request = request
            return WebSearchResult(request.query, (), ())

    fake = FakeProvider()
    result = WebSearchTool(fake).search("kanıt ara", limit=3, language="tr")

    assert result.query == "kanıt ara"
    assert fake.request == WebSearchRequest("kanıt ara", 3, "tr")
    with pytest.raises(FrozenInstanceError):
        result.query = "changed"


def test_search_layer_has_no_model_dependency():
    source = (Path(__file__).resolve().parents[3] / "src/kkb_agent/tools/web_search.py").read_text()
    assert "MIA" not in source
    assert "openai" not in source.casefold()
    assert "chat.completions" not in source
