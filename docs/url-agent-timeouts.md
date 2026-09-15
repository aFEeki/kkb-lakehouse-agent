# URL agent time budget

A turn that takes ninety seconds has lost the demo whatever it eventually returns. Every
fetch, render and OCR call in the URL agent carries an explicit timeout, and one `Deadline`
bounds the whole turn on top of those.

## Why per-call timeouts are not enough

A discovery can be two fetches, a browser render and an OCR pass over several pages. Each
one can finish comfortably inside its own timeout and the turn can still run for minutes.
`Deadline` is therefore a single clock for the whole operation, threaded through
`discover_document`, `read_page`, `extract_pdf` and `ocr_images`:

```python
deadline = Deadline(DEFAULT_TOTAL_SECONDS)          # 60s
with bounded_fetcher(per_request_seconds=15) as fetcher:
    result = discover_document(url, query=..., fetcher=fetcher, router=router,
                               deadline=deadline)
```

Before each step, `deadline.require("fetching ...")` either returns the seconds left or
raises `ToolTimeoutError` naming **what** did not start, the size of the budget and how
long had already gone. "Timed out" alone tells an operator nothing.

The rule is that a step never *begins* once nothing is left. How long a call will take
cannot be known in advance, so the deadline does not try to predict it — the per-call
timeout is what bounds a step that has started.

## Bounding each call

| Step | Bound |
|---|---|
| fetch | `bounded_fetcher(per_request_seconds=...)`, default 15s |
| render | `min(renderer.timeout_ms, remaining budget)`, passed into `Renderer.render` |
| OCR | the budget is checked before each call of at most three images |

`SafeURLFetcher` builds its own client at 30s when none is given, and a 60s turn cannot
afford two of those. `bounded_fetcher` injects a client with an explicit timeout instead —
it is a context manager because a client the fetcher did not create is not the fetcher's to
close. The SCRUM-61 safety module is unchanged.

`Renderer.render` takes `timeout_ms` as part of its contract. A renderer that cannot be
bounded is exactly what a turn budget has to rule out.

## Partial results

`ToolTimeoutError` carries `partial`: the OCR pages already read, or the discovery hops
already walked. `extract_pdf` uses it — a scan that timed out halfway returns the pages it
managed to read, with a note saying so:

> Timed out during OCR: 3 of 10 page(s) were read; the rest are missing, not empty.

The distinction in that sentence is the point. An empty page and an unread page look the
same in the output and mean completely different things, so the document says which it is
rather than leaving a reader to assume.

Discovery does not return a partial document, because half a walk is not half an answer —
but the error still carries the hops, so the trace can show how far it got and which link
it was following.

## Testing

`Deadline` takes its clock as an argument, so every timeout test advances a fake clock
instead of sleeping. The suite proves the budget stops a walk whose individual fetches
would each have passed, that the renderer is handed only the time left, and that a timed
out OCR pass keeps what it read.
