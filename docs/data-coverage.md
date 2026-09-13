# Data coverage report

What is in the data pool, what is not, and why. Updated as sources land.

Feeds SCRUM-87. Incomplete while acquisition is in progress — sections marked **not started**
are gaps, not omissions.

---

## Summary

| Source | Status | Coverage |
|---|---|---|
| BDDK Aylık Bülten | **acquired** | 2021-01 → 2026-06, 17 tables, Sektör only |
| BDDK Haftalık Bülten | **acquired** | 2021-01-08 → 2026-06-26, 286 weeks, 9 tables, TL |
| TCMB EVDS | **first subset acquired** | 22 series, 2021-01 → 2026-06 |
| BDDK FinTürk | **acquired** | 2021-03 → 2026-06, 22 quarters, 7 tables, 82 provinces |
| Live URLs | **not started** | on-demand, no pre-acquisition |

All three BDDK sources required by the organizers are now acquired. They state the
data *alınacaktır* for 2021-01 to 2026-06.

---

## BDDK Aylık Bülten — acquired

Fetched 2026-09-12 in a single 40-minute pass. **1,122 fetches, zero failures.**

| | |
|---|---|
| Periods | 66 (2021-01 → 2026-06), no gaps |
| Tables | 17 of 17 |
| Sector scope (`taraf`) | Sektör (10001) only |
| Rows | 33,965 |
| Raw size | 5.3 MB |
| Location | `data/bronze/bddk/aylik/` (gitignored) |
| Manifest | `data/bronze/bddk/manifest_aylik.jsonl` |

Source is a JSON report endpoint, not a file download, so values arrive typed and columns
arrive named. See `scripts/crawl_bddk_aylik.py`.

### Tables held

1 Bilanço · 2 Kar Zarar · 3 Krediler · 4 Tüketici Kredileri · 5 Sektörel Kredi Dağılımı ·
6 KOBİ Kredileri · 7 Sendikasyon Seküritizasyon · 8 Menkul Kıymetler ·
9 Mevduat Türler İtibarıyla · 10 Mevduat Vade İtibarıyla · 11 Likidite Durumu ·
12 Sermaye Yeterliliği · 13 Yabancı Para Pozisyonu · 14 Bilanço Dışı İşlemler ·
15 Rasyolar · 16 Diğer Bilgiler · 17 Yurt Dışı Şube Rasyoları

### Known characteristics

**Units vary by table.** Table 5 reports in `bin TL`; tables 1-4 and 6-14 in `milyon TL`.
Tables 15-17 carry no unit at all — they are ratios and counts. The caption states the unit,
so it is captured at parse time rather than inferred.

**Tables 15-17 state no period in the caption.** For those three the payload carries no
evidence of which period it is; the manifest's recorded request parameters are the only
provenance. Relevant to the trust layer.

**Five schema changes fall inside the range**, all confirmed as regulatory reorganisation
rather than data errors:

| Table | Period | Change |
|---|---|---|
| 3 Krediler | 2022-01 | New loan categories; *Mal Karşılığı Vesaikin Finansmanı* removed |
| 8 Menkul Kıymetler | 2022-09 | *Altın Tahvili* and *Altına Dayalı Kira Sertifikası* added |
| 12 Sermaye Yeterliliği | 2021-06 | Basel risk-weight buckets reorganised |
| 12 Sermaye Yeterliliği | 2021-11 | *Risk Ağırlığı %25*, *KDA Riskine Esas Tutar* added |
| 12 Sermaye Yeterliliği | 2022-06 | *Risk Ağırlığı %500* added |

**Four row labels change over time** because they embed references to other row numbers,
which shift when rows are inserted. Display name is therefore not a safe series key — see
SCRUM-20.

### Not acquired: the other nine sector scopes

`taraf` has ten values. Only **Sektör** (whole sector) is held. The nine outstanding are
Mevduat, Katılım, Kalkınma ve Yatırım, Yerli Özel, Kamu, Yabancı, and three Mevduat
sub-splits.

Deliberate: that is roughly 10,000 further requests, about six hours at the current polite
delay. It should follow a decision about which sector splits the questions actually need,
rather than being fetched speculatively. Until then, **any question asking about a specific
bank group cannot be answered**, and that limitation should be stated rather than
approximated with sector totals.

---

## BDDK Haftalık Bülten — acquired

Fetched 2026-09-13 in a single 100-minute pass. **2,574 files, zero failures.**

| | |
|---|---|
| Periods | 286 weeks (2021-01-08 → 2026-06-26), no gaps |
| Tables | 9 of 9, every period |
| Currency | TL only (a USD option exists and would double the count) |
| Sector scope | default only — a `taraf` dimension exists here too and is unfetched |
| Raw size | 504 MB (39 MB gzipped) |
| Location | `data/bronze/bddk/haftalik/TL/` (gitignored) |
| Manifest | `data/bronze/bddk/manifest_haftalik.jsonl` |

Tables: Krediler · Takipteki Alacaklar · Menkul Değerler · Mevduat · Diğer Bilanço
Kalemleri · Bilanço Dışı İşlemler · Bankalarda Saklanan Menkul Değerler 1 and 2 ·
Yabancı Para Pozisyonu

### Shape differs from monthly, and it matters

The monthly bulletin is a JSON endpoint. **Weekly is a stateful ASP.NET form flow serving
HTML**, so the stored bronze artefacts are raw HTML pages rather than JSON payloads:

- Every POST needs a fresh `__RequestVerificationToken`; without it the endpoint returns 500
- Selection is session state — period must be set before iterating tables
- Each response renders the same table at **three precisions** (0, 2 and 5 decimals). The
  5-decimal variant is the one to parse
- **Numeric cells are Turkish-formatted text**: `12.694.338,76219`. This is why SCRUM-17 is
  real work for this source and was not needed for monthly
- Row labels carry the same embedded row-number references as monthly
  (`Toplam Krediler (2+10)`), so the SCRUM-96 identity normalisation applies here unchanged

Size is the notable difference: 504 MB against monthly's 8 MB, because each page carries
full site chrome. It compresses to 39 MB, which is still too large for casual sharing —
re-running the crawler is the better distribution route for this source.

## BDDK FinTürk — acquired

Fetched 2026-09-13 in 7 minutes. **154 files, zero failures.**

| | |
|---|---|
| Periods | 22 quarters (2021-03 → 2026-06) |
| Tables | 7 of 7, every period |
| Provinces | **82**, all in every file |
| Bank groups | 7 (Sektör, Mevduat, Katılım, Kalkınma ve Yatırım, Kamu, Yerli Özel, Yabancı) |
| Rows per file | 574 (82 provinces × 7 groups) |
| Raw size | 10 MB |
| Location | `data/bronze/bddk/finturk/` (gitignored) |
| Manifest | `data/bronze/bddk/manifest_finturk.jsonl` |

Tables carry their unit in the name: Krediler (Bin TL), Mevduat (Bin TL), Bireysel
Bankacılık (Bin TL), Seçilmiş Sektörel Krediler (Bin TL), Oranlar (%), and two more.

### Quarterly, and it must stay quarterly

Periods are `2021-3`, `2021-6`, `2021-9`, `2021-12`. **Do not resample into months that do
not exist.** The data contract already forbids producing unobserved periods as if
observed; this is the source where that rule bites.

### Cheap to acquire, because of two array parameters

`POST /BultenFinturk/tr/Home/VeriGetir` takes `sehirList` and `tarafList` as arrays, and
the city list accepts the sentinel `HEPSİ`, which returns all 82 provinces at once.
Passing every bank group alongside collapses acquisition to one request per table-period:
**154 calls rather than the ~12,000** a province-by-province loop would have made.

### Province totals reconcile to roughly the national figure

Summing the 82 provinces for Sektör, 2025-12, gives **23,714,596 milyon TL** against BDDK
monthly's `Toplam Krediler` of **23,122,195** — a 2.56% gap, the same order as the
BDDK↔EVDS offsets in SCRUM-94. FinTürk reports *Toplam Nakdi Krediler* while the monthly
bulletin reports *Toplam Krediler*, so the two are not defined identically. The geography
adding up this closely validates the acquisition; the exact definitional difference is
follow-up, not a blocker.

Distribution is plausible: İstanbul 34.2%, Ankara 13.3%, İzmir 5.4%.

### The catalog does not yet model province

`il` is a dimension the series catalog has no field for. Decide how it is represented
before loading this into the catalog, or it will be flattened into series names and become
unqueryable.

---

## TCMB EVDS — first subset acquired

Scope declared in `config/evds-series.json`; ingestion in `scripts/ingest_evds_curated.py`.
Output is one parquet per series under `data/silver/evds/` with a `coverage.json`, both
gitignored. The committed config is the reproducible declaration of scope.

| | |
|---|---|
| Series configured | **22** |
| Frequencies | 13 monthly, 7 weekly, 2 daily |
| Requested range | 2021-01-01 → 2026-06-30 |
| Full EVDS catalog | **no** — `is_full_evds_catalog: false` |

### All three demo-scenario series are present

| Turn | Series | Code | Frequency |
|---|---|---|---|
| 1 | Konut kredisi faizi | `TP.KTF12` | weekly |
| 2 | Tüketici Fiyat Endeksi | `TP.GENENDEKS.T1` | monthly |
| 3 | Konut Fiyat Endeksi | `TP.KFE.TR` | monthly |

Plus loan rates by type, FX, reserves, deposits including KKM, banking-sector aggregates,
and capacity utilisation.

### Two things this subset does not resolve

**The housing loan interest rate is weekly; the demo table is monthly.** Turn 1 needs a
frequency conversion, and because it is a rate the aggregation rule is mean or period-end,
never a sum. See SCRUM-26.

**There is still no housing loan *flow* series.** BDDK table 4 gives the balance
outstanding and EVDS provides the rate, but neither gives *kullandırılan* — new lending
extended, which is what the published question literally asks for. Decision #10 remains
open and now has to be answered from what actually exists rather than in the abstract.

### Not acquired

22 series against a planned pool of roughly 250 (DECISIONS #4). The configured `known_gaps`
records this explicitly. Questions outside the covered categories cannot currently be
answered, and that should be stated rather than approximated.

---

## Live URLs — not started

Read on demand rather than pre-acquired, by design. See the Agent Tools epic.
