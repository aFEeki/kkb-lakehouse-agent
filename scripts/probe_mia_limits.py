#!/usr/bin/env python3
"""SCRUM-35 - find the MIA rate limit before demo day does.

    .venv/bin/python scripts/probe_mia_limits.py

Deliberately pushes until the service pushes back, then stops. Appends findings to
docs/mia-capabilities.md.

Bounded on purpose
------------------
This is the one probe designed to provoke a failure, so it is capped in three ways:
every phase has a hard request budget, the whole run has a global budget, and it stops
the moment a 429 appears rather than characterising the limit by repeatedly hitting it.
Knowing roughly where the ceiling is beats knowing it precisely and being throttled for
the rest of the evening.

Requests are tiny - thinking disabled, 8-token completions - so this measures the
request-rate limit rather than a token-throughput limit.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

BASE_URL = os.getenv("MIA_BASE_URL", "https://mia.csp.kloudeks.com/v1")
CHAT = os.getenv("MIA_CHAT_MODEL", "kkbhackathon2026/Qwen3.8-27B")
EMBED = os.getenv("MIA_EMBED_MODEL", "kkbhackathon2026/Qwen3-Embedding-8B")
KEY = os.getenv("MIA_API_KEY", "")

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
GLOBAL_BUDGET = 220


@dataclass
class Outcome:
    ok: int = 0
    rate_limited: int = 0
    other_errors: list[str] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    retry_after: str | None = None


spent = 0


def ping(client: OpenAI) -> tuple[bool, float, Exception | None]:
    """One minimal request. Returns (ok, seconds, error)."""
    global spent
    spent += 1
    t0 = time.perf_counter()
    try:
        client.chat.completions.create(
            model=CHAT,
            messages=[{"role": "user", "content": "1"}],
            max_tokens=8,
            extra_body=NO_THINK,
        )
        return True, time.perf_counter() - t0, None
    except Exception as e:  # noqa: BLE001 - the error itself is the measurement
        return False, time.perf_counter() - t0, e


def is_rate_limit(e: Exception) -> bool:
    s = f"{type(e).__name__} {e}".lower()
    return "429" in s or "rate" in s and "limit" in s or "too many requests" in s


def retry_after_of(e: Exception) -> str | None:
    resp = getattr(e, "response", None)
    if resp is not None:
        for h in ("retry-after", "x-ratelimit-reset", "x-ratelimit-remaining",
                  "x-ratelimit-limit"):
            v = resp.headers.get(h) if hasattr(resp, "headers") else None
            if v:
                return f"{h}={v}"
    return None


def phase_concurrency(client: OpenAI, levels: tuple[int, ...], budget: int) -> dict[int, Outcome]:
    print("\n[1] Concurrency ramp — how many simultaneous requests are accepted")
    results: dict[int, Outcome] = {}
    for n in levels:
        if spent + n > budget:
            print(f"    stopping: request budget reached before level {n}")
            break
        out = Outcome()
        with ThreadPoolExecutor(max_workers=n) as pool:
            for ok, dt, err in pool.map(lambda _: ping(client), range(n)):
                if ok:
                    out.ok += 1
                    out.latencies.append(dt)
                elif is_rate_limit(err):
                    out.rate_limited += 1
                    out.retry_after = out.retry_after or retry_after_of(err)
                else:
                    out.other_errors.append(f"{type(err).__name__}: {err}"[:120])
        results[n] = out
        med = f"{statistics.median(out.latencies):.2f}s" if out.latencies else "—"
        print(f"    {n:>3} parallel: ok={out.ok:<3} 429={out.rate_limited:<3} "
              f"other={len(out.other_errors):<2} median {med}")
        if out.other_errors:
            print(f"        e.g. {out.other_errors[0]}")
        if out.rate_limited:
            print(f"    -> rate limited at {n} concurrent. Stopping the ramp.")
            if out.retry_after:
                print(f"       header: {out.retry_after}")
            break
        time.sleep(1.0)
    return results


def phase_burst(client: OpenAI, count: int, budget: int) -> Outcome:
    print(f"\n[2] Sequential burst — {count} requests back to back, no delay")
    out = Outcome()
    t0 = time.perf_counter()
    for _ in range(count):
        if spent >= budget:
            print("    stopping: request budget reached")
            break
        ok, dt, err = ping(client)
        if ok:
            out.ok += 1
            out.latencies.append(dt)
        elif is_rate_limit(err):
            out.rate_limited += 1
            out.retry_after = retry_after_of(err)
            print(f"    -> 429 after {out.ok} successful requests "
                  f"in {time.perf_counter()-t0:.1f}s "
                  f"({out.ok/max(time.perf_counter()-t0, 1e-9)*60:.0f}/min)")
            if out.retry_after:
                print(f"       header: {out.retry_after}")
            break
        else:
            out.other_errors.append(f"{type(err).__name__}: {err}"[:120])
            if len(out.other_errors) >= 3:
                print(f"    stopping: repeated non-rate-limit errors — {out.other_errors[-1]}")
                break
    elapsed = time.perf_counter() - t0
    if not out.rate_limited:
        print(f"    no 429 in {out.ok} requests over {elapsed:.1f}s "
              f"({out.ok/max(elapsed, 1e-9)*60:.0f}/min sustained)")
    return out


def phase_embeddings(client: OpenAI, batch: int) -> str:
    print(f"\n[3] Embedding batch — {batch} inputs in one call")
    try:
        t0 = time.perf_counter()
        r = client.embeddings.create(
            model=EMBED, input=[f"konut kredisi {i}" for i in range(batch)],
            encoding_format="float",
        )
        dt = time.perf_counter() - t0
        return f"{len(r.data)} vectors in {dt:.2f}s (dim {len(r.data[0].embedding)})"
    except Exception as e:  # noqa: BLE001
        return f"failed at batch {batch}: {type(e).__name__}: {str(e)[:100]}"


def main() -> int:
    if not KEY or KEY == "API_KEYINIZ":
        print("MIA_API_KEY is not set.")
        return 1

    ap = argparse.ArgumentParser()
    ap.add_argument("--burst", type=int, default=80, help="sequential burst size (default 80)")
    ap.add_argument("--budget", type=int, default=GLOBAL_BUDGET,
                    help=f"total request cap (default {GLOBAL_BUDGET})")
    a = ap.parse_args()

    print(f"MIA limits probe | {BASE_URL}\nmodel={CHAT}")
    print(f"request budget: {a.budget}  (stops early on first 429)")

    client = OpenAI(api_key=KEY, base_url=BASE_URL, timeout=60.0, max_retries=0)

    conc = phase_concurrency(client, (1, 2, 4, 8, 16), a.budget)
    burst = phase_burst(client, a.burst, a.budget)
    emb = phase_embeddings(client, 64)

    max_conc = max((n for n, o in conc.items() if o.rate_limited == 0 and not o.other_errors),
                   default=0)
    limited_at = next((n for n, o in conc.items() if o.rate_limited), None)

    print(f"\n{'=' * 72}")
    print(f"requests spent: {spent}")
    if limited_at:
        print(f"VERDICT: rate limited at {limited_at} concurrent requests; "
              f"{max_conc} ran clean.")
    elif burst.rate_limited:
        print(f"VERDICT: no concurrency limit hit up to {max_conc}; "
              f"sequential burst hit 429 after {burst.ok} requests.")
    else:
        print(f"VERDICT: no rate limit found within budget. {max_conc} concurrent and "
              f"{burst.ok} back-to-back requests all succeeded.")
        print("         A ceiling exists but sits above what this probe spent. Treat the")
        print("         measured numbers as a floor, not the limit.")
    print("=" * 72)

    lines = [
        "",
        "## Rate limits (SCRUM-35)",
        "",
        f"Probed {datetime.now().strftime('%Y-%m-%d %H:%M')} with {spent} minimal requests "
        "(thinking off, 8-token completions), so this measures request rate rather than "
        "token throughput.",
        "",
        "| Test | Result |",
        "|---|---|",
    ]
    for n, o in conc.items():
        med = f"{statistics.median(o.latencies):.2f}s" if o.latencies else "—"
        lines.append(f"| {n} concurrent | ok={o.ok}, 429={o.rate_limited}, median {med} |")
    lines.append(
        f"| sequential burst | {burst.ok} ok, {burst.rate_limited} rate limited |"
    )
    lines.append(f"| embedding batch of 64 | {emb} |")
    if burst.retry_after or any(o.retry_after for o in conc.values()):
        ra = burst.retry_after or next(o.retry_after for o in conc.values() if o.retry_after)
        lines.append(f"| rate-limit header | `{ra}` |")
    lines += ["", "**Caveat:** absence of a 429 within this budget is not proof there is no "
                  "limit. Treat these as a floor.", ""]

    report = ROOT / "docs" / "mia-capabilities.md"
    if report.exists():
        report.write_text(report.read_text(encoding="utf-8") + "\n".join(lines), encoding="utf-8")
        print(f"\nAppended to {report.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
