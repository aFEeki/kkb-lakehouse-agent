# KKB Lakehouse Agent

Türkiye bankacılık ve makroekonomi verileri üzerinde çalışan agentic analiz sistemi.
Türkçe sorulan bir soruyu anlar, analizi planlar, uygun araçları kendisi seçer, hesaplamayı
yürütür, sonucu doğrular ve ürettiği her sayının kaynağını gösterir.

**KKB Hackathon 2026** — Lakehouse Agent Builder & Data Analytics
**Takım:** Fellas in Istanbul

---

## Çalıştırma

Gereken: Python 3.11+, Node 20+, Docker, [uv](https://docs.astral.sh/uv/).

### 1. Kurulum

```bash
git clone <repo> && cd kkb-lakehouse-agent
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
python -m playwright install chromium      # JS ile üretilen sayfaları okumak için
```

### 2. Anahtarlar

```bash
cp .env.example .env
```

`.env` içinde `MIA_API_KEY` doldurulmalıdır. Anahtar yoksa sistem çalışmaya devam eder:
model planlaması yerine yazılı plan, araç seçimi için de anahtar kelime kuralları devreye
girer. Yanıtta `Plan: script` ifadesi hangi yolun çalıştığını söyler.

### 3. Veri

Veri deposu git'e dahil değildir (~70 MB). Yayınlanan anlık görüntüyü indirin:

```bash
gh release download data-2026-09-15
tar xzf kkb-data-snapshot-*.tgz && rm kkb-data-snapshot-*.tgz
```

Doğrulama — beklenen çıktı `47015 / 1330275 / 325ca5b2cfd660b5`:

```bash
python -c "
import duckdb, hashlib
c = duckdb.connect('data/gold/lakehouse.duckdb', read_only=True)
rows = c.execute('select series_id, observations, nonzero_observations from series_catalog order by series_id').fetchall()
print(len(rows), c.execute('select count(*) from series_observations').fetchone()[0],
      hashlib.sha256(repr(rows).encode()).hexdigest()[:16])
"
```

Alternatif olarak bronze katmandan yeniden üretilebilir (~8 dakika, ağ gerektirmez):
`python scripts/build_catalog.py`

### 4. Web arama servisi

```bash
docker compose up -d searxng
docker ps
```

Çalışmıyorsa web arama aracı "kullanılamıyor" yanıtı verir; diğer araçlar etkilenmez.

### 5. Çalıştır

```bash
# terminal 1 — backend
uvicorn kkb_agent.api.main:app --reload --host 127.0.0.1 --port 8000

# terminal 2 — arayüz
cd web && npm ci
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

<http://localhost:3000> adresini açın ve soruyu yazın.

### Örnek sorular

Yayınlanan senaryo — sırayla, aynı sohbette:

```
Konut kredisi faizleri düştüğü halde kredi hacmi neden artmadı?
Tabloyu bozmadan, sadece konut kredisi tutarlarını enflasyondan arındırabilir misin?
Bu tabloyu hiç bozmadan, konut fiyat endeksini yeni sütun olarak ekle.
```

Serbest sorular ve araçlar:

```
Ticari kredilerin 2021-2025 seyrini göster            → lakehouse
Konut kredisi bakiyesinde aykırı değer var mı?        → anomali
Mevduatta yapısal kırılma nerede?                     → değişim tespiti
Konut kredisi ile konut fiyat endeksi arasında nedensellik var mı?  → nedensellik
https://www.borsaistanbul.com/dosyalar/kmtp/veriler/kmp_au.pdf oku  → URL ajanı
BDDK konut kredisi düzenlemeleri hakkında internette ara            → web arama
```

Verimizle ilgisi olmayan sorular yanıtlanmaz, reddedilir.

### Testler

```bash
pytest -q                 # 874 test
pytest -m invariant       # veri doğruluğu kapısı
ruff check src tests scripts
```

---

## Sistem ne yapıyor?

Soru → seri çözümleme → model planı → doğrulanmış işlem yürütme → hesaplanmış bulgular →
kaynaklı yanıt. İş bölümü sistemin tamamına hâkimdir:

| | karar verir |
|---|---|
| **Model** | *ne* yapılacağına — hangi seri, hangi araç, hangi işlem |
| **Kod** | bunun *geçerli olup olmadığına* — şema, sözlük, değişmezler |
| **Kod** | *aritmetiğe* — hiçbir sayı model tarafından üretilmez |

Beş aşama (`/ask` üzerinden SSE ile canlı yayınlanır): veri keşfi, veri hazırlığı, agentic
analitik, analiz, doğrulama.

**Araçlar:** Lakehouse, Web Search, Web URL Agent, Anomali, Causality, Change Detection.
Soruyu hangi aracın yanıtlayacağına model karar verir; eşleşme yoksa soru reddedilir.

**Güven katmanı:** her sütun kaynağına kadar izlenebilir, her sayı kod tarafından
hesaplanır, birimi veya birikim kipi çözülemeyen seri sunulmaz.

---

## Veri

| Kaynak | Seri | Kapsam |
|---|---|---|
| BDDK FinTürk — İllere Göre | 41.522 | 2021-03 → 2026-06, 82 il |
| BDDK Aylık Bülten | 4.960 | 2021-01 → 2026-06 |
| BDDK Haftalık Bülten | 283 | 2021-01 → 2026-06 |
| TCMB EVDS | 250 | 2021-01 → 2026-06 |

Toplam 47.015 seri, 1.330.275 gözlem. Anlık görüntü sabittir, böylece sonuçlar yeniden
üretilebilir. Sütun bazında ayrıntı: [docs/database-definitions.md](docs/database-definitions.md).

Derleme yedi değişmezle kapılıdır — de-kümülasyon kapanışı, işaret ve sınıflandırma
tutarlılığı, FinTürk birimleri, banka grubu bölüntüleri, sessiz doldurma yasağı, tazelik.
İhlalde yeni anlık görüntü yayınlanmaz. Hepsi CI'da her push'ta çalışır.

---

## Teknoloji

Python 3.11+, FastAPI, DuckDB, pandas, Plotly, pypdf, Playwright, pytest, ruff.
Arayüz Next.js. Dil modeli yalnızca MIA / Kloudeks üzerinden (`Qwen3`); üçüncü taraf LLM
API'si kullanılmaz.

Ayrıntılı geliştirme notları: [docs/local-development.md](docs/local-development.md).

---

## Depo kuralları

Yerel ayarlar, gizli anahtarlar ve veri gölü git dışındadır; her commit öncesi
`git status` kontrol edilir. MIA anahtarı yalnızca ortam değişkeninde tutulur ve hiçbir
koşulda arayüz koduna, bir commit'e veya ekran görüntüsüne girmez.
