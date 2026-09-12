# Data coverage report

What is in the data pool, what is not, and why. Updated as sources land.

Feeds SCRUM-87. Incomplete while acquisition is in progress — sections marked **not started**
are gaps, not omissions.

---

## Summary

| Source | Status | Coverage |
|---|---|---|
| BDDK Aylık Bülten | **acquired** | 2021-01 → 2026-06, 17 tables, Sektör only |
| BDDK Haftalık Bülten | **out of scope for v1** | see below |
| BDDK FinTürk | **out of scope for v1** | see below |
| TCMB EVDS | **not started** | blocked on API key |
| Live URLs | **not started** | on-demand, no pre-acquisition |

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

## BDDK Haftalık Bülten and FinTürk — out of scope for v1

Recorded decision (DECISIONS.md #3): monthly only for the first pass. Weekly holds most of
the parsing volume and the published demo scenario needs none of it. FinTürk is province-level
and quarterly by nature.

The brief names all three. This gap is a scope decision, not an oversight, and should be
presented as such.

---

## TCMB EVDS — not started

**Blocked on an API key.** `EVDS_API_KEY` is unset; the API returns a redirect to login
without one.

This is the larger gap of the two, because EVDS carries series the published scenario
depends on and BDDK does not provide:

- Housing loan **interest rates** (turn 1)
- **TÜFE** for deflation (turn 2)
- **House price index** (turn 3)

It is also where a housing loan **flow** series would live. BDDK table 4 gives the balance
outstanding, which is not the same measure the scenario asks for.

---

## Live URLs — not started

Read on demand rather than pre-acquired, by design. See the Agent Tools epic.
