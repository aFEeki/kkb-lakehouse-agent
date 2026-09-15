# URL agent acceptance checks

"The router supports six types" is not evidence that any of them work. These checks run the
tool against real published sources and assert values the source actually returned, so a
regression is detectable rather than merely possible.

They are opt-in:

```bash
KKB_NETWORK_TESTS=1 .venv/Scripts/python.exe -m pytest \
    tests/kkb_agent/tools/url_agent/test_acceptance_live.py -q
```

The default `pytest -q` and CI skip them. Nothing on a normal push touches a publisher we
depend on for the demo, launches a browser, or spends model quota.

## What is covered, and against what

| Type | Source | Asserted |
|---|---|---|
| HTML | Borsa İstanbul precious-metals landing page | the gold link is found and followed; the reason is recorded |
| text PDF | `kmp_au.pdf`, reached from that page | read by **pypdf**, no OCR; `93.824.682.381` survives with its Turkish separators |
| JS-rendered HTML | `borsaistanbul.com/endeks/xtumy` | metadata is static, the value row is **not** — the evidence for SCRUM-63 |
| text / CSV | TCMB EVDS `type=csv`, with the project's own EVDS key | header row exact; `02-01-2026,42.92290000` |
| image | page 1 of `ith_au.pdf` rendered to PNG, read by live Unlimited-OCR | `49.216,41`, `7.791,56`; no `<\|det\|>`/`<\|ref\|>` left in the text |
| scanned PDF | the same page wrapped as an image-only PDF | falls back to OCR, recovers the same figures, notes say "no text layer" |

`ith_au.pdf` is deliberately **not** the document the handlers were built against — that was
`kmp_au.pdf`. Gold *import* data, not gold *trading* data.

## Two things worth stating plainly

**No published source among the demo URLs is a scanned PDF.** All 21 PDFs on the
precious-metals page carry a text layer, which is good news for the demo and leaves the OCR
fallback untested by any real file. The scanned case is therefore built from a real page
with its text layer removed, rather than from invented content. The OCR is live either way.

**No published source among them is a workbook either.** `kmp_au.xlsx` and `kmp_au.xls`
both 404, and EVDS rejects `type=xlsx` with "Invalid type". A test records that absence and
fails if a workbook ever appears, so the gap stays visible instead of being forgotten. Excel
is covered meanwhile by unit tests over real openpyxl and xlrd output, plus the case that
actually turns up in the wild: an HTML table published under a `.xls` name, which trusting
the extension would hand to xlrd and lose entirely.

## Credentials

`conftest.py` isolates every test from the developer's `.env`, and that default is right —
a unit test that quietly depends on someone's credentials is one that passes on a single
machine. These checks are the exception and say so: `live_settings()` names the file
explicitly, because proving the real key reaches the real service is the point of them.

## Stability

OCR output was measured byte-identical across repeated runs at `temperature=0`, which is
why exact figures are asserted rather than loose patterns. The model does emit HTML
`<table>` markup and, on non-text regions, occasional repeated nonsense; neither is asserted
against here beyond requiring the layout tags to be gone.

A failure in this file means "go and look at the source", not "the code is broken". A
publisher is free to redesign a page at any time, and finding that out from a test is the
entire reason these exist.
