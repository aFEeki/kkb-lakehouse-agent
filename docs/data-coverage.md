# Data coverage report

What is in the data pool, what is not, and why. Updated as sources land.

Feeds SCRUM-87. Incomplete while acquisition is in progress — sections marked **not started**
are gaps, not omissions.

---

## Summary

| Source | Status | Coverage |
|---|---|---|
| BDDK Aylık Bülten | **acquired** | 2021-01 → 2026-06, 17 tables, all 10 sector scopes |
| BDDK Haftalık Bülten | **acquired** | 2021-01-08 → 2026-06-26, 286 weeks, 9 tables, TL |
| TCMB EVDS | **acquired** | 250 series, 2021-01 → 2026-06 |
| BDDK FinTürk | **acquired** | 2021-03 → 2026-06, 22 quarters, 7 tables, 82 provinces |
| Live URLs | **not started** | on-demand, no pre-acquisition |

All three BDDK sources required by the organizers are now acquired. They state the
data *alınacaktır* for 2021-01 to 2026-06.

### What the catalog holds

`data/gold/lakehouse.duckdb`, built from bronze by `scripts/build_catalog.py`:

| | |
|---|---|
| Series | **47,015** |
| Observations | **1,330,275** |
| Unservable | **1** (`TP.KKM.K4`, no published unit) |

| Source | Series |
|---|---|
| BDDK FinTürk | 41,522 |
| BDDK Aylık | 4,960 |
| BDDK Haftalık | 283 |
| TCMB EVDS | 250 |

By measure: 34,454 stock · 11,287 ratio · 619 flow · 618 count · 30 rate · 6 index.
599 series accumulate year-to-date and carry the de-cumulation rule to prove it.

Three checks run against this, each verifying the data against itself rather than against
what we expected:

| Check | Scope | Result |
|---|---|---|
| `check_taraf_partitions.py` | 83,808 comparisons | 2 of 3 partitions exact, worst 0.257% |
| `check_finturk_units.py` | 1,782 province-quarters | worst 0.019% |
| `reconcile_bddk_evds.py` | BDDK ↔ EVDS | three stable offsets |

---

## BDDK Aylık Bülten — acquired

Fetched 2026-09-12 (Sektör) and completed 2026-09-14 (the other nine scopes).
**11,220 fetches, zero failures.**

| | |
|---|---|
| Periods | 66 (2021-01 → 2026-06), no gaps |
| Tables | 17 of 17 |
| Sector scope (`taraf`) | all 10 |
| Rows | 339,650 |
| Raw size | 53 MB |
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

### All ten sector scopes held

`taraf` has ten values and all ten are now held: Sektör, Mevduat, Katılım, Kalkınma ve
Yatırım, Yerli Özel, Kamu, Yabancı, and three Mevduat sub-splits. Questions about a
specific bank group are answerable.

The payload names its own scope in every row's first cell, so the crawler's request
parameter is checked against what the response says — a report viewer ignoring `taraf`
would otherwise file one group's figures under another's label.

**The scopes form three partitions, and each must sum to its parent:**

```
Sektör  = Mevduat + Katılım + Kalkınma ve Yatırım               (by bank type)
Sektör  = Yerli Özel + Kamu + Yabancı                           (by ownership)
Mevduat = Mevduat-Yerli Özel + Mevduat-Kamu + Mevduat-Yabancı
```

This is the strongest integrity check we have, because it needs no second source and no
assumption about what the numbers should be. `scripts/check_taraf_partitions.py` asserts
it across 83,808 comparisons; two of the three partitions close exactly on every material
comparison and the third's worst gap is 0.257%.

It earns its keep. It caught the catalog collapsing all ten scopes into one series
(SCRUM-98), and then four rows filed inside balance-sheet tables that are actually ratios
— a ratio does not add across bank groups, so it stands out against an identity every
genuine balance line satisfies.

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

## TCMB EVDS — acquired

Scope declared in `config/evds-series.json`; ingestion in `scripts/ingest_evds_curated.py`.
Output is one parquet per series under `data/silver/evds/` with a `coverage.json`, both
gitignored. The committed config is the reproducible declaration of scope.

The selection is no longer hand-listed. `scripts/walk_evds_catalog.py` walks the published
tree and `scripts/select_evds_series.py` filters it, so the scope can be re-derived and
argued with rather than taken on trust.

| | |
|---|---|
| Catalogue walked | **154 categories, 678 datagroups, 53,792 series** |
| Series configured | **250** |
| Frequencies | 119 quarterly, 93 monthly, 36 weekly, 2 daily |
| Requested range | 2021-01-01 → 2026-06-30 |
| Ingested | 250 of 250, zero failures |
| Full EVDS catalog | **no** — `is_full_evds_catalog: false` |

**The unit is on the datagroup, not the series.** `BIRIMI` is a datagroup field, which is
why every non-rate series ingested before the walk had an empty unit and was refused by
`is_usable()`. 164 of 678 groups publish no unit at all, and a handful name two
possibilities (`Yüzde, TL`); those are excluded rather than resolved by a coin flip.

### All three demo-scenario series are present

| Turn | Series | Code | Frequency |
|---|---|---|---|
| 1 | Konut kredisi faizi | `TP.KTF12` | weekly |
| 2 | Tüketici Fiyat Endeksi | `TP.GENENDEKS.T1` | monthly |
| 3 | Konut Fiyat Endeksi | `TP.KFE.TR` | monthly |

Plus loan rates by type, FX, reserves, deposits including KKM, banking-sector aggregates,
and capacity utilisation.

### The *kullandırılan* question, answered

**Gross new-lending volume does not exist in any source we hold**, and that is now a
finding rather than a failure to find. The whole catalogue was searched: six of 53,792
series match the word, and none is what the demo means — five are the CBRT's own lending
to TMSF and to banks, mostly archived, and the sixth family uses *kullandırımlar dahil* as
a scope qualifier on a weighted-average **interest rate**. `yeni kredi` matches nothing.
BDDK's three publications are balances throughout.

**A net flow does exist, and we hold it.** TCMB's financial accounts publish household
loans on a transactions basis:

| | |
|---|---|
| `TP.FINHESTNKS61014.ZP34` | F.4 Krediler, Hanehalkı (Konsolide Akım) |
| Frequency | quarterly, bin TL, 2010-Q4 → |
| What it is | net incurrence of loan liabilities — new lending **minus** repayments |

Better than differencing a BDDK balance, because a financial-accounts transaction excludes
revaluation and reclassification, which a stock difference silently includes. The maturity
splits `ZP35` and `ZP36` come with it.

The asset-side twin `ZP12` must not be substituted: it is what households *lend*, which
oscillates around zero, against 219–643 bn TL per quarter of borrowing on the liability
side. Pinned by name in the selection and covered by a test.

So a *kullandırılan* question is answered with the net quarterly flow, **labelled as net
rather than gross**, alongside the balance — never a balance presented as a flow. See
DECISIONS #10.

### Still outstanding

**The housing loan interest rate is weekly; the demo table is monthly.** Turn 1 needs a
frequency conversion, and because it is a rate the aggregation rule is mean or period-end,
never a sum. See SCRUM-26.

**One series carries no unit.** `TP.KKM.K4` — its datagroup publishes no `BIRIMI` and its
note does not say. The magnitude is consistent with milyar TL, but consistent is not
stated, so it is held without a unit and `is_usable()` refuses it. It is the only
unservable series in the catalog.

**Topics deliberately out of scope:** international statistics, balance of payments, and
the CBRT's own balance sheet. Questions reaching into those cannot be answered, and that
should be stated rather than approximated.

---

## Live URLs — not started

Read on demand rather than pre-acquired, by design. See the Agent Tools epic.
