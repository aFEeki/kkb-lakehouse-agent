# Database definitions

Every table, column and controlled vocabulary in the system, and why each exists.

Checked against the published snapshot `data-2026-09-15` (catalog fingerprint
`325ca5b2cfd660b5`). The figures quoted are from that snapshot, not estimates.

---

## 1. Layers

| Layer | Location | Contents | In git? |
|---|---|---|---|
| **bronze** | `data/bronze/bddk/` | Source responses exactly as fetched — BDDK Aylık and FinTürk JSON, Haftalık HTML — plus a JSONL manifest per acquisition recording each file's sha256 and fetch time | no, distributed as a release asset |
| **silver** | `data/silver/evds/` | EVDS series as parquet, one file per acquisition | no, same |
| **gold** | `data/gold/lakehouse.duckdb` | The two tables below | no, same |

Bronze is the pinned input; gold is derived from it. `scripts/build_catalog.py` rebuilds
gold from bronze with no network access, so the same bronze always produces the same
catalog. That property is what lets the snapshot be shared and everyone arrive at
identical numbers.

**Totals:** 47,015 series, 1,330,275 observations, covering 2021-01-01 to 2026-06-30.

| Source | Series | What it is |
|---|---|---|
| `bddk_finturk` | 41,522 | BDDK FinTürk — İllere Göre. Quarterly, per province |
| `bddk_aylik` | 4,960 | BDDK Aylık Bülten. Monthly, per bank-group scope |
| `bddk_haftalik` | 283 | BDDK Haftalık Bülten. Weekly |
| `evds` | 250 | TCMB EVDS |

---

## 2. `series_catalog`

One row per series: what it is, what unit it is in, whether it accumulates, how it may be
aggregated, and where it came from. Without it, answering "show me housing loan interest
rates" would mean opening 4,255 files and guessing.

```sql
CREATE TABLE series_catalog (
    series_id              VARCHAR PRIMARY KEY,
    source                 VARCHAR NOT NULL,
    source_ref             VARCHAR NOT NULL,
    name_tr                VARCHAR NOT NULL,
    raw_label              VARCHAR,

    measure_type           VARCHAR NOT NULL,
    statement_kind         VARCHAR,
    sector_scope           VARCHAR,
    currency_basis         VARCHAR,
    province               VARCHAR,          -- NULL means national

    unit_raw               VARCHAR,
    unit_normalized        VARCHAR,
    scale_factor           DOUBLE DEFAULT 1.0,

    cumulative_mode        VARCHAR NOT NULL,
    cumulative_evidence    VARCHAR,
    cumulative_verified_by VARCHAR,
    native_freq            VARCHAR NOT NULL,
    aggregation_rule       VARCHAR NOT NULL,

    coverage_start         DATE,
    coverage_end           DATE,
    observations           INTEGER DEFAULT 0,
    nonzero_observations   INTEGER DEFAULT 0,
    retrieved_at           TIMESTAMP,
    source_hash            VARCHAR,
    notes                  VARCHAR
);
```

### Identity

| Column | Type | Description |
|---|---|---|
| `series_id` | VARCHAR, PK | Stable identifier, `{source}.{table}.{scope}.{slug}`. BDDK row labels embed references to *other row numbers* (`Risk Ağırlıklı Kalemler Toplamı (10+27+28)`), and those numbers shift when a row is inserted above — so identity is a composite of source, table, scope and the *normalised* label, never the raw label or row order. One slug policy across all sources: Turkish-correct casefold, Turkish letters preserved. |
| `source` | VARCHAR, NOT NULL | One of `bddk_aylik`, `bddk_haftalik`, `bddk_finturk`, `evds`. |
| `source_ref` | VARCHAR, NOT NULL | Where in the source this came from: `t04#row2` for BDDK, the series code for EVDS. |
| `name_tr` | VARCHAR, NOT NULL | Display name in Turkish. |
| `raw_label` | VARCHAR | What the source called it at acquisition time, preserved verbatim through renames. Normalisation is recorded, never applied invisibly. |

### Semantics — what the number actually *is*

| Column | Type | Description |
|---|---|---|
| `measure_type` | VARCHAR, NOT NULL | `stock` \| `flow` \| `rate` \| `index` \| `ratio` \| `count` \| `unknown`. Governs which operations are legal. **A housing loan *balance* is not housing lending *extended*** — confusing them is the failure a domain reviewer catches first. `unknown` is never guessed; such series are refused rather than served. |
| `statement_kind` | VARCHAR, NULL | `balance_sheet` \| `income_statement` \| `off_balance_sheet` \| `ratio`. Which financial statement the source table represents. This is the *source definition* that settles cumulative mode when the data pattern cannot. NULL for EVDS. |
| `sector_scope` | VARCHAR | Bank-group scope (BDDK *taraf*): `Sektör`, `Mevduat`, `Katılım`, `Kalkınma ve Yatırım`, `Yerli Özel`, `Kamu`, `Yabancı`, and three `Mevduat-*` splits. One canonical spelling per scope — BDDK writes them in title case monthly and upper case in FinTürk, which once put 17 distinct values in a column holding nine scopes. |
| `currency_basis` | VARCHAR | `TP` (lira) \| `YP` (foreign currency) \| `Toplam`. |
| `province` | VARCHAR, NULL | **NULL means national.** 41,522 series, all FinTürk, across 82 values: Turkey's 81 provinces plus `YURT DIŞI` for foreign branches, which FinTürk publishes as a peer of the provinces. National and İstanbul are different series and must never be merged, and a filter meaning "every province" should exclude `YURT DIŞI`. |

**Distribution:** stock 34,442 · ratio 11,287 · count 618 · flow 600 · rate 62 · index 6.

### Units

| Column | Type | Description |
|---|---|---|
| `unit_raw` | VARCHAR | The unit string exactly as published (`Bin TL`, `Milyon TL`, `Yüzde`, `Adet`). |
| `unit_normalized` | VARCHAR | Canonical unit: `TRY`, `%`, `adet`, `kişi`, `gün`, `USD`, `endeks`. Empty means unresolved — such a series is **not servable**. |
| `scale_factor` | DOUBLE | Multiplier to reach the normalised unit. `Bin TL` → 1,000; `Milyon TL` → 1,000,000. |

Table `t05` is in *bin TL* where `t01`–`t04` are *milyon TL*. Mixing them is a silent
1000× error, invisible on a log axis. Units that name two possibilities — `"Yüzde, TL"`,
`"bin TL veya yüzde"` — are deliberately **absent** from the mapping: they stay unservable
rather than being resolved by a coin flip.

**Distribution:** TRY 37,186 · % 8,655 · adet 618 · kişi 518 · gün 20 · USD 12 · endeks 6.
Zero series are unservable for want of a unit.

### Accumulation and frequency

| Column | Type | Description |
|---|---|---|
| `cumulative_mode` | VARCHAR, NOT NULL | `none` \| `ytd` \| `inception` \| `ambiguous`. Whether the published figure accumulates. 599 series are `ytd`; the rest `none`. A month-over-month change taken from a cumulative series without de-cumulating is wrong by an order of magnitude — and plausibly so. |
| `cumulative_evidence` | VARCHAR | Why it was classified that way, in readable form: `"none: january resets 1/5, within-year monotone 3/6, globally monotone False; falls in some periods…"`. Populated for 100% of series so a reviewer can disagree. |
| `cumulative_verified_by` | VARCHAR | *What settled it*, where evidence records what was observed: `pattern` (the data was decisive, 4,411), `statement` (the accounting settled an ambiguous pattern, 42,354), `source-definition` (EVDS publishes levels and rates, 250). **Empty means nothing verified it** — such a series must not be served. Zero are empty today. |
| `native_freq` | VARCHAR, NOT NULL | `D` \| `W` \| `M` \| `Q` as published. Quarterly FinTürk must never be resampled into months it does not have. Q 41,641 · M 5,053 · W 319 · D 2. |
| `aggregation_rule` | VARCHAR, NOT NULL | `last` \| `sum` \| `mean` — how to collapse a higher frequency into a lower one. Coupled to `measure_type`: a de-cumulated flow sums, a stock takes period end, a rate averages. Using the wrong rule is as damaging as the wrong cumulative mode. last 46,353 · sum 600 · mean 62. |

### Coverage and provenance

| Column | Type | Description |
|---|---|---|
| `coverage_start` / `coverage_end` | DATE | First and last observed period. |
| `observations` | INTEGER | Number of periods held. |
| `nonzero_observations` | INTEGER | How many of those carry an actual figure. A row published as `0.0` every month is a real publication and a useless answer: **4,496 series are entirely zero**, and `observations` alone makes them look as well-covered as any other. Retrieval ranks them last rather than hiding them. |
| `retrieved_at` | TIMESTAMP | When the underlying source was fetched. Naive on purpose — both sources stamp UTC, and `TIMESTAMPTZ` would require `pytz`, a deprecated dependency added for one column. UTC is re-attached on read, where the assumption is stated rather than implied. |
| `source_hash` | VARCHAR | Digest of the bronze acquisition this series was built from — sha256 over the sorted per-file hashes in the manifest, so a re-crawl fetching identical bytes in a different order yields the same digest. Per source rather than per file, because a monthly series is assembled from 66 files. |
| `notes` | VARCHAR | Source category and datagroup, where the source provides them. |

### Indexes

```sql
CREATE INDEX idx_catalog_source   ON series_catalog(source);
CREATE INDEX idx_catalog_measure  ON series_catalog(measure_type);
CREATE INDEX idx_catalog_province ON series_catalog(province);
CREATE INDEX idx_obs_period       ON series_observations(period);
```

---

## 3. `series_observations`

Long format — one row per series per period — so sources at four different frequencies
coexist without a ragged wide table.

```sql
CREATE TABLE series_observations (
    series_id       VARCHAR NOT NULL,
    period          DATE NOT NULL,
    value           DOUBLE,
    value_reported  DOUBLE,
    PRIMARY KEY (series_id, period)
);
```

| Column | Type | Description |
|---|---|---|
| `series_id` | VARCHAR, NOT NULL | Foreign key to `series_catalog`. |
| `period` | DATE, NOT NULL | **Period start**, always. A month is `2021-03-01`, a quarter `2021-07-01`. |
| `value` | DOUBLE, NULL | This period's own figure. A year-to-date series is de-cumulated on the way in, so a join can never read six months of profit as one month's. **NULL means not observed — never zero-filled.** |
| `value_reported` | DOUBLE, NULL | The figure as the source published it. Equal to `value` unless the series accumulates. Kept for the year-end closure check and for provenance. |

Keeping both columns is what makes the de-cumulation auditable: the transform is
reversible and checkable against the publisher's own year-end total.

---

## 4. Controlled vocabularies

| Enum | Values |
|---|---|
| `Source` | `bddk_aylik`, `bddk_haftalik`, `bddk_finturk`, `evds` |
| `MeasureType` | `stock`, `flow`, `rate`, `index`, `ratio`, `count`, `unknown` |
| `Frequency` | `D`, `W`, `M`, `Q` |
| `AggregationRule` | `last`, `sum`, `mean` |
| `CumulativeMode` | `none`, `ytd`, `inception`, `ambiguous` |
| `StatementKind` | `balance_sheet`, `income_statement`, `off_balance_sheet`, `ratio` |
| `OperationType` | `add_column`, `deflate_column`, `index_column`, `revert_to` |

### Unit normalisation

| Published | Normalised | Scale |
|---|---|---|
| `TL` / `Türk Lirası` | TRY | 1 |
| `Bin TL` | TRY | 1,000 |
| `Milyon TL` | TRY | 1,000,000 |
| `Milyar TL` | TRY | 1,000,000,000 |
| `ABD Doları` (and Bin/Milyon/Milyar) | USD | 1 / 10³ / 10⁶ / 10⁹ |
| `%` / `Yüzde` / `Ağırlıklı Ortalama` | % | 1 |
| `Adet` | adet | 1 |
| `Kişi` / `Bin Kişi` | kişi | 1 / 1,000 |
| `Gün` | gün | 1 |
| `Endeks`, `2003=100`, `2010=100`, `2020=100`, `2021=100` | endeks | 1 |

---

## 5. Enforced invariants

The build runs these after writing and exits non-zero on breach; they also run in CI on
every push against a committed 1.8 MB slice of real bronze. Each compares the data against
*its own publisher's arithmetic*, not against what we expected.

| Invariant | What it proves | Coverage |
|---|---|---|
| **I1** de-cumulation closes | The de-cumulated months of a year sum back to that year's published December figure | 2,875 series-years |
| **I2** sign | A `count` is never negative. Deliberately narrow: 161 flow, 29 stock, 9 ratio and 8 rate series go negative *legitimately* (net positions, retained losses, released provisions), so a universal rule would fire on correct data | 618 count series |
| **I3** classification | Every year-to-date series carrying data shows the January reset (579/579); disagreements between the data pattern and the accounting are reported against a recorded baseline of 129 | 4,960 monthly series |
| **I4** FinTürk units | Table 6's per-capita columns reconstruct table 1's published loans, proving those columns are TL and not Bin TL — a 1000× question | 1,782 province-quarters, worst drift 0.019% |
| **I6** bank-group partitions | BDDK's ten scopes form three partitions that must each sum back to their parent | 89,619 comparisons |
| **I7** no silent fill | NULL is never interpolated or forward-filled without an explicit, recorded operation | every column |
| **I8** freshness | `coverage_end` is consistent with the source's publication lag | every series |

Each check also fails when it compares *nothing* — a scope-collapsing bug leaves every
partition with a missing child, and "OK, 0 comparisons" would otherwise read as a pass.

---

## 6. The analysis frame

The runtime object an answer is built in. Versioned, append-only, and carrying its own
provenance — a number in the table can always be traced to the file it came from.

### `AnalysisFrame`

| Field | Type | Description |
|---|---|---|
| `frame_id` | Identifier | Stable across versions |
| `version` | int | Increments on every operation |
| `spine` | `Spine` | The shared row axis |
| `columns` | tuple[`Column`] | |
| `findings` | tuple[`Finding`] | |
| `charts` | tuple[`ChartSpec`] | |
| `operations` | tuple[`Operation`] | Append-only history |

### `Spine`

| Field | Type | Description |
|---|---|---|
| `key` | Identifier | Default `time` |
| `kind` | `date` \| `datetime` | |
| `label` | str \| None | Display label |
| `values` | tuple[date] | **Immutable once set.** Adding a shorter series must never truncate it; a computation needing complete rows narrows its own window and states it |

### `Column`

| Field | Type | Description |
|---|---|---|
| `key` | Identifier | Unique within the frame |
| `label` | Identifier | Display name |
| `dtype` | `integer` \| `number` \| `string` \| `boolean` | |
| `values` | tuple[Scalar] | Same length as the spine; NULL where unobserved |
| `measure_type` | Identifier \| None | Carried from the catalog |
| `unit` | `Unit` \| None | |
| `origin` | `source` \| `derived` | Read from the lakehouse, or computed from other columns |
| `lineage` | `Lineage` | Mandatory |

### Lineage structures

| Type | Fields | Purpose |
|---|---|---|
| `Unit` | `symbol`, `scale`, `description` | Rendered in the column header, so a reader never has to assume |
| `SourceReference` | `source_type`, `reference`, `retrieved_at`, `raw_sha256`, `metadata` | Which series, from which acquisition, at which bytes |
| `Transformation` | `name`, `implementation_version`, `parameters` | A descriptive audit record. **Never evaluated as code** |
| `ParentLineage` | `frame_id`, `frame_version`, `column_key`, `lineage` | A derived column's inputs, recursively |
| `Lineage` | `sources`, `parents`, `transformations` | A source column has sources; a derived column has parents and the transformation that produced it |

### `Finding`

| Field | Type | Description |
|---|---|---|
| `finding_id` | Identifier | |
| `statement` | Identifier | The claim, in Turkish |
| `frame_version` | int | The version the evidence was computed in |
| `supporting_column_keys` | tuple, min 1 | **A finding with no evidence cannot be constructed** |
| `spine_range` | `SpineRange` \| None | Half-open row interval `[start, stop)` the claim applies to |
| `producing_tool` | Identifier | Which tool computed it |
| `status` | `active` \| `superseded` \| `withdrawn` | |
| `supersedes` | Identifier \| None | Revision keeps the old finding visible rather than deleting it |
| `confidence` | float 0–1 \| None | |
| `caveats` | tuple[str] | What must be disclosed alongside the claim |

### `ChartSpec`

`chart_id`, `chart_type` (`line` \| `bar` \| `scatter`), `spine_key`, `column_keys`,
`axis_policy` (`by_unit`), `axis_assignments`, `indexing_recommended`, `title`.

Axis assignment is derived from column units, not chosen by a model — plotting a
percentage and a lira balance on one axis makes one of them invisible.

---

## 7. Snapshot identity

A snapshot is either complete and published or not visible at all. The published one is
`data-2026-09-15`, built from commit `dc2f53d`.

To confirm you hold exactly that data:

```bash
.venv/bin/python -c "
import duckdb, hashlib
c = duckdb.connect('data/gold/lakehouse.duckdb', read_only=True)
rows = c.execute('select series_id, observations, nonzero_observations from series_catalog order by series_id').fetchall()
print(c.execute('select count(*) from series_catalog').fetchone()[0], 'series')
print(c.execute('select count(*) from series_observations').fetchone()[0], 'observations')
print('fingerprint:', hashlib.sha256(repr(rows).encode()).hexdigest()[:16])
"
```

Expected:

```
47015 series
1330275 observations
fingerprint: 325ca5b2cfd660b5
```

`scripts/build_catalog.py` reproduces it from the same bronze without network access.
`scripts/package_snapshot.py` builds the release archive and refuses to package a catalog
that cannot answer the published questions.
