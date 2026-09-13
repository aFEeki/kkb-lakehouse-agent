# MIA capability report

Measured 2026-09-12 17:32 +03 against `https://mia.csp.kloudeks.com/v1`
Chat: `kkbhackathon2026/Qwen3.8-27B` · Embeddings: `kkbhackathon2026/Qwen3-Embedding-8B`

| Capability | Result |
|---|---|
| connectivity | OK |
| reasoning | THINKING MODEL - budget consumed by reasoning |
| thinking_toggle | chat_template_kwargs.enable_thinking=False works |
| tool_calling (thinking off) | SUPPORTED |
| tool_calling (thinking on) | SUPPORTED |
| structured: json_schema (strict) | ENFORCED |
| structured: guided_json (legacy vLLM) | VALID JSON, SCHEMA NOT ENFORCED |
| structured: json_object | VALID JSON, SCHEMA NOT ENFORCED |
| context | at least ~128,000 tokens |
| latency (thinking off) | mean 2.6s |
| latency (thinking on) | mean 7.7s |
| streaming | SUPPORTED, first token 0.13s |
| embeddings | dimension 4096 |
| turkish | sample produced - judge by eye |

## Verdict

Both native tool calling and strict json_schema work. The planner can emit tool calls or schema-constrained JSON, whichever suits. No fallback router needed, and no extra half-day for Track B.

## Notes

- Reasoning tokens count against max_tokens. Any call needing visible output must either disable thinking or budget generously (1024+).

## How to call this model

```python
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}

# schema-constrained planner output
r = client.chat.completions.create(
    model=CHAT, messages=[...], max_tokens=1024, extra_body=NO_THINK,
    response_format={"type": "json_schema",
                     "json_schema": {"name": "op", "schema": OP_SCHEMA, "strict": True}},
)
```

`guided_json` is accepted but NOT enforced on this deployment - do not rely on it.

## Not covered here

- Rate limit / 429 behaviour - push until it breaks, separately
- OCR model - needs a PNG, see SCRUM-60

## Rate limits (SCRUM-35)

Probed 2026-09-13 13:24 with 111 minimal requests (thinking off, 8-token completions), so this measures request rate rather than token throughput.

| Test | Result |
|---|---|
| 1 concurrent | ok=1, 429=0, median 0.98s |
| 2 concurrent | ok=2, 429=0, median 2.79s |
| 4 concurrent | ok=4, 429=0, median 1.02s |
| 8 concurrent | ok=8, 429=0, median 0.25s |
| 16 concurrent | ok=16, 429=0, median 0.83s |
| sequential burst | 80 ok, 0 rate limited |
| embedding batch of 64 | 64 vectors in 1.40s (dim 4096) |

**Caveat:** absence of a 429 within this budget is not proof there is no limit. Treat these as a floor.
