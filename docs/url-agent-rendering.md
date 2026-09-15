# URL agent browser rendering

`read_page` reads a page, and uses a headless browser only when a static fetch does not
carry what was asked for. `PlaywrightRenderer` is the browser side of that.

## Static first, browser second

```python
result = read_page(
    "https://www.borsaistanbul.com/endeks/xtumy",
    fetcher=SafeURLFetcher(),
    router=create_content_type_router(),
    renderer=PlaywrightRenderer(),
    is_sufficient=lambda document: ...,   # what the caller came for
)
result.document          # the extracted page
result.decision.rendered # whether a browser was needed
result.decision.reason   # why, in words, for the trace
```

The static fetch always runs first. It is faster, needs no browser, and for most pages it
is complete. `is_sufficient` is the caller's predicate because only the caller knows what
it came for; the tool never guesses that a page is "probably incomplete". With no
predicate supplied, nothing is ever rendered.

A non-HTML response is never rendered — a PDF does not improve in a browser — and that is
recorded as the reason rather than silently skipped.

## Safety

The browser is pointed at `document.final_url`: the URL that `SafeURLFetcher` (SCRUM-61)
already validated and followed, never at raw caller input. Scheme, private/loopback/
link-local address and redirect checks therefore still apply to the address the browser
loads.

That is a boundary, not a guarantee. A page's own scripts can request further URLs that
this process does not screen, so — exactly as `url-agent-safety.md` already states for
static fetches — a production deployment must also restrict outbound network access at
the network level.

## Waiting for content, never for a clock

`PlaywrightRenderer` navigates with `wait_until="domcontentloaded"`, then waits for
`wait_for_selector` when one is given, and otherwise for the network to go idle. There is
no `sleep` anywhere: a fixed delay is either too short and flaky, or long enough to lose
the demo. `timeout_ms` bounds every wait and defaults to 15s.

Playwright is an optional dependency. Without it, `PlaywrightRenderer.render` raises
`RendererUnavailableError` naming the two commands that fix it, and `read_page` raises the
same error when a page needed rendering and no renderer was supplied. Neither returns a
half-empty document.

## The xtumy question, settled

The SCRUM-54 probe could not say whether `borsaistanbul.com/endeks/xtumy` needs a browser,
because Playwright was not installed when it ran. Measured 2026-09-15 with it installed:

| | Static | Rendered |
|---|---|---|
| Table row counts | `[17, 1, 0]` | `[17, 9, 12]` |
| Index metadata (code, name, ISIN) | present | present |
| Current-value rows | **none** | Değer, change %, high/low, month- and year-to-date, volume |

So the metadata is static and **the values are not**: that page does require rendering.
The live comparison is kept as a test in `test_render.py`, opt-in behind
`KKB_NETWORK_TESTS=1` so neither CI nor a default test run launches a browser or touches
the publisher. It asserts the static response still lacks the values, so if the publisher
ever changes that, the test says so instead of silently keeping the browser in the path.

## Not covered here

Manual upload (SCRUM-65) and end-to-end timeout orchestration across fetch, render and OCR
(SCRUM-66). Rendering is not wired into `discover_document`: link discovery works from the
static DOM today, and a page whose *links* are script-generated would need that wiring.
