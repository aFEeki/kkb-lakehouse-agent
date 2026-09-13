"""Low-volume evidence probe for the two Borsa Istanbul demo URLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup

from kkb_agent.tools import SafeURLFetcher, URLSafetyLimits

PRECIOUS_METALS_URL = (
    "https://www.borsaistanbul.com/veriler/"
    "kiymetli-madenler-ve-kiymetli-taslar-piyasasi/piyasa-verileri"
)
XTUMY_URL = "https://www.borsaistanbul.com/endeks/xtumy"
_TEXT_MARKERS = ("xtumy", "bist 100", "bist tüm-100", "endeks")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_html(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _normalized_text(html: bytes) -> str:
    soup = BeautifulSoup(_decode_html(html), "lxml")
    return " ".join(soup.get_text(" ", strip=True).split())


def _metadata(content) -> dict[str, object]:
    return {
        "final_url": content.final_url,
        "content_type": content.content_type,
        "byte_count": len(content.body),
        "sha256": _sha256(content.body),
        "redirect_count": content.redirect_count,
        "method": "SafeURLFetcher static HTTP fetch",
    }


def inspect_precious_metals(content) -> dict[str, object]:
    """Return a bounded page-specific summary; never retain the response body."""

    soup = BeautifulSoup(_decode_html(content.body), "lxml")
    matches: list[dict[str, object]] = []
    for anchor in soup.find_all("a", href=True):
        label = " ".join(anchor.get_text(" ", strip=True).split())
        absolute_url = urljoin(content.final_url, anchor["href"])
        searchable = f"{label} {absolute_url}".casefold()
        if not any(term in searchable for term in ("altın", "altin", ".pdf")):
            continue
        split = urlsplit(absolute_url)
        matches.append(
            {
                "label": label[:200],
                "url": absolute_url,
                "path": split.path,
                "query_parameters": sorted({key for key, _value in parse_qsl(split.query)}),
            }
        )
        if len(matches) == 25:
            break

    text = _normalized_text(content.body)
    gold_document = next(
        (item for item in matches if item["path"] == "/dosyalar/kmtp/veriler/kmp_au.pdf"),
        None,
    )
    document_family_paths = [
        item["path"]
        for item in matches
        if re.fullmatch(r"/dosyalar/kmtp/veriler/kmp_[a-z]{2}\.pdf", str(item["path"]))
    ]
    return {
        **_metadata(content),
        "normalized_text_length": len(text),
        "gold_transactions_document": gold_document,
        "observed_document_family_paths": document_family_paths,
        "observed_document_path_pattern": "/dosyalar/kmtp/veriler/kmp_<metal-code>.pdf",
        "date_query_parameter_observed": bool(gold_document and gold_document["query_parameters"]),
        "relevant_links": matches,
        "relevant_link_count_capped": len(matches),
    }


def inspect_xtumy_static(content) -> dict[str, object]:
    soup = BeautifulSoup(_decode_html(content.body), "lxml")
    text = _normalized_text(content.body)
    folded = text.casefold()
    contexts = []
    for node in soup.find_all(string=lambda value: value and "xtumy" in value.casefold()):
        context = " ".join(node.parent.get_text(" ", strip=True).split())[:240]
        if context and context not in contexts:
            contexts.append(context)
        if len(contexts) == 5:
            break
    tables = soup.find_all("table")
    table_summaries = []
    for table in tables[:5]:
        rows = []
        for row in table.find_all("tr")[:5]:
            cells = [
                " ".join(cell.get_text(" ", strip=True).split())[:160]
                for cell in row.find_all(["th", "td"])[:8]
            ]
            if cells:
                rows.append(cells)
        table_summaries.append({"row_count": len(table.find_all("tr")), "sample_rows": rows})
    return {
        **_metadata(content),
        "title": " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else None,
        "normalized_text_length": len(text),
        "normalized_text_sha256": _sha256(text.encode("utf-8")),
        "marker_counts": {marker: folded.count(marker) for marker in _TEXT_MARKERS},
        "xtumy_contexts": contexts,
        "table_count": len(tables),
        "table_row_counts": [len(table.find_all("tr")) for table in tables[:10]],
        "table_summaries": table_summaries,
    }


def inspect_xtumy_rendered() -> dict[str, object]:
    """Render once with an already-installed Chromium; install nothing."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "available": False,
            "method": "Playwright Chromium rendered DOM",
            "error_type": "PlaywrightUnavailable",
            "error": "Playwright is not installed in the active environment.",
        }

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                response = page.goto(XTUMY_URL, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except PlaywrightTimeoutError:
                    pass
                final_url = page.url
                text = " ".join(page.locator("body").inner_text().split())
                folded = text.casefold()
                return {
                    "available": True,
                    "method": "Playwright Chromium rendered DOM",
                    "http_status": response.status if response else None,
                    "final_url": final_url,
                    "normalized_text_length": len(text),
                    "normalized_text_sha256": _sha256(text.encode("utf-8")),
                    "marker_counts": {marker: folded.count(marker) for marker in _TEXT_MARKERS},
                }
            finally:
                browser.close()
    except (PlaywrightError, OSError) as exc:
        return {
            "available": False,
            "method": "Playwright Chromium rendered DOM",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


def run_probe(*, render_xtumy: bool) -> dict[str, object]:
    observed_at = datetime.now(UTC).isoformat()
    limits = URLSafetyLimits(max_redirects=5, max_bytes=5 * 1024 * 1024, max_pdf_pages=25)
    evidence: dict[str, object] = {
        "observed_at_utc": observed_at,
        "probe_version": 1,
        "limits": {
            "max_redirects": limits.max_redirects,
            "max_bytes": limits.max_bytes,
            "max_pdf_pages": limits.max_pdf_pages,
        },
        "raw_response_bodies_retained": False,
        "targets": {},
    }

    with SafeURLFetcher(limits=limits) as fetcher:
        targets = evidence["targets"]
        assert isinstance(targets, dict)
        for name, url, inspector in (
            ("precious_metals", PRECIOUS_METALS_URL, inspect_precious_metals),
            ("xtumy_static", XTUMY_URL, inspect_xtumy_static),
        ):
            try:
                targets[name] = inspector(fetcher.fetch(url))
            except Exception as exc:  # Evidence records the real bounded probe failure.
                targets[name] = {
                    "requested_url": url,
                    "method": "SafeURLFetcher static HTTP fetch",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }

    evidence["targets"]["xtumy_rendered"] = (
        inspect_xtumy_rendered()
        if render_xtumy
        else {
            "available": False,
            "method": "Playwright Chromium rendered DOM",
            "error_type": "NotRequested",
            "error": "Rendered comparison was not requested.",
        }
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render-xtumy", action="store_true")
    args = parser.parse_args()

    evidence = run_probe(render_xtumy=args.render_xtumy)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
