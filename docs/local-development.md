# Yerel geliştirme

Bu aşama yalnızca uygulama temelidir. Ingestion, AnalysisFrame, ajan araçları ve
sohbet ekranı henüz uygulanmamıştır. Backend `src/kkb_agent/`, frontend `web/`,
backend birim testleri ise kaynak ağacını aynalayan `tests/kkb_agent/` altındadır.

## Gereksinimler ve kurulum

Python 3.11+, uv ve Node.js 20.9+ gerekir. Python paketlemesi mevcut setuptools
yapısını kullanır. Frontend paket yöneticisi npm'dir; `web/package-lock.json`
repoda tutulur.

Repo kökünde:

```bash
uv venv --python 3.13 --managed-python .venv
source .venv/bin/activate
uv pip install -e .
# Geliştirme ve test araçları:
uv pip install -e ".[dev]"
# İsteğe bağlı URL-agent geliştirme bağımlılığı ve tarayıcı:
uv pip install -e ".[browser]"
python -m playwright install chromium
```

Python 3.13 burada yerel kurulum tercihi; projenin minimumu 3.11 olarak korunur.
uv, gerekirse yönetilen Python'u indirir. Kurulum internet erişimi gerektirir.
Python bağımlılıkları mevcut sürüm alt sınırlarıyla çözülür; henüz Python kilit
dosyası yoktur.

İlk kurulumda, mevcut kişisel dosyaları koruyarak:

```bash
cp -n .env.example .env
cp -n web/.env.example web/.env.local
```

Sağlık ekranı için gerçek MIA/EVDS anahtarı gerekmez. MIA istemcisi ilk kullanımda
boş anahtarı veya `API_KEYINIZ` yer tutucusunu açıklayıcı hatayla reddeder.
Anahtarlar sadece kökteki `.env` dosyasına yazılır. Frontend ortamına aktarılmaz.

## Backend

Repo kökünde:

```bash
.venv/bin/uvicorn kkb_agent.api.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
curl http://localhost:8000/health
```

Başarılı yanıt:

```json
{"status":"ok","components":{"application":"ok","duckdb":"ok","lancedb":"ok"}}
```

Her istek yerel depoları kontrol eder. Gerekli klasörleri ve boş veritabanını ilk
kontrolde oluşturur; iş tabloları oluşturmaz. Bir depo açılamazsa 503 ve `degraded`
döner; ayrıntı sunucu logunda kalır. Sonraki istek yeniden kontrol eder. Başlangıç
ve sağlık kontrolleri MIA, EVDS veya SearxNG'ye bağlanmaz.

## Frontend

Ayrı terminalde:

```bash
cd web
npm ci
npm run dev -- --hostname 127.0.0.1 --port 3000
```

`http://localhost:3000` adresini açın. Sayfa backend durumunu gösterir ve yeniden
kontrol düğmesi sunar. `NEXT_PUBLIC_API_BASE_URL` yalnızca FastAPI adresidir;
varsayılan `http://localhost:8000`. Bu değer Next.js build sırasında tarayıcı
paketine alınır; değiştirince geliştirme sunucusunu yeniden başlatın veya tekrar
build alın. Hiçbir `NEXT_PUBLIC_*` değişkenine gizli anahtar koymayın.

Backend `CORS_ORIGINS` ayarı JSON listedir; varsayılan olarak localhost ve
127.0.0.1 üzerinde port 3000'e izin verir. Farklı frontend portu kullanıyorsanız
bu listeyi güncelleyin. `web/src/lib/api.ts`, HTTP ve gelecekteki EventSource
tüketicileri için ortak backend URL fonksiyonunu içerir. Henüz SSE endpoint'i
veya açık EventSource bağlantısı yoktur.

Üretim derlemesini yerelde doğrulamak için, `web/` altında:

```bash
npm run build
npm run typecheck
npm start -- --hostname 127.0.0.1 --port 3000
```

Dev ve build komutları Webpack kullanır; bu yerel ortamda Turbopack'in PostCSS
worker'ı port açma iznine takılmıştır.

Üretim build'i ve Chromium kurulumundan sonra repo kökünde
`.venv/bin/python scripts/smoke_local.py` gerçek tarayıcı/backend bağlantısını,
Tailwind stillerini ve hata sonrası yeniden denemeyi kontrol eder. Port 8000 ve
3000 boş olmalıdır; script kendi servislerini açıp kapatır ve geçici veritabanları
kullanır. Frontend build'inde varsayılan backend URL'si kullanılmalıdır.

## Depolama ve ortam ayarları

Pydantic Settings kökteki `.env` dosyasını okur; ortam değişkenleri önceliklidir.
Uygulamayı repo kökünden çalıştırın: göreli yollar çalışma dizinine göre çözülür.

- `DATA_DIR`: varsayılan `./data`.
- `DUCKDB_PATH`: varsayılan `./data/gold/lakehouse.duckdb`.
- `LANCEDB_PATH`: varsayılan `./data/gold/.lancedb`.

Son iki değişken tanımlanmamışsa yollar `DATA_DIR/gold/` altında türetilir.
`.env.example` bunları açıkça tanımlar; sadece DATA_DIR değiştirecekseniz bu iki
satırı kaldırın veya yeni konumla uyumlu güncelleyin. DuckDB gömülü bir veritabanıdır;
sunucu/container gerekmez. LanceDB şu aşamada yalnızca boş yerel katalog deposudur.

Yerel web araması için `docker compose up -d searxng` komutu SearxNG JSON API'sini
`http://localhost:8888` üzerinde başlatır. Backend adresi `SEARXNG_URL` ile değiştirilebilir.
Normal testler Docker veya canlı ağ gerektirmez. Sağlık kontrolü bu URL'yi kullanmaz;
ulaşılamayan arama servisi yalnızca web araması sırasında güvenli bir ret üretir.

## Testler ve lint

Repo kökünde:

```bash
.venv/bin/python -c "import kkb_agent; print(kkb_agent.__file__)"
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

Testler ağ veya gerçek anahtar gerektirmez; geçici dizinler kullanır. Geliştiricinin
`.env` dosyasını ve gerçek data/gold verilerini kullanmaz. DuckDB SELECT 1 ve bağlantı
kapanışı, LanceDB açma/yeniden açma, MIA istemci fabrikası, adapter sözleşmesi ve
FastAPI sağlık/CORS/hata sonrası toparlanma davranışı kapsanır.

Kaynak/test eşleştirme örneği:
`src/kkb_agent/catalog/duckdb_store.py` →
`tests/kkb_agent/catalog/test_duckdb_store.py`.

İsteğe bağlı Chromium smoke testi:

```bash
.venv/bin/python -c 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(); page = b.new_page(); page.set_content("<title>smoke</title>"); assert page.title() == "smoke"; b.close(); p.stop(); print("Chromium OK")'
```

## GitHub Actions

`.github/workflows/ci.yml`, push, pull request ve elle çalıştırmada backend
kontrollerini Python 3.11/3.13 ile, frontend kontrollerini Node.js 22 ile çalıştırır.
Backend işi import, Ruff ve pytest; frontend işi npm ci, TypeScript ve üretim
derlemesini kapsar. Workflow gerçek API anahtarı veya GitHub secret istemez.
Deployment yapmaz. Dosya GitHub'a push edildiğinde Actions altında çalışır.

## Sık karşılaşılan sorunlar

- `ModuleNotFoundError`: repo kökünden `.venv` oluşturun ve editable kurulumu yapın.
- pytest başlamadan `readline` içinde segmentation fault: bu makinede Anaconda
  Python 3.12 ile görüldü. uv yönetimli Python ile ayrı bir sanal ortam kullanın.
- Sağlık ekranı bağlantı hatası: FastAPI portunu, frontend backend URL'sini ve CORS
  origin listesini kontrol edin.
- Sağlık yanıtı 503: sunucu logunu, dizin yazma iznini ve aynı DuckDB dosyasını başka
  sürecin kilitleyip kilitlemediğini kontrol edin.
- Chromium executable bulunamadı: etkin ortamda `python -m playwright install chromium`.
- Paket veya tarayıcı indirmesi engelli: ağ/proxy izinlerini kontrol edip kurulumu tekrarlayın.

Frontend kurulumu için [Next.js kurulum belgesi](https://nextjs.org/docs/app/getting-started/installation)
ve [Tailwind Next.js rehberi](https://tailwindcss.com/docs/installation/framework-guides/nextjs)
esas alınmıştır.
