# URL agent content-type router

`ContentTypeRouter` turns the untrusted bytes returned by `SafeURLFetcher` (SCRUM-61) into a
typed `URLDocument`. It is deterministic, runs in memory, and contains no LLM, database,
catalog or `AnalysisFrame` dependency, so it can be built and tested standalone.

## Routing on what the response actually is

Routing never consults the request URL or its file extension. A PDF served from an `.aspx`
path routes as a PDF, and an HTML error page served from a `.pdf` path routes as HTML — the
case that silently produces a garbage "extraction" when a tool trusts the extension.

The decision order is:

1. The response `Content-Type`, mapped through `MEDIA_TYPE_KINDS`.
2. Leading-byte inspection (`sniff_kind`) when the declared type is absent, generic
   (`application/octet-stream`) or unmapped. `%PDF-`, OLE2, zip-containing-`xl/`, PNG, JPEG,
   GIF, WEBP and an HTML/XML opening tag are recognised.
3. Refusal. An unrecognised type raises `UnsupportedContentTypeError` naming what was
   returned, rather than guessing at a parse.

A type that is recognised but has no registered handler raises
`UnimplementedContentTypeError`, which is deliberately a different error: "we cannot read
TIFF" and "this composition has no OCR wired up yet" are different facts and a caller should
be able to tell them apart.

## Handlers

Handlers are supplied explicitly as a `Mapping[DocumentKind, ContentHandler]` and copied into
a read-only registry, following `OperationExecutor`. There is no global registry, dynamic
import or plugin discovery. A handler's returned `URLDocument` is checked: a handler that
returns the wrong kind, or raises, fails with the source URL named.

`create_content_type_router()` registers the handlers that need no model call:

| Kind | Method | Yields |
|---|---|---|
| `html` | BeautifulSoup + lxml | text with script/style removed, tables, absolute deduplicated links |
| `excel` | openpyxl (`.xlsx`), xlrd (legacy `.xls`) | one table per worksheet |
| `text` | decode | text, UTF-8 first then cp1254 for Turkish legacy pages |

`pdf_handler` and `image_handler` are constructor arguments because both depend on OCR and
belong to SCRUM-60. Until one is passed, that content type fails as unimplemented.

The fetcher strips the charset parameter from `Content-Type`, so encoding is re-established
here: BeautifulSoup detects it for HTML, and the text handler records which encoding worked in
`extraction_method`.

## Untrusted content

`URLDocument.trust_level` is fixed at `"untrusted"` and the model is frozen. Extracted text is
third-party data to analyze, never an instruction to follow; a test feeds instruction-injection
text through the router and asserts it stays inert, immutable document text. `ExtractionLimits`
bounds text length, table, row, column and link counts so one hostile or merely enormous source
cannot exhaust a turn, and any truncation is recorded in `URLDocument.notes` rather than applied
silently.

## Not covered here

PDF text and OCR extraction (SCRUM-60), linked-document discovery (SCRUM-62), browser
rendering (SCRUM-63), acceptance tests against real published sources (SCRUM-64), manual
upload (SCRUM-65) and end-to-end timeout orchestration (SCRUM-66).
