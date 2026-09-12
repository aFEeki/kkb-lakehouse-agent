#!/usr/bin/env python3
"""SCRUM-34 - what the MIA API actually supports.

Run:  .venv/bin/python scripts/probe_mia.py

Writes docs/mia-capabilities.md.

Two things this probe learned the hard way, kept here so they are not re-learned:

1. Qwen3.8-27B is a REASONING model. It emits a thinking block on a separate
   `reasoning_content` channel, and those tokens count against max_tokens. A small
   budget is consumed entirely by reasoning and returns empty content - which looks
   like a broken endpoint but is not. Disable with
   extra_body={"chat_template_kwargs": {"enable_thinking": False}}.

2. Test tool calling with every REQUIRED parameter present in the prompt. Ask the model
   to add a series without telling it which series, and it will correctly ask a
   clarifying question instead of calling the tool. That is good behaviour, not a
   missing capability.
"""

from __future__ import annotations

import json
import os
import sys
import time
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

results: dict[str, str] = {}
notes: list[str] = []


def log(k: str, v: str, detail: str = "") -> None:
    results[k] = v
    print(f"    -> {v}")
    if detail:
        print(f"       {detail[:300]}")


def err(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


OP_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["add_series_column", "deflate_column", "index_column", "set_chart"],
        },
        "series_id": {"type": "string"},
        "reasoning_tr": {"type": "string"},
    },
    "required": ["operation", "reasoning_tr"],
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_series_column",
            "description": "Bir seriyi mevcut analiz tablosuna yeni sutun olarak ekler.",
            "parameters": {
                "type": "object",
                "properties": {
                    "series_id": {"type": "string", "description": "Katalogdaki seri kimligi"},
                    "label_tr": {"type": "string"},
                },
                "required": ["series_id"],
            },
        },
    }
]

# Every required parameter present. See docstring note 2.
Q_COMPLETE = (
    "Konut kredisi faiz orani serisini tabloya yeni sutun olarak ekle. "
    "series_id: TP_KREDI_FAIZ_KONUT"
)


def probe_connectivity(c: OpenAI) -> bool:
    print("\n[0] Connectivity")
    try:
        r = c.chat.completions.create(
            model=CHAT,
            messages=[{"role": "user", "content": "Merhaba"}],
            max_tokens=64,
            extra_body=NO_THINK,
        )
        log("connectivity", "OK", (r.choices[0].message.content or "")[:80])
        return True
    except Exception as e:
        log("connectivity", "FAILED", err(e))
        return False


def probe_reasoning(c: OpenAI) -> None:
    print("\n[1] Reasoning behaviour")
    try:
        r = c.chat.completions.create(
            model=CHAT, messages=[{"role": "user", "content": "Merhaba, nasilsin?"}], max_tokens=64
        )
        m = r.choices[0].message
        reasoning = getattr(m, "reasoning_content", None) or getattr(m, "reasoning", None)
        content = m.content or ""
        if reasoning and not content:
            log(
                "reasoning",
                "THINKING MODEL - budget consumed by reasoning",
                f"{len(reasoning)} reasoning chars, 0 content chars at max_tokens=64",
            )
            notes.append(
                "Reasoning tokens count against max_tokens. Any call needing visible output "
                "must either disable thinking or budget generously (1024+)."
            )
        elif reasoning:
            log(
                "reasoning", "THINKING MODEL", f"{len(reasoning)} reasoning chars alongside content"
            )
        else:
            log("reasoning", "no separate reasoning channel")

        off = c.chat.completions.create(
            model=CHAT,
            messages=[{"role": "user", "content": "Merhaba, nasilsin?"}],
            max_tokens=64,
            extra_body=NO_THINK,
        )
        om = off.choices[0].message
        if (om.content or "") and not (getattr(om, "reasoning_content", None)):
            results["thinking_toggle"] = "chat_template_kwargs.enable_thinking=False works"
            print("    -> thinking can be disabled via chat_template_kwargs")
    except Exception as e:
        log("reasoning", "FAILED", err(e))


def probe_tool_calling(c: OpenAI) -> None:
    print("\n[2] Native tool calling")
    for label, extra in (("thinking off", NO_THINK), ("thinking on", None)):
        try:
            kw = {"extra_body": extra} if extra else {}
            r = c.chat.completions.create(
                model=CHAT,
                messages=[{"role": "user", "content": Q_COMPLETE}],
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=1024,
                **kw,
            )
            calls = r.choices[0].message.tool_calls
            if calls:
                args = calls[0].function.arguments
                ok = "series_id" in json.loads(args)
                log(
                    f"tool_calling ({label})",
                    "SUPPORTED" if ok else "CALLED, ARGS INCOMPLETE",
                    f"{calls[0].function.name}({args})",
                )
            else:
                log(
                    f"tool_calling ({label})",
                    "NO TOOL CALL",
                    (r.choices[0].message.content or "")[:200],
                )
        except Exception as e:
            log(f"tool_calling ({label})", "REJECTED", err(e))


def probe_structured(c: OpenAI) -> None:
    print("\n[3] Structured output paths")
    attempts = [
        (
            "json_schema (strict)",
            NO_THINK,
            {
                "type": "json_schema",
                "json_schema": {"name": "planner_op", "schema": OP_SCHEMA, "strict": True},
            },
        ),
        ("guided_json (legacy vLLM)", {**NO_THINK, "guided_json": OP_SCHEMA}, None),
        ("json_object", NO_THINK, {"type": "json_object"}),
    ]
    for label, extra, rf in attempts:
        try:
            kw = {"extra_body": extra} if extra else {}
            if rf:
                kw["response_format"] = rf
            r = c.chat.completions.create(
                model=CHAT,
                messages=[
                    {"role": "system", "content": "Yalnizca gecerli JSON dondur."},
                    {"role": "user", "content": Q_COMPLETE},
                ],
                max_tokens=1024,
                **kw,
            )
            out = r.choices[0].message.content or ""
            try:
                parsed = json.loads(out)
                ok = set(OP_SCHEMA["required"]).issubset(parsed)
                log(
                    f"structured: {label}",
                    "ENFORCED" if ok else "VALID JSON, SCHEMA NOT ENFORCED",
                    str(parsed)[:220],
                )
            except json.JSONDecodeError:
                log(f"structured: {label}", "NOT JSON", out[:200])
        except Exception as e:
            log(f"structured: {label}", "REJECTED", err(e))


def probe_context(c: OpenAI) -> None:
    print("\n[4] Context window")
    found = None
    for n in (16_000, 32_000, 64_000, 128_000):
        try:
            c.chat.completions.create(
                model=CHAT,
                messages=[{"role": "user", "content": "veri " * n + "\nKisaca yanitla."}],
                max_tokens=32,
                extra_body=NO_THINK,
            )
            found = n
            print(f"       ~{n:,} tokens: OK")
        except Exception as e:
            print(f"       ~{n:,} tokens: {err(e)[:120]}")
            break
    log("context", f"at least ~{found:,} tokens" if found else "below 16k")


def probe_latency(c: OpenAI) -> None:
    print("\n[5] Latency, thinking on vs off (3 runs each, 300-token answer)")
    prompt = "Turkiye ekonomisinde 2023 yilini uc cumleyle ozetle."
    for label, extra in (("thinking off", NO_THINK), ("thinking on", None)):
        times, toks = [], []
        try:
            for _ in range(3):
                kw = {"extra_body": extra} if extra else {}
                t0 = time.perf_counter()
                r = c.chat.completions.create(
                    model=CHAT,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1024,
                    **kw,
                )
                times.append(time.perf_counter() - t0)
                toks.append(r.usage.completion_tokens if r.usage else 0)
            log(
                f"latency ({label})",
                f"mean {sum(times) / len(times):.1f}s",
                f"runs {[f'{t:.1f}s' for t in times]}, completion tokens {toks}",
            )
        except Exception as e:
            log(f"latency ({label})", "FAILED", err(e))


def probe_streaming(c: OpenAI) -> None:
    print("\n[6] Streaming")
    try:
        t0 = time.perf_counter()
        stream = c.chat.completions.create(
            model=CHAT,
            messages=[{"role": "user", "content": "Enflasyonu uc cumleyle anlat."}],
            max_tokens=512,
            stream=True,
            extra_body=NO_THINK,
        )
        ttft, chunks = None, 0
        for ch in stream:
            if not ch.choices:
                continue
            d = ch.choices[0].delta
            piece = d.content or getattr(d, "reasoning_content", None)
            if piece:
                chunks += 1
                if ttft is None:
                    ttft = time.perf_counter() - t0
        log(
            "streaming",
            f"SUPPORTED, first token {ttft:.2f}s" if ttft else "no content chunks",
            f"{chunks} chunks",
        )
    except Exception as e:
        log("streaming", "NOT SUPPORTED", err(e))


def probe_embeddings(c: OpenAI) -> None:
    print("\n[7] Embeddings")
    try:
        r = c.embeddings.create(
            model=EMBED,
            input=["Konut kredisi kullandirim tutari", "Konut kredisi faiz orani"],
            encoding_format="float",
        )
        log("embeddings", f"dimension {len(r.data[0].embedding)}", f"{len(r.data)} vectors")
    except Exception as e:
        log("embeddings", "FAILED", err(e))


def probe_turkish(c: OpenAI) -> None:
    print("\n[8] Turkish quality - a native speaker judges this")
    try:
        r = c.chat.completions.create(
            model=CHAT,
            max_tokens=600,
            extra_body=NO_THINK,
            messages=[
                {
                    "role": "user",
                    "content": "Konut kredisi faizleri 2024'te %2.5'ten %4.1'e "
                    "yukselirken kredi hacmi reel "
                    "olarak %18 daraldi. Bu iliskiyi bir bankaciya iki cumleyle acikla.",
                }
            ],
        )
        out = (r.choices[0].message.content or "").strip()
        print(f"\n{out}\n")
        log("turkish", "sample produced - judge by eye")
    except Exception as e:
        log("turkish", "FAILED", err(e))


def verdict() -> str:
    tool_ok = any(v == "SUPPORTED" for k, v in results.items() if k.startswith("tool_calling"))
    schema_ok = results.get("structured: json_schema (strict)") == "ENFORCED"
    if tool_ok and schema_ok:
        return (
            "Both native tool calling and strict json_schema work. The planner can emit "
            "tool calls or schema-constrained JSON, whichever suits. No fallback router "
            "needed, and no extra half-day for Track B."
        )
    if schema_ok:
        return "Strict json_schema enforces the operation schema. Planner emits constrained JSON."
    if tool_ok:
        return "Native tool calling works. Planner emits tool calls; validate args with Pydantic."
    return (
        "No reliable structured output. Closed-enum prompting, strict parsing, one bounded "
        "retry, deterministic keyword router as fallback."
    )


def write_report() -> Path:
    out = ROOT / "docs" / "mia-capabilities.md"
    out.parent.mkdir(exist_ok=True)
    lines = [
        "# MIA capability report",
        "",
        f"Measured {time.strftime('%Y-%m-%d %H:%M %Z')} against `{BASE_URL}`",
        f"Chat: `{CHAT}` · Embeddings: `{EMBED}`",
        "",
        "| Capability | Result |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in results.items()]
    lines += ["", "## Verdict", "", verdict(), ""]
    if notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    lines += [
        "## How to call this model",
        "",
        "```python",
        'NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}',
        "",
        "# schema-constrained planner output",
        "r = client.chat.completions.create(",
        "    model=CHAT, messages=[...], max_tokens=1024, extra_body=NO_THINK,",
        '    response_format={"type": "json_schema",',
        '                     "json_schema": {"name": "op", "schema": OP_SCHEMA, "strict": True}},',
        ")",
        "```",
        "",
        "`guided_json` is accepted but NOT enforced on this deployment - do not rely on it.",
        "",
        "## Not covered here",
        "",
        "- Rate limit / 429 behaviour - push until it breaks, separately",
        "- OCR model - needs a PNG, see SCRUM-60",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> int:
    if not KEY or KEY == "API_KEYINIZ":
        print("MIA_API_KEY is not set. Put the real key in .env (gitignored).")
        return 1

    print(f"MIA probe  |  {BASE_URL}\nchat={CHAT}\nembed={EMBED}")
    c = OpenAI(api_key=KEY, base_url=BASE_URL, timeout=180.0, max_retries=0)

    if not probe_connectivity(c):
        return 1
    probe_reasoning(c)
    probe_tool_calling(c)
    probe_structured(c)
    probe_context(c)
    probe_latency(c)
    probe_streaming(c)
    probe_embeddings(c)
    probe_turkish(c)

    print("\n" + "=" * 72)
    print("VERDICT: " + verdict())
    print("=" * 72)
    print(f"\nWritten to {write_report().relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
