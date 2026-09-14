# The series catalog — a guide for the team

Everything the agent can answer from is in one DuckDB file. This is how to get it, how to
query it, and which six columns decide whether the number you get back is right.

`data-coverage.md` is the exhaustive reference — per-source acquisition detail, schema
changes, manifests. This is the practical one: read it once before your first query.

| | |
|---|---|
| Series | **47,015** |
| Observations | **1,330,275** |
| Sources | 4 |
| Unservable | **0** |
| Coverage | 2021-01 → 2026-06 |

---

## 1. Get the data

The whole pool is one tarball. It is gitignored — 768 MB of raw regulatory filings does not
belong in git — so it is shared as a file with a checksum.

```bash
shasum -a 256 kkb-data-snapshot-2026-09-14-91d751a.tgz
# 59c92ca43f24bc326c89affaba60c85e9c1ab1ed33ab03a16282b24b2d3ab26f

tar -xzf kkb-data-snapshot-2026-09-14-91d751a.tgz -C /path/to/kkb-lakehouse-agent/
```

It unpacks straight into `data/` — bronze, silver and gold all land in place. The filename
carries the commit that built it, so when your numbers do not match someone else's, that is
the first thing to compare.

### The gold layer is derived, not authoritative

`data/gold/lakehouse.duckdb` is in the archive as a convenience. **Bronze is the only source
of truth.** If you distrust the catalog, rebuild it:

```bash
.venv/bin/python scripts/build_catalog.py   # ~60s
```

The same bronze always produces the same catalog. That determinism is the whole reason the
snapshot can be shared at all — it is what lets four people arrive at identical numbers from
the same input.

---

## 2. What's in it

Four sources. Three are the BDDK publications the organizers named explicitly; the fourth is
TCMB's EVDS, which supplies the rates, prices and FX everything else has to be measured
against.

| Source | Series | Grain | Coverage |
|---|---:|---|---|
| **BDDK FinTürk** (İllere göre) | 41,522 | quarterly · 82 provinces × 7 bank groups × 7 tables | 2021-Q1 → 2026-Q2 |
| **BDDK Aylık Bülten** | 4,960 | monthly · 17 tables × **10 bank-group scopes** | 2021-01 → 2026-06 |
| **BDDK Haftalık Bülten** | 283 | weekly · 9 tables, TL | 2021-01-08 → 2026-06-26 |
| **TCMB EVDS** | 250 | daily / weekly / monthly / quarterly | 2021-01 → 2026-06 |

Acquisition was **14,198 requests with zero failures**. Every file has a manifest row
recording the requested period, HTTP status, byte count and sha256, so a download can be
checked rather than assumed.

### What the series measure

| Type | Count | What it means | Legal to… |
|---|---:|---|---|
| `stock` | 34,454 | a position at a moment — a loan balance | take the period end |
| `ratio` | 11,287 | one quantity over another | take the period end; **never sum** |
| `flow` | 619 | activity during a period | sum across periods |
| `count` | 618 | branches, banks, ATMs, staff | sum across provinces |
| `rate` | 30 | an interest rate | average; never sum |
| `index` | 6 | a rebased level — CPI, house prices | take the period end |

**599 series accumulate year-to-date** and carry the evidence for that classification in the
row. Every other series is a level.

---

## 3. How to query it

Two tables. `series_catalog` is one row per series — what it is. `series_observations` is
long format, one row per series per period — the numbers.

**There is no DuckDB to install.** It is an embedded library, already in `.venv`, with no
server and no CLI on your PATH. `data/gold/lakehouse.duckdb` is the entire database — one
file. Open it read-only:

```python
import duckdb
con = duckdb.connect("data/gold/lakehouse.duckdb", read_only=True)
con.execute(QUERY).fetchall()          # or .fetchdf() for a DataFrame
```

```sql
-- find something
SELECT series_id, name_tr, sector_scope, unit_raw, measure_type, observations
FROM   series_catalog
WHERE  lower(name_tr) LIKE '%konut%' AND source = 'bddk_aylik'
LIMIT  10;

-- then pull it, in real lira
SELECT o.period, o.value * c.scale_factor AS try
FROM   series_catalog c
JOIN   series_observations o USING (series_id)
WHERE  c.series_id = 'bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut'
ORDER  BY o.period;
```

> **Always multiply by `scale_factor`.** Table 5 of the monthly bulletin publishes in
> `bin TL` where tables 1–4 use `milyon TL`. Reading them side by side without scaling is a
> 1000× error, and it looks completely plausible on a chart.

A `NULL` value means *not observed*. It is never zero-filled and never interpolated — if a
period is missing from a series, that period was not published.

---

## 4. The six fields that matter

The catalog has 24 columns. None are decoration — each exists because getting it wrong
produces a number that is wrong *and plausible*, which is the only kind that survives review.
These six will bite you first.

| Column | What it is | What goes wrong if you ignore it |
|---|---|---|
| `cumulative_mode` | `none` or `ytd` | 599 series reset each January. A month-over-month change taken from one without de-cumulating is wrong by an order of magnitude — and in December it is wrong by twelve. |
| `measure_type` | stock / flow / ratio / rate / count / index | A housing loan *balance* is not housing lending *extended*. Summing a ratio across 82 provinces produces a number with no meaning. |
| `scale_factor` | multiplier to reach `unit_normalized` | bin TL against milyon TL is 1000×. Per-capita TL against bin TL is a million. |
| `sector_scope` | Sektör, Mevduat, Katılım, Kamu… | Ten bank groups publish the same table. "Housing loans" means 801 milyar TL sector-wide and 76 milyar TL for participation banks. |
| `native_freq` | `D` / `W` / `M` / `Q` | FinTürk is quarterly. Resampling it into months invents observations that were never published. |
| `province` | `NULL` = nationwide | FinTürk is per-province. İstanbul is not Türkiye, and the two live side by side under near-identical names. |

There is a helper that encodes all of this: **`aggregation_rule`** tells you whether a series
sums, averages or takes the period end when you change frequency. Use it rather than deciding
per query.

---

## 5. Three worked examples

### Bank groups partition the sector — and they close

Housing loans, June 2026, from `t04#row2` across all ten scopes:

| Scope | milyar TL | Partition |
|---|---:|---|
| **Sektör** | **801.4** | the whole sector |
| Mevduat | 725.4 | by bank type |
| Katılım | 76.0 | " |
| Kalkınma ve Yatırım | 0.0 | " → **801.4** |
| Kamu | 358.0 | by ownership |
| Yerli Özel | 272.9 | " |
| Yabancı | 170.5 | " → **801.4** |

Both partitions sum back to Sektör exactly. That is not a coincidence — it is BDDK's own
arithmetic, and we check it across 83,808 comparisons on every build.

### Per-capita figures are ratios, not amounts

FinTürk table 6 publishes `Kişi Başı Nakdi Kredi` per province, in plain TL. It is marked
`ratio`, deliberately.

> **Do not sum per-capita lending across provinces.** Adding İstanbul's 573,643 TL to Van's
> 82,940 TL (June 2026) gives you a number that means nothing. To get a province total,
> multiply by that province's population — which the same table gives you as
> `Yurtiçi Şube Sayısı × Şubeye Düşen Nüfus`.

That identity is how we proved the unit is TL and not bin TL: reconstructing table 1 from it
matches the published figure across 1,782 province-quarters, worst deviation **0.019%**.

### Profit accumulates; balances don't

`Dönem Karı (Zararı)` is year-to-date — June's figure is the first six months, not June
alone. `Tüketici Kredileri - Konut` is a balance and does not accumulate. Both live in the
same monthly bulletin, and the only thing distinguishing them is `cumulative_mode`.

The classification is not a guess. Each row carries `cumulative_evidence` explaining how it
was decided:

```
none: january resets 0/5, within-year monotone 4/6, globally monotone False;
falls in some periods, so it is not accumulating || settled by pattern
```

Monotonicity alone is **not** evidence under Turkish inflation — a balance growing at 60% a
year rises every month without accumulating anything. That is why the rule is the accounting
statement kind first, pattern second.

---

## 6. What isn't there

Short list, stated plainly. If a question reaches into one of these, say so rather than
approximating.

### No gross *kullandırılan* anywhere

The demo question asks about new lending extended. **No source publishes it.** That is a
finding, not a failure to look: all 53,792 EVDS series were searched. Six match the word, and
none is what is meant — five are the central bank's own lending to TMSF and to banks, mostly
archived, and the sixth family uses *kullandırımlar dahil* as a scope qualifier on an
interest rate. BDDK's three publications are balances throughout.

**But a net flow does exist, and we hold it.** TCMB's financial accounts publish household
loans on a transactions basis:

| | |
|---|---|
| `TP.FINHESTNKS61014.ZP34` | F.4 Krediler, Hanehalkı (Konsolide Akım) |
| frequency | quarterly · bin TL · 2010-Q4 onward |
| what it is | net incurrence of loan liabilities — new lending **minus** repayments |

It beats differencing a BDDK balance, because a financial-accounts transaction excludes
revaluation and reclassification — which a stock difference silently includes.

> **Label it net, never gross.** And do not substitute the asset-side twin `ZP12`: that is
> what households *lend*, which oscillates around zero, against 219–643 bn TL per quarter of
> borrowing on the liability side.

### Out of EVDS scope, on purpose

- International statistics, balance of payments, and the CBRT's own balance sheet — none of
  this project's questions reach them.
- EVDS keeps **22,010** series under an `ARŞİV` topic: superseded publications it still
  serves. Excluded.
- 164 of 678 datagroups publish no unit at all. A series whose unit we cannot state is one we
  will not serve, so those were never ingested.

### Weekly bulletin, narrower than monthly

Weekly is TL only, and its own `taraf` dimension is unfetched — so bank-group questions have
to go to the monthly bulletin.

---

## 7. Verify it yourself

Three checks, all comparing the data against *itself* rather than against what we expected to
find. Each exits non-zero on breach.

```bash
.venv/bin/python scripts/check_taraf_partitions.py
.venv/bin/python scripts/check_finturk_units.py
.venv/bin/python scripts/reconcile_bddk_evds.py
```

| Check | Scope | Result |
|---|---:|---|
| Bank-group partitions | 83,808 comparisons | 2 of 3 partitions exact; worst material gap 0.257% |
| FinTürk units | 1,782 province-quarters | worst deviation 0.019% |
| BDDK ↔ EVDS | cross-source | three stable offsets, ratio std < 0.005 |

The first is the strongest, because it needs no second source and no assumption. BDDK's ten
bank-group scopes form three partitions of the sector, and the publisher's own arithmetic has
to hold:

```
Sektör  = Mevduat + Katılım + Kalkınma ve Yatırım
Sektör  = Yerli Özel + Kamu + Yabancı
Mevduat = Mevduat-Yerli Özel + Mevduat-Kamu + Mevduat-Yabancı
```

It has already earned its keep twice — catching the catalog silently collapsing all ten
scopes into one series, and then four rows filed inside balance-sheet tables that turned out
to be ratios. A ratio does not add across bank groups, so it stands out sharply against an
identity every genuine balance line satisfies.

**Run these after any rebuild.** They are fast, and they are the difference between believing
the pipeline works and knowing it does.

---

Snapshot: `kkb-data-snapshot-2026-09-14-91d751a.tgz` · 75,331,709 bytes · built from commit
`91d751a`.

Full detail in `data-coverage.md`, conventions in `DECISIONS.md`, and the Turkish summary
that ships inside the archive at `data/SNAPSHOT.md`.
