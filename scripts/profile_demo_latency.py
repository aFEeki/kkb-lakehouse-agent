#!/usr/bin/env python3
"""Opt-in LIVE sequential benchmark. Read-only gold; real HTTP/SSE, no mocked providers.

Instrumentation wraps existing boundaries only. Durations are inclusive and may overlap.
Run separately before/after a patch; stdout is safe JSONL, never provider exception text.
"""

import argparse
import hashlib
import json
import socket
import threading
import time
from contextlib import ExitStack
from functools import wraps
from unittest.mock import patch

import httpx
import uvicorn
from openai.resources.chat.completions import Completions

from kkb_agent.agent import analyze, router, turn1
from kkb_agent.agent.executor import OperationExecutor
from kkb_agent.agent.planner import OperationPlanner
from kkb_agent.api import main, turn1_runner
from kkb_agent.api.contracts import AskRequest
from kkb_agent.catalog.hybrid import HybridRetrieval
from kkb_agent.catalog.series_source import CatalogSeriesSource
from kkb_agent.config import Settings
from kkb_agent.tools.url_agent.router import ContentTypeRouter
from kkb_agent.tools.url_safety import SafeURLFetcher
from kkb_agent.tools.web_search import SearxNGSearchProvider

CASES = (
    ("turn1", "Konut kredisi bakiyeleri ile konut kredisi faizlerini 2021-2025 arasında göster."),
    ("turn2", "Konut kredisi tutarını TÜFE ile reel hale getir."),
    ("turn3", "Konut fiyat endeksini de ekleyip önceki bulguyu yeniden değerlendir."),
    ("generic", "2022 ile 2024 arasında konut kredilerindeki değişimi göster."),
    ("anomaly", "Konut kredisi serisinde anomalileri bul."),
    ("change", "Konut kredilerinde belirgin kırılma noktaları var mı?"),
    ("web", "TCMB'nin güncel duyurularını web'de ara."),
    ("url", "https://example.org/ oku"),
)


def main_benchmark():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    timings = {}

    def instrument(stack, owner, attribute, label):
        original = getattr(owner, attribute)

        @wraps(original)
        def measured(*a, **kw):
            name = label(kw) if callable(label) else label
            start = time.perf_counter()
            try:
                return original(*a, **kw)
            finally:
                timings.setdefault(name, []).append(time.perf_counter() - start)

        stack.enter_context(patch.object(owner, attribute, measured))

    settings = Settings(vector_retrieval_enabled=False)
    app = main.create_app(settings)
    with ExitStack() as stack:
        for owner, name, label in (
            (turn1_runner, "classify_published_turn", "intent_classification"),
            (turn1_runner, "select_tool", "routing"),
            (OperationPlanner, "plan", "planning"),
            (HybridRetrieval, "resolve", "generic_retrieval"),
            (turn1, "_load_catalog", "duckdb_catalog"),
            (turn1, "_spine_periods", "duckdb_spine"),
            (analyze, "_rows", "duckdb_catalog"),
            (analyze, "_periods", "duckdb_spine"),
            (router, "_resolve", "tool_series_resolution"),
            (CatalogSeriesSource, "fetch", "series_fetch"),
            (OperationExecutor, "execute", "operation_execution"),
            (router, "_call", "analytical_tool"),
            (analyze, "_findings", "generic_findings"),
            (turn1, "_findings", "published_findings"),
            (SearxNGSearchProvider, "search", "searxng"),
            (SafeURLFetcher, "fetch", "url_fetch"),
            (ContentTypeRouter, "route", "url_processing"),
            (main, "_encode_sse", "sse_serialization"),
        ):
            instrument(stack, owner, name, label)
        instrument(
            stack,
            Completions,
            "create",
            lambda kw: (
                "model_" + kw.get("response_format", {}).get("json_schema", {}).get("name", "other")
            ),
        )
        # The server runs in-process so the above probes cover its worker threads;
        # the client uses a real streaming socket rather than TestClient buffering.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Benchmark server unavailable")
                time.sleep(0.01)
            version = 0
            with httpx.Client(timeout=180, trust_env=False) as client:
                for name, question in CASES:
                    timings.clear()
                    payload = dict(
                        question=question,
                        analysis_id="latency-published"
                        if name.startswith("turn")
                        else f"latency-{name}",
                        version=version if name.startswith("turn") else 0,
                    )
                    parse_start = time.perf_counter()
                    AskRequest.model_validate(payload)
                    parsing = time.perf_counter() - parse_start
                    start = time.perf_counter()
                    first = result_at = None
                    outcome = "missing"
                    signature = None
                    frame_version = None
                    stages = []
                    with client.stream(
                        "POST", f"http://127.0.0.1:{port}/ask", json=payload
                    ) as response:
                        for line in response.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            elapsed = time.perf_counter() - start
                            first = elapsed if first is None else first
                            event = json.loads(line[6:])
                            if event["type"] in ("stage_start", "stage_end"):
                                stages.append((event["type"], event["payload"]["stage"], elapsed))
                            if event["type"] == "result":
                                result_at = elapsed
                                frame = event["payload"].get("frame")
                                if frame:
                                    frame_version = frame["version"]
                                    analytical = {
                                        "spine": frame["spine"],
                                        "columns": frame["columns"],
                                        "findings": frame["findings"],
                                    }

                                    # Finding/lineage timestamps and IDs are bookkeeping.
                                    def stable(value):
                                        if isinstance(value, dict):
                                            return {
                                                k: stable(v)
                                                for k, v in value.items()
                                                if k not in {"created_at", "timestamp", "frame_id"}
                                            }
                                        if isinstance(value, list):
                                            return [stable(v) for v in value]
                                        return value

                                    signature = hashlib.sha256(
                                        json.dumps(stable(analytical), sort_keys=True).encode()
                                    ).hexdigest()
                            if event["type"] == "completion":
                                outcome = event["payload"]["outcome"]
                                if name.startswith("turn") and outcome == "succeeded":
                                    version = event["frame_version"]
                    print(
                        json.dumps(
                            dict(
                                label=args.label,
                                path=name,
                                question=question,
                                parsing_probe_seconds=parsing,
                                first_event_seconds=first,
                                result_seconds=result_at,
                                total_seconds=time.perf_counter() - start,
                                outcome=outcome,
                                version=frame_version,
                                analytical_sha256=signature,
                                probes={
                                    k: {"seconds": sum(v), "calls": len(v)}
                                    for k, v in timings.items()
                                },
                                stages=stages,
                            ),
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
        finally:
            server.should_exit = True
            worker.join(timeout=10)
            listener.close()


if __name__ == "__main__":
    main_benchmark()
