# Borsa Istanbul URL probe

Observed at `2026-09-13T15:28:52.081176+00:00`. The machine-readable evidence is
[`evidence/borsa-url-probe.json`](evidence/borsa-url-probe.json). Raw response bodies are not
retained.

## Method

The probe made one static request to each published demo URL through the SCRUM-61
`SafeURLFetcher`, with at most five redirects, 5 MiB per response and 25 pages for any
declared PDF. It recorded final URLs, content types, byte counts, SHA-256 hashes and bounded
HTML summaries. It then checked whether the existing environment could make one Playwright
comparison for `xtumy`; Playwright was not installed, so no browser was launched and no
browser request was made.

Re-run from the repository root:

```powershell
.venv\Scripts\python.exe scripts/probe_borsa_urls.py `
  --output docs/evidence/borsa-url-probe.json `
  --render-xtumy
```

## Observed evidence

### Precious metals market data

- Requested and final URL:
  `https://www.borsaistanbul.com/veriler/kiymetli-madenler-ve-kiymetli-taslar-piyasasi/piyasa-verileri`
- Static response: `text/html`, 79,765 bytes, no redirect.
- SHA-256: `14504bc85d081de3f7029ec782473870c9166060fd76940578a3ac0095698d72`.
- The static HTML contained an `Altın İşlemleri` link to
  `https://www.borsaistanbul.com/dosyalar/kmtp/veriler/kmp_au.pdf`.
- The observed metal-document family was `kmp_au.pdf`, `kmp_ag.pdf`, `kmp_pl.pdf` and
  `kmp_pd.pdf`, corresponding to the observed path pattern
  `/dosyalar/kmtp/veriler/kmp_<metal-code>.pdf`.
- The gold document URL had no query parameters. No date query parameter was observed.

### XTUMY

- Requested and final URL: `https://www.borsaistanbul.com/endeks/xtumy`.
- Static response: `text/html`, 84,829 bytes, no redirect.
- SHA-256: `e08995dbff06ee4fc4cff86e55b1ef913bbad256d49df1b1f8764a201018f7f8`.
- The static HTML title was `BIST TUM-100 | Borsa İstanbul A.Ş.` and contained `XTUMY`.
- Three table elements were observed, with 17, 1 and 0 rows. The first contained index
  metadata including code `XTUMY`, name `BIST TUM-100` and ISIN `TRAIMKB01380`.
- The second table contained only the observed header cells `Güncel Endeks Değerleri` and
  `Önceki Kapanış`; no value row was present in the static table summary.
- Playwright was not installed in the active environment. A rendered DOM was therefore not
  available for comparison.

## Interpretation

For the precious-metals scenario, the landing page exposes the target as a linked PDF. The
observed gold link is a stable-looking metal-code path rather than a date-query URL; this is
an observation from this timestamp, not a promise that the publisher will keep the pattern.

The `xtumy` static response exposes index metadata but did not expose a populated current-
value row in the observed table structure. This is not enough to claim that JavaScript is
required: that conclusion needs the rendered DOM comparison that this environment could not
perform. SCRUM-63 must remain conditional on a completed comparison.

This probe does not fetch or parse the linked PDF and does not implement content routing,
link discovery or browser rendering.
