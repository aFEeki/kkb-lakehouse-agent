# Teknoloji Yığını

Projede kullanılan tüm teknolojiler. Sürüm alt sınırları için [`pyproject.toml`](pyproject.toml).

Üç kural bu listenin tamamını belirler:

1. **Arka uç ve agentic katman Python.** Yarışma kuralı gereği, özel bir gereklilik olmadıkça
   ek bir dilde geliştirme yapılmaz. Arayüz bu kuralın dışındadır.
2. **Yalnızca açık kaynak kütüphaneler.** Tabloların lisans sütunu bunun içindir.
3. **Çalışma zamanında yalnızca Kloudeks.** Hiçbir üçüncü parti LLM servisi kullanılmaz.

---

## Dil ve çalışma zamanı

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| Python 3.11+ | Arka uç, agentic katman, tüm veri işleme | PSF |
| TypeScript | Yalnızca arayüz | Apache-2.0 |
| SQL (DuckDB lehçesi) | Lakehouse sorguları | — |

---

## Yapay zekâ ve çıkarım — Kloudeks MIA

Tek çıkarım sağlayıcısıdır. Uç nokta OpenAI uyumludur: `https://mia.csp.kloudeks.com/v1`

| Model | Kullanım | Sınır |
|---|---|---|
| `kkbhackathon2026/Qwen3.8-27B` | Soru anlama, planlama, anlatı üretimi, görsel yorumlama | Prompt başına en fazla 5 görsel |
| `kkbhackathon2026/Qwen3-Embedding-8B` | Seri metaverisi üzerinde anlamsal arama vektörleri | — |
| `kkbhackathon2026/Unlimited-OCR` | Belge görselinden metin çıkarma | Prompt başına en fazla 3 görsel |

| Kütüphane | Kullanım | Lisans |
|---|---|---|
| `openai` (Python SDK) | MIA istemcisi — uç nokta OpenAI uyumlu olduğu için standart istemci kullanılır | Apache-2.0 |

API anahtarı yalnızca sunucu tarafında, ortam değişkeninde tutulur. Arayüz koduna hiçbir
koşulda girmez.

---

## Veri depolama

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| **DuckDB** | Analitik veri deposu ve lakehouse sorgu motoru | MIT |
| **LanceDB** | Seri metaverisi için vektör deposu | Apache-2.0 |
| Apache Parquet (PyArrow) | Silver ve gold katman dosya formatı | Apache-2.0 |
| Yerel dosya sistemi | Bronze katman — ham kaynak dosyaların SHA-256 özetiyle arşivi | — |

Katman yapısı: `bronze` (ham, dokunulmamış) → `silver` (ayrıştırılmış, kaynak birimlerinde)
→ `gold` (hizalanmış, kümülatiften arındırılmış, birleştirilebilir).

---

## Veri işleme

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| Pandas | Veri çerçeveleri, zaman serisi hizalama | BSD-3-Clause |
| NumPy | Sayısal işlemler | BSD-3-Clause |
| PyArrow | Parquet okuma/yazma, DuckDB birlikte çalışması | Apache-2.0 |

---

## Veri temini ve belge ayrıştırma

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| httpx | HTTP istemcisi — EVDS API, BDDK dosyaları, canlı URL'ler | BSD-3-Clause |
| openpyxl | `.xlsx` bültenler | MIT |
| xlrd | Eski `.xls` dosyalar — BDDK bazı dosyaları hâlâ bu formatta yayımlıyor | BSD |
| BeautifulSoup4 | HTML ayrıştırma, sayfadaki hedef belge bağlantısının bulunması | MIT |
| lxml | HTML/XML arka ucu | BSD |
| pypdf | Metin katmanı olan PDF'lerden doğrudan çıkarım | BSD-3-Clause |
| pypdfium2 | PDF sayfalarının PNG'ye dönüştürülmesi — OCR öncesi adım | Apache-2.0 / BSD-3-Clause |
| Playwright | JavaScript ile üretilen sayfalar (headless Chromium) | Apache-2.0 |

OCR sırası önemlidir: önce metin çıkarımı denenir, başarısız olursa sayfa görsele
dönüştürülüp Unlimited-OCR'a gönderilir. OCR varsayılan değil, geri düşüş yoludur.

---

## Analiz

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| statsmodels | ADF ve KPSS durağanlık testleri, eşbütünleşme, Granger / Toda-Yamamoto, STL ayrıştırma | BSD-3-Clause |
| ruptures | Kırılma noktası tespiti (PELT, ikili bölümleme) | BSD-2-Clause |
| SciPy | İstatistiksel testler, dayanıklı z-skorları | BSD-3-Clause |

---

## Çıktı ve görselleştirme

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| Plotly (Python) | Grafik tanımının sunucu tarafında üretilmesi | MIT |
| plotly.js | Grafiğin tarayıcıda çizilmesi | MIT |

Eksen ataması sütun birimlerinden deterministik olarak türetilir; modelin serbest seçimine
bırakılmaz.

---

## API ve arka uç

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| FastAPI | HTTP API | MIT |
| Uvicorn | ASGI sunucusu | BSD-3-Clause |
| Pydantic | Şema doğrulama — özellikle planlayıcı çıktısının işlem sözlüğüne uygunluğu | MIT |
| Server-Sent Events | Aşama izinin arayüze akıtılması | — |
| python-dotenv | Ortam değişkeni yönetimi | BSD-3-Clause |

---

## Arayüz

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| React 19 | Bileşen katmanı | MIT |
| Next.js | Uygulama çatısı | MIT |
| TypeScript | Tip güvenliği | Apache-2.0 |
| Tailwind CSS | Stil | MIT |
| react-plotly.js | Grafik bileşeni | MIT |

Arayüz MIA'ya doğrudan istek atmaz. Zincir her zaman: tarayıcı → FastAPI → MIA.

---

## Web araması

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| SearxNG (kendi sunucumuzda) | Web Search aracı | AGPL-3.0 |

Ticari bir arama API'sinin kural kapsamına girip girmediği KKB'ye soruldu. Yanıt gelene kadar
kendi barındırdığımız çözüm varsayılandır; böylece gelecek yanıt yapılmış işi geçersiz kılamaz.

---

## Geliştirme araçları

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| uv | Bağımlılık ve sanal ortam yönetimi | MIT / Apache-2.0 |
| pytest | Testler ve veri doğrulama kuralları | MIT |
| ruff | Lint ve biçimlendirme | MIT |
| Git / GitHub (private) | Sürüm kontrolü | — |

---

## Dağıtım

| Teknoloji | Kullanım | Lisans |
|---|---|---|
| Docker / Docker Compose | Paketleme ve SearxNG'nin çalıştırılması | Apache-2.0 |
| Barındırma | **Karara bağlanmadı** — Kloudeks üzerinde uygulama barındırma imkânı KKB'ye soruldu. Yanıt gelene kadar kendi sunucumuz varsayılan | — |

Veri gölü tamamen yerel dosyalardan oluşur (DuckDB + Parquet). Uygulama nereye taşınırsa
veri de onunla taşınır; depolama tasarımı barındırma kararına bağlı değildir.

---

## Değerlendirilip kapsam dışı bırakılanlar

| Teknoloji | Gerekçe |
|---|---|
| **Neo4j** | Köken kaydı graf yapısında; ancak analiz nesnesi başına birkaç düzine düğüm söz konusu. DuckDB içinde JSON olarak saklamak yeterli. Ayrı bir servisin kurulması, işletilmesi ve demo günü ayakta tutulması, sağladığı faydadan büyük bir maliyet. Gün 5'ten sonra, çalışan bir sisteme *ek* olarak yeniden değerlendirilebilir — güven katmanının bağımlı olduğu bir bileşen olarak değil. Bkz. [DECISIONS.md](DECISIONS.md) #16 |
| Ticari arama API'leri | Kural kapsamı netleşene kadar kullanılmıyor |
| Tesseract OCR | Gerek kalmadı — Unlimited-OCR aynı işi Türkçe desteğiyle ve ek sistem bağımlılığı olmadan yapıyor |

---

## Lisans notu

Yukarıdaki lisanslar yaygın olarak bilinen değerlerdir; teslim öncesi `uv pip list` çıktısı
üzerinden doğrulanacaktır. Çalışma zamanında kullanılan tüm kütüphaneler açık kaynaktır.
