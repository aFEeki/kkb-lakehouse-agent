# URL agent PDF and OCR extraction

`extract_pdf` reads a PDF into a `URLDocument`, and `extract_image` reads a standalone
image. Both are registered through `create_content_type_router` (SCRUM-59) and consume the
untrusted bytes returned by `SafeURLFetcher` (SCRUM-61).

## Text first, OCR only as a fallback

`extract_pdf` always runs pypdf first. Text-layer extraction is exact, costs no model quota
and takes milliseconds, so it is never skipped. OCR runs only when **every** page came back
empty, which is what a scanned document looks like.

With no text layer and no OCR backend supplied, the call raises `PDFTextLayerMissingError`
naming the URL and page count. It does not return an empty document: silently answering
"this PDF contains nothing" about a scanned price table is the failure mode this tool exists
to avoid.

## Page attribution

`URLDocument.pages` carries one `ExtractedPage` per page with a 1-based `page_number` and the
`extraction_method` that produced it (`pypdf` or `unlimited-ocr`). The page number is the only
position this tool ever claims.

Unlimited-OCR accepts up to three images per prompt but returns a single text block per
prompt, so a three-page batch would merge three pages into one block with no way to say which
page a number came from. `MIAOCRBackend` therefore sends **one image per call** with
`window_size=128`, the documented single-image setting. `MAX_IMAGES_PER_CALL` is still
enforced, so a backend handed more than three images fails loudly rather than silently
dropping pages. `ocr_images` batches only within that limit and verifies that a backend
returned exactly one block per image it was given.

## Layout tags

Unlimited-OCR can emit `<|ref|>label<|/ref|><|det|>[[x1,y1,x2,y2]]<|/det|>`. `strip_layout_tags`
keeps the `ref` label text, drops the `det` box and removes any residual `<|...|>` control
token. The boxes are dropped rather than republished because their coordinate space is not
documented; a position we cannot interpret is left absent instead of being invented.

## Caching

`OCRCache` keys text by the SHA-256 of the exact rendered image, so re-reading the same
document — or the same page inside a different document — costs no additional model call. The
cache is passed in explicitly; there is no global or hidden state.

## Rendering

Pages are rendered by pypdfium2 at `RENDER_SCALE = 2.0` with `rev_byteorder=True` (RGB rather
than pdfium's native BGR) and encoded to PNG by `encode_png` in this module. The encoder is
written against the PNG specification using only `zlib`, `struct` and NumPy, all of which the
project already declares, so scanned-PDF support adds no new dependency. A test checks the
signature, IHDR fields and that the compressed scanline stream round-trips to the expected
pixels.

## Not covered here

Linked-document discovery (SCRUM-62), browser rendering (SCRUM-63), acceptance tests against
real published sources (SCRUM-64), manual upload (SCRUM-65) and end-to-end timeout
orchestration (SCRUM-66). Table and cell geometry inside a PDF is not extracted; only page
level text is claimed.
