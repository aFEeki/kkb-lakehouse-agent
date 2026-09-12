# KKB Lakehouse Agent

Türkiye bankacılık ve makroekonomi verileri üzerinde çalışan agentic analiz sistemi.
Türkçe sorulan bir soruyu anlar, analizi planlar, uygun araçları kendisi seçer, hesaplamayı
yürütür, sonucu doğrular ve ürettiği her sayının kaynağını gösterir.

**KKB Hackathon 2026** — Lakehouse Agent Builder & Data Analytics
**Ekip:** Fellas in Istanbul

---

## Sistem ne yapıyor?

BDDK bültenleri ve TCMB EVDS'ten oluşturulan veri havuzu üzerinde analitik soruları
yanıtlar; kapsam Ocak 2021 – Haziran 2026'dır. Gerektiğinde canlı kaynaklarla zenginleştirir.

Aşağıdaki örnek, organizasyonun paylaştığı senaryodur ve sistemin birincil kabul testi
olarak ele alınmıştır:

| Tur | Soru | Sistemin yaptığı |
|---|---|---|
| 1 | 2021–2025 arasında kullandırılan konut kredilerinin aylık dağılımı ve konut kredisi faiz oranları. Faizlerin düştüğü dönemlerde kredi hacmi nasıl değişti? | İki seriyi çözümler, ortak bir aylık omurgaya hizalar, grafikleştirir ve soruyu hesaplanmış kanıta dayanarak yanıtlar |
| 2 | *Tabloyu bozmadan*, sadece konut kredisi tutarlarını enflasyondan arındırabilir misin? | Bir fiyat endeksi getirir, yalnızca ilgili sütuna uygular; satırlara ve diğer sütunlara dokunmaz |
| 3 | *Bu tabloyu hiç bozmadan*, konut fiyat endeksini yeni sütun olarak ekle. Kredilerin artmamasının nedeni fiyat artışları olabilir mi? | Dördüncü seriyi aynı satırlara ekler ve önceki turdaki kendi bulgusunu yeniden değerlendirir |

Sistem, bir sohbet boyunca tek bir canlı analiz nesnesi taşır ve onu yerinde değiştirir. Her
turda sonucu sıfırdan yeniden üretmez — bkz. [Tasarım kararları](#tasarım-kararları).

---

## Mimari

Şartnamede tanımlanan beş aşama ve hepsinin altında çalışan güven katmanı.

| Aşama | Uygulama | Modül |
|---|---|---|
| **1 · Veri Keşfi & Temini** | BDDK bültenleri ve EVDS serilerinin taranması ve arşivlenmesi. Ham veri, ayrıştırılmadan önce SHA-256 özeti ve çekim zaman damgasıyla saklanır | `ingest/`, `data/bronze/` |
| **2 · Veri Temizliği & Hizalama** | Kalite ve eksik veri kontrolleri, kümülatif ayrıştırma, birim normalizasyonu, frekans hizalama. Çıktı, her serinin her seriyle birleştirilebildiği tek bir havuzdur | `transform/`, `catalog/`, `data/gold/` |
| **3 · Agentic Analytics Motoru** | Türkçe soru anlama, anlamsal ve sözcüksel aramanın birlikte kullanıldığı seri çözümleme, araç seçimi, analiz nesnesi üzerinde çok adımlı yürütme | `agent/`, `tools/lakehouse.py` |
| **4 · Verinin Analiz Edilmesi** | Anomali tespiti, kırılma noktası tespiti ve kırılmaları dikkate alan nedensellik testleri | `tools/anomaly.py`, `tools/change_detection.py`, `tools/causality.py` |
| **5 · Doğrulama & Sonuç** | Web araması ve doğrudan URL okuma ile teyit; çıktının grafik, tablo veya rapor olarak sunulması | `tools/web_search.py`, `tools/web_url.py`, `api/` |

### Güven Katmanı

İzlenebilirlik, yanıtın sonuna eklenen bir not değil, veri yapısının bir özelliğidir. Her
sonuçtaki her sütun bir **köken kaydı** taşır: kaynak türü, tam kaynak referansı (EVDS seri
kodu ya da çalışma kitabı, sayfa ve hücre aralığı), çekim zamanı, ayrıştırıldığı ham verinin
SHA-256 özeti ve uygulanan dönüşümlerin sıralı zinciri. Türetilmiş sütunlar, kendilerini
oluşturan sütunların köken kayıtlarını da özyinelemeli olarak taşır.

Arayüz bu bilgiyi sütun bazında gösterir. "Bu sayı nereden geliyor?" sorusu, sayfanın altındaki
bir kaynak listesine bakılarak değil, sütuna tıklanarak yanıtlanır.

Doğruluk kontrolü, veri derlemesini kapıda tutan bir **doğrulama kuralları** kümesiyle
sağlanır. Başarısız bir kural uyarı üretmez, derlemeyi durdurur. Kurallar
[`docs/invariants.md`](docs/) içinde belgelenmiştir.

---

## Ajan Araçları

| Araç | Yetenek |
|---|---|
| **Lakehouse** | Hazırlanan veri havuzu üzerinde doğal dille keşif, sorgulama ve birleştirme |
| **Web Search** | Dış kaynaklardan araştırma ve teyit |
| **Web URL Agent** | Verilen bir URL'yi okuyup anlam çıkarır — PDF, Excel, görsel ve metin; sayfanın kendisinde değil, sayfadan bağlantılanan belgelerde duran veriler dâhil |
| **Anomaly** | Olağandışı hareketler, aykırı değerler ve beklenen davranıştan sapmalar |
| **Causality** | Gözlenen ilişkinin gerçekten neden-sonuç mu, yoksa yalnızca korelasyon mu olduğu |
| **Change Detection** | Zaman serisinde seviye, eğilim ve davranış değişiklikleri |

Planlayıcı her soruyu ihtiyaç duyduğu araçlara yönlendirir ve yanıtta bu araçlara atıf yapar.
Araç seçimi gizli değildir; yürütme izinde görünür.

---

## Tasarım kararları

Diğer her şeyi belirleyen iki karar var ve ikisi de bilinçli.

### Analiz nesnesinin tarih omurgası değiştirilemez

Bir sorunun sonucu, kalıcı ve sürümlenen bir nesnedir: sabit bir tarih omurgası, her biri
kendi köken kaydını taşıyan tipli sütunlar, bulgular ve bir grafik tanımı. Planlayıcı bu nesne
üzerinde kapalı bir işlem sözlüğünden operasyonlar üretir; nesneyi baştan oluşturmaz ve
aritmetik yapmaz.

Sütun ekleyen her işlem mevcut omurga üzerine sol birleştirme (left join) yapar ve satır
sayısının değişmediğini doğrular. Omurganın yeniden dilimlenmesi, kullanıcının açık onayını
gerektiren ayrı bir işlemdir. "Tabloyu bozmadan" ifadesi böylece sistemin ummakla yetindiği
bir davranış değil, güvence altına aldığı bir kural hâline gelir: iç birleştirmeye ya da
tarih aralığını değiştirmeye karar veren bir planlayıcı bunu sessizce yapamaz.

Aynı işlem günlüğü sisteme çalışan bir geri alma yeteneği kazandırır; hatalı bir adım, önceki
turlar kaybedilmeden geri alınabilir.

### Kümülatif veriler seri bazında sınıflandırılır ve bir kişi tarafından doğrulanır

BDDK bazı verileri yıl başından itibaren biriken, bazılarını yayın başlangıcından itibaren
biriken toplamlar olarak yayımlar; bunların yanında olağan dönem değerleri de yer alır. Üçü
bir grafikte birbirinden ayırt edilemez ve birinin yanlış sınıflandırılması, o seriden
türetilen bütün dönemsel değişim hesaplarını bozar.

Akla ilk gelen yöntem burada işe yaramaz. "Sürekli artıyorsa kümülatiftir" varsayımı, bu
dönemdeki TL serileri için neredeyse hiçbir bilgi taşımaz; enflasyon zirvede %85'e ulaştığı
için nominal değerler ne ölçerse ölçsün hemen her ay artmıştır. Ayırt edici olan **Ocak
kırılmasıdır**. Sınıflandırma bu teste dayanır, gerekçesiyle birlikte katalogda kayıt altına
alınır ve seri gold katmanına geçmeden önce bir kişi tarafından onaylanır. Doğrulanamayan
seri yayımlanmaz, kapsam dışı bırakılır.

Her seri ayrıca açık bir toplulaştırma kuralı taşır; çünkü kümülatif ayrıştırma ile frekans
dönüşümü birbirine bağlıdır: ayrıştırılmış haftalık bir akım aya toplanarak, bir stok ise
dönem sonu değeri alınarak dönüştürülür.

---

## Veri kaynakları

| Kaynak | Kapsam | Not |
|---|---|---|
| **BDDK** — Haftalık Bülten, Aylık Bülten, FinTürk | 2021-01 → 2026-06 | Excel bültenler; çok satırlı başlıklar, karışık birimler, kümülatif ve kümülatif olmayan seriler |
| **TCMB EVDS** | 2021-01 → 2026-06 | Günlük, haftalık ve aylık seriler; dokümante API üzerinden |
| **Canlı URL'ler** | Talep üzerine | Sorgu anında verilir ve doğrudan okunur |

Seriler, sonuçların yeniden üretilebilmesi için tarihli bir anlık görüntüye sabitlenir. Her iki
kaynak da geçmiş verilerde revizyon yaptığından, anlık görüntü tarihi arayüzde gösterilir.

---

## Teknoloji

Arka uç ve agentic katmanın tamamı, yarışma kurallarına uygun olarak Python ile geliştirilmiştir.
Yalnızca açık kaynak kütüphaneler kullanılmıştır.

| Alan | Tercih |
|---|---|
| Çıkarım | Kloudeks MIA — `Qwen3.8-27B` (akıl yürütme, görsel), `Qwen3-Embedding-8B` (arama), `Unlimited-OCR` (belge) |
| Analitik veri deposu | DuckDB |
| Vektör deposu | LanceDB |
| Veri işleme | Pandas |
| Belgeler | pypdf, pypdfium2, openpyxl |
| İstatistik | statsmodels, ruptures, SciPy |
| Grafik | Plotly |
| API | FastAPI |
| Arayüz | Next.js |

Çalışma zamanında hiçbir üçüncü parti LLM servisi kullanılmaz. Tüm çıkarım Kloudeks platformu
üzerinden yapılır ve API anahtarı yalnızca sunucu tarafında tutulur; arayüz model uç noktasına
değil, kendi API'mize istek atar.

Kullanılan tüm teknolojiler, kullanım amaçları ve lisanslarıyla birlikte
[STACK.md](STACK.md) içinde listelenmiştir (İngilizce).

---

## Depo yapısı

```
src/kkb_agent/
  llm/          Kloudeks MIA istemcisi — tek çıkarım sağlayıcısı
  frame/        Analiz nesnesi: omurga, sütunlar, köken kaydı, işlemler
  ingest/       BDDK ve EVDS verilerinin bronze katmana alınması
  catalog/      Seri metaverisi — birim, kümülatif mod, toplulaştırma kuralı, semantik
  transform/    Kümülatif ayrıştırma, birim normalizasyonu, frekans hizalama
  tools/        Altı ajan aracı
  agent/        Planlayıcı ve yürütme döngüsü
  api/          FastAPI arka ucu

web/            Next.js arayüzü
tests/          Veri derlemesini kapıda tutan doğrulama kuralları
scripts/        Veri alma işleri ve platform yetenek testleri
eval/           Regresyon için sabitlenmiş Türkçe soru kümesi
docs/           Mimari, veritabanı tanımları, model yetenek raporu
data/           Yerel veri gölü (git dışı) — bronze → silver → gold
```

---

## Çalıştırma

**Mevcut durum:** Yerel geliştirme temeli, `/health` API'si ve sağlık ekranı hazırdır.
Kurulum ve çalışan komutlar için [yerel geliştirme rehberine](docs/local-development.md)
bakın. Aşağıdaki lakehouse oluşturma akışı hedef kullanımdır; veri derleme script'i
ve invariant testleri henüz uygulanmamıştır.

```bash
cp .env.example .env          # MIA_API_KEY, EVDS_API_KEY
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

python scripts/build_lakehouse.py    # temin → ayrıştırma → hizalama → doğrulama
pytest -m invariant                  # veri doğruluğu kapısı
uvicorn kkb_agent.api.main:app --reload
```

Canlı ortam: *(adres eklenecek)*

---

## Durum

20 Eylül teslimine yönelik geliştirme sürüyor. Yol haritası için [PLAN.md](PLAN.md).
Bu bölüm, bileşenler tamamlandıkça güncellenir.

---

## Ekip

Fellas in Istanbul — *(üyeler eklenecek)*

## Depo kuralları

Yerel ayarlar, gizli anahtarlar ve veri gölü git dışındadır; her commit öncesi `git status`
kontrol edilmelidir. MIA anahtarı yalnızca ortam değişkeninde tutulur ve hiçbir koşulda
arayüz koduna, bir commit'e veya ekran görüntüsüne girmemelidir.
