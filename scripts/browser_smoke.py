#!/usr/bin/env python3
"""Offline LIVE browser -> Next.js -> FastAPI -> production runner smoke.

Build web with NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8101 first.
Only search/fetch provider boundaries are replaced; no HTTP/SSE replay.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import duckdb
import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

from kkb_agent.api.main import create_app
from kkb_agent.api.turn1_runner import TurnOneAskRunner
from kkb_agent.catalog.hybrid import HybridRetrieval
from kkb_agent.catalog.vector_index import SemanticIndex, catalog_rows
from kkb_agent.config import Settings
from kkb_agent.regression import build_snapshot_database
from kkb_agent.tools.url_safety import UntrustedContent
from kkb_agent.tools.web_search import WebSearchItem, WebSearchResult, WebSearchUnavailableError

ROOT = Path(__file__).resolve().parents[1]


class LocalEmbeddings:
    model_id = "offline-browser"

    def embed_texts(self, texts):
        return [[1.0, 0.0] if "bddk_aylik" in text else [0.0, 1.0] for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class LocalSearch:
    def search(self, query, **kwargs):
        if "kesinti" in query:
            raise WebSearchUnavailableError("secret-provider-detail")
        return WebSearchResult(
            query,
            (
                WebSearchItem(
                    "TCMB test duyurusu", "https://example.org/news", "Sabit kaynak özeti"
                ),
            ),
            (),
        )


class LocalFetcher:
    def fetch(self, url):
        return UntrustedContent(
            url,
            url,
            "text/html",
            b"<html><body><h1>Offline report</h1><p>Verified local fixture.</p></body></html>",
            0,
        )


def wait_for(url, process=None):
    for _ in range(200):
        if process is not None and process.poll() is not None:
            raise RuntimeError("Frontend exited; inspect browser smoke server log")
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Server did not become ready: {url}")


def main():
    with tempfile.TemporaryDirectory(prefix="kkb-browser-") as directory:
        db = Path(directory) / "fixture.duckdb"
        build_snapshot_database(
            ROOT / "tests/fixtures/regression/published_three_turn_snapshot.json", db
        )
        settings = Settings(
            _env_file=None,
            duckdb_path=db,
            data_dir=Path(directory),
            mia_api_key="",
            evds_api_key="",
            cors_origins=["http://127.0.0.1:3101"],
        )
        runner = TurnOneAskRunner(db, web_search_tool=LocalSearch(), url_fetcher=LocalFetcher())
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(settings, ask_runner=runner),
                host="127.0.0.1",
                port=8101,
                log_level="error",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in ("MIA_API_KEY", "EVDS_API_KEY")
        }
        with (Path(directory) / "web.log").open("w") as log:
            web = subprocess.Popen(
                ["npm", "run", "start", "--", "--port", "3101"],
                cwd=ROOT / "web",
                env=environment,
                stdout=log,
                stderr=log,
            )
            try:
                wait_for("http://127.0.0.1:8101/health")
                wait_for("http://127.0.0.1:3101", web)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    page = browser.new_page()
                    page.route(
                        "**/*",
                        lambda route: (
                            route.continue_()
                            if route.request.url.startswith(("http://127.0.0.1:", "data:", "blob:"))
                            else route.abort()
                        ),
                    )
                    requests = []
                    responses = []
                    page.on(
                        "request",
                        lambda req: (
                            requests.append(req.post_data_json)
                            if req.url.endswith("/ask")
                            else None
                        ),
                    )
                    page.on(
                        "response",
                        lambda res: responses.append(res) if res.url.endswith("/ask") else None,
                    )
                    page.goto("http://127.0.0.1:3101")

                    def ask(question, outcome="succeeded"):
                        page.get_by_label("Sorunuz").fill(question)
                        page.get_by_role("button", name="Gönder", exact=True).click()
                        expect(page.locator("article").last).to_have_attribute(
                            "data-outcome", outcome, timeout=60000
                        )
                        expect(page.get_by_label("Sorunuz")).to_be_enabled()
                        return page.locator("article").last

                    first = ask(
                        "Konut kredisi bakiyeleri ile konut kredisi faizlerini "
                        "2021-2025 arasında göster."
                    )
                    expect(first).to_have_attribute("data-version", "2")
                    expect(first.locator("table")).to_be_visible()
                    expect(first.locator("figure").first).to_be_visible()
                    first_frame = next(
                        json.loads(line[6:])["payload"]["frame"]
                        for line in responses[-1].text().splitlines()
                        if line.startswith("data: ") and json.loads(line[6:])["type"] == "result"
                    )
                    ask("Konut kredisi tutarını TÜFE ile reel hale getir.")
                    expect(page.locator("article").last).to_have_attribute("data-version", "4")
                    assert requests[0]["analysis_id"] == requests[1]["analysis_id"]
                    assert requests[1]["version"] == 2
                    second_frame = next(
                        json.loads(line[6:])["payload"]["frame"]
                        for line in responses[-1].text().splitlines()
                        if line.startswith("data: ") and json.loads(line[6:])["type"] == "result"
                    )
                    assert first_frame["columns"] == second_frame["columns"][:2]
                    assert first_frame["spine"] == second_frame["spine"]
                    ask("Konut fiyat endeksini de ekleyip önceki bulguyu yeniden değerlendir.")
                    expect(page.locator("article").last).to_have_attribute("data-version", "5")
                    info = ask("TCMB'nin güncel duyurularını web'de ara.")
                    expect(info.get_by_role("link", name="TCMB test duyurusu")).to_have_attribute(
                        "href", "https://example.org/news"
                    )
                    assert "TOOL_RUN_NOT_RENDERABLE" not in responses[-1].text()
                    assert '"outcome":"succeeded"' in responses[-1].text()
                    failed = ask("kesinti web'de ara", "failed")
                    expect(failed.get_by_role("alert")).to_be_visible()
                    assert "secret-provider-detail" not in page.content()
                    expect(page.locator('article[data-version="5"] table')).to_be_visible()
                    url = ask("https://example.org/report oku")
                    expect(url.get_by_role("link", name="Okunan kaynak")).to_be_visible()
                    generic = ask("2022 ile 2024 arasında konut kredilerindeki değişimi göster.")
                    expect(generic.locator("table")).to_be_visible()
                    assert "2022-01" in generic.inner_text() and "2024-12" in generic.inner_text()
                    causal = ask(
                        "Konut kredisi ile konut fiyat endeksi arasında nedensellik var mı?"
                    )
                    assert any(
                        status in causal.inner_text()
                        for status in (
                            "not_identifiable",
                            "limited_evidence",
                            "predictive_relationship_supported",
                        )
                    )
                    # A genuine same-concept alternative in this temporary catalog.
                    # The frozen published snapshot is never modified.
                    with duckdb.connect(str(db)) as connection:
                        connection.execute(
                            "INSERT INTO series_catalog SELECT * REPLACE "
                            "('evds.fixture.cpi_variant' AS series_id) FROM series_catalog "
                            "WHERE series_id = 'evds.TP.GENENDEKS.T1'"
                        )
                    # Offline real LanceDB, injected only for these selective scenarios.
                    index = SemanticIndex(Path(directory) / "lance", LocalEmbeddings())
                    index.build(catalog_rows(db))
                    runner._retrieval = HybridRetrieval(index, mode="selective")
                    semantic = ask("Ev satın almak için kalan kredi borcu bakiyesi göster")
                    expect(semantic.locator("table")).to_be_visible()
                    semantic = page.locator("article").nth(page.locator("article").count() - 1)
                    semantic_version = int(semantic.get_attribute("data-version"))
                    ambiguity = ask("Tüketici fiyat endeksi göster", "failed")
                    expect(ambiguity.get_by_role("alert")).to_contain_text(
                        "Birden fazla uyumlu seri"
                    )
                    expect(semantic.locator("table")).to_be_visible()
                    assert requests[-1]["version"] == semantic_version
                    browser.close()
                print(
                    "PASS browser: published 2 -> 4 -> 5, live SSE, charts/table, "
                    "web/URL success, safe failure, state preservation, generic dates, "
                    "selective semantic result and ambiguity"
                )
            finally:
                web.terminate()
                web.wait(timeout=15)
                server.should_exit = True
                thread.join(timeout=15)


if __name__ == "__main__":
    main()
