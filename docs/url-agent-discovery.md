# URL agent linked-document discovery

`discover_document` walks from a landing page to the document it links, then reads that
document through the content-type router (SCRUM-59). It is deterministic and consults no
model: the link it takes is decided by term overlap, and every step records what it did and
why.

## What it does

```python
result = discover_document(
    "https://www.borsaistanbul.com/veriler/kiymetli-madenler-.../piyasa-verileri",
    query="altın işlemleri",
    fetcher=SafeURLFetcher(),
    router=create_content_type_router(ocr=MIAOCRBackend(mia)),
)
result.document.text     # the PDF's contents
result.hops              # the trail that produced it
```

A URL that is already the document needs no hop and is returned directly, so a caller does
not have to know in advance whether it was handed a landing page or a file.

## Choosing a link

Each candidate link scores:

| Signal | Weight |
|---|---|
| query term found in the link text | 2.0 |
| query term found in the link URL | 1.0 |
| URL ends in `.pdf`, `.xlsx`, `.xls`, `.csv`, `.txt` **and** at least one term already matched | 1.0 |

The document-suffix bonus deliberately requires a term match first. Without that condition
every PDF on the page scores above zero, so a request for silver happily follows the link
for gold — the page's first document link would win on the suffix bonus alone.

A link that matches nothing scores zero and is never followed. When no link scores, the call
raises `DocumentNotFoundError` naming the query and how many links were considered, instead
of following its best guess.

Ties break towards the link that appears first in the page, so the same page and query always
produce the same choice.

## Turkish matching

Terms are casefolded with the project's `turkish_casefold`, because `str.lower()` maps `I` to
`i` where Turkish needs `ı`. On top of that, `fold_ascii` compares the unaccented skeletons,
so `Altın`, `ALTIN` and `altin` are all the same word. That second step is what makes the
real case work: publishers write Turkish without diacritics as often as with, and URL slugs
are ASCII regardless.

## Bounds and the trace

`max_hops` defaults to 2 and is enforced: exceeding it raises `HopLimitError` naming where it
stopped. A URL already visited is never followed again, so a page that links back to itself
cannot loop.

`DiscoveredDocument.hops` is the audit trail. Each `DiscoveryHop` carries the page URL, what
kind of document it turned out to be, the link followed, its score, and a `reason` in words —
for example `link text matched ['altın', 'işlemleri']; URL ends in .pdf`. The final hop
records the accepted document. This is what the interface's trace panel should show; a
choice the system cannot explain is not one a judge should trust.

## Not covered here

Browser rendering for pages that build their links in JavaScript (SCRUM-63), acceptance tests
against the live published sources (SCRUM-64), manual upload (SCRUM-65) and timeout
orchestration (SCRUM-66). The scoring weights are fixed constants, chosen to be explainable
rather than tuned against any particular publisher.
