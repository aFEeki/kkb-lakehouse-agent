# URL agent manual upload

`read_upload` reads a file a person handed over, through exactly the parsers a fetch would
have used. It is the recovery path for the case where a publisher is down, blocked or slow
on the day.

```python
result = read_upload(
    "kmp_au.pdf",
    body,
    router=create_content_type_router(ocr=MIAOCRBackend(mia)),
    uploaded_by="ege",
    uploaded_at=observed_at,
)
result.document      # the same URLDocument a fetch would have produced
result.provenance    # who supplied it, when, and the sha256 of the exact bytes
```

## The same parsers, deliberately

The bytes go through the same `ContentTypeRouter` as a fetch, so a PDF still gets pypdf
with its OCR fallback, a workbook still gets openpyxl, and an unreadable file is still
refused rather than guessed at. There is no second, more forgiving parser for uploads:
if a document would not have been readable from a URL, uploading it changes nothing, and
that is the point.

Routing still goes by the bytes. `declared_content_type` carries whatever the upload
claimed and may be absent, in which case the router falls back to the leading bytes — the
more trustworthy signal for an upload anyway, since a filename is chosen by whoever named
the file. A page saved as `report.pdf` that is really HTML routes as HTML.

## Saying so, in two places

`UploadProvenance` records filename, uploader, upload time, byte count and the SHA-256 of
the exact bytes. `uploaded_at` is a parameter rather than a clock read inside, so the time
comes from whoever actually observed it and tests stay reproducible.

The document *itself* also carries `UPLOAD_NOTE` as its first note:

> Operator-supplied upload: this document was handed to the system by a person, not
> fetched from a live source.

Both, because a consumer that reads only the document and never unwraps the provenance
must still be unable to present this as something the system fetched. The backlog is
explicit that an upload is a recovery path and not a substitute: showing an upload does not
evidence the live-URL capability the brief asks for, so the interface has to say which one
happened.

## Limits

Uploads are untrusted input like any fetch. `max_bytes` defaults to the same 10 MiB the
fetcher allows; an oversized file is refused with both its size and the limit, and an empty
one is refused outright rather than producing an empty document.

## Not covered here

The HTTP endpoint and the interface control that accept the file, and rehearsing the path
as part of demo prep. This module is the reading half only.
