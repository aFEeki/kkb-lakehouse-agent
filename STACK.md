# Technology Stack

Every technology used in the project. Version floors are in [`pyproject.toml`](pyproject.toml).

Three rules determine this entire list:

1. **Python for the backend and agentic layer.** Per the competition rules, no development
   in an additional language unless there is a specific requirement. The frontend is the
   stated exception.
2. **Open-source libraries only.** That is what the licence column is for.
3. **Kloudeks only at runtime.** No third-party LLM service is used.

---

## Language and runtime

| Technology | Purpose | Licence |
|---|---|---|
| Python 3.11+ | Backend, agentic layer, all data work | PSF |
| TypeScript | Frontend only | Apache-2.0 |
| SQL (DuckDB dialect) | Lakehouse queries | — |

---

## AI and inference — Kloudeks MIA

The only inference provider. The endpoint is OpenAI-compatible: `https://mia.csp.kloudeks.com/v1`

| Model | Purpose | Limit |
|---|---|---|
| `kkbhackathon2026/Qwen3.8-27B` | Question understanding, planning, narrative generation, vision | Max 5 images per prompt |
| `kkbhackathon2026/Qwen3-Embedding-8B` | Embedding vectors for semantic search over series metadata | — |
| `kkbhackathon2026/Unlimited-OCR` | Text extraction from document images | Max 3 images per prompt |

| Library | Purpose | Licence |
|---|---|---|
| `openai` (Python SDK) | MIA client — the endpoint is OpenAI-compatible, so the standard client is used | Apache-2.0 |

The API key lives in an environment variable, server-side only. It never reaches frontend
code under any circumstances.

---

## Storage

| Technology | Purpose | Licence |
|---|---|---|
| **DuckDB** | Analytical store and lakehouse query engine | MIT |
| **LanceDB** | Vector store for series metadata | Apache-2.0 |
| Apache Parquet (PyArrow) | File format for the silver and gold layers | Apache-2.0 |
| Local filesystem | Bronze layer — raw source files archived with their SHA-256 | — |

Layering: `bronze` (raw, untouched) → `silver` (parsed, still in source units) → `gold`
(harmonised, de-cumulated, joinable).

---

## Data processing

| Technology | Purpose | Licence |
|---|---|---|
| Pandas | Dataframes, time-series alignment | BSD-3-Clause |
| NumPy | Numerics | BSD-3-Clause |
| PyArrow | Parquet I/O, DuckDB interop | Apache-2.0 |

---

## Acquisition and document parsing

| Technology | Purpose | Licence |
|---|---|---|
| httpx | HTTP client — EVDS API, BDDK files, live URLs | BSD-3-Clause |
| openpyxl | `.xlsx` bulletins | MIT |
| xlrd | Legacy `.xls` — BDDK still publishes some files in this format | BSD |
| BeautifulSoup4 | HTML parsing, finding the target document linked from a page | MIT |
| lxml | HTML/XML backend | BSD |
| pypdf | Direct extraction from PDFs that have a text layer | BSD-3-Clause |
| pypdfium2 | Rendering PDF pages to PNG — the step before OCR | Apache-2.0 / BSD-3-Clause |
| Playwright | JavaScript-rendered pages (headless Chromium) | Apache-2.0 |

Order matters: text extraction is attempted first, and only if that yields nothing is the
page rendered to an image and sent to Unlimited-OCR. OCR is the fallback path, not the
default.

---

## Analysis

| Technology | Purpose | Licence |
|---|---|---|
| statsmodels | ADF and KPSS stationarity tests, cointegration, Granger / Toda-Yamamoto, STL decomposition | BSD-3-Clause |
| ruptures | Change-point detection (PELT, binary segmentation) | BSD-2-Clause |
| SciPy | Statistical tests, robust z-scores | BSD-3-Clause |

---

## Output and visualisation

| Technology | Purpose | Licence |
|---|---|---|
| Plotly (Python) | Chart specification generated server-side | MIT |
| plotly.js | Chart rendered in the browser | MIT |

Axis assignment is derived deterministically from column units rather than left to the
model's discretion.

---

## API and backend

| Technology | Purpose | Licence |
|---|---|---|
| FastAPI | HTTP API | MIT |
| Uvicorn | ASGI server | BSD-3-Clause |
| Pydantic | Schema validation — in particular, checking planner output against the operation vocabulary | MIT |
| Server-Sent Events | Streaming the stage trace to the interface | — |
| python-dotenv | Environment configuration | BSD-3-Clause |

---

## Frontend

| Technology | Purpose | Licence |
|---|---|---|
| React 19 | Component layer | MIT |
| Next.js | Application framework | MIT |
| TypeScript | Type safety | Apache-2.0 |
| Tailwind CSS | Styling | MIT |
| react-plotly.js | Chart component | MIT |

The frontend never calls MIA directly. The chain is always: browser → FastAPI → MIA.

---

## Web search

| Technology | Purpose | Licence |
|---|---|---|
| SearxNG (self-hosted) | Web Search tool | AGPL-3.0 |

Whether a commercial search API falls under the third-party rule has been raised with KKB.
Until an answer arrives, the self-hosted option is the default — so the eventual answer
cannot invalidate work already done.

---

## Development tooling

| Technology | Purpose | Licence |
|---|---|---|
| uv | Dependency and virtual environment management | MIT / Apache-2.0 |
| pytest | Tests, including the data invariant suite | MIT |
| ruff | Linting and formatting | MIT |
| Git / GitHub (private) | Version control | — |

---

## Deployment

| Technology | Purpose | Licence |
|---|---|---|
| Docker / Docker Compose | Packaging, and running SearxNG | Apache-2.0 |
| Hosting | **Undecided** — whether Kloudeks can host the application has been raised with KKB. Our own server is the default until it answers | — |

The data lake is entirely local files (DuckDB + Parquet). It moves wherever the application
moves, so the storage design does not depend on the hosting decision.

---

## Evaluated and left out of scope

| Technology | Reasoning |
|---|---|
| **Neo4j** | Lineage is genuinely graph-shaped, but the graph is a few dozen nodes per analysis object. Storing it as JSON inside DuckDB is sufficient. Standing up, operating and keeping a separate service alive on demo day costs more than it returns. Worth revisiting after Day 5 as an *addition* to a working system — never as something the trust layer depends on. See [DECISIONS.md](DECISIONS.md) #16 |
| Commercial search APIs | Not used until the rule's scope is clarified |
| Tesseract OCR | Unnecessary — Unlimited-OCR does the same job with Turkish support and no extra system dependency |

---

## Licence note

The licences above are widely known values and will be verified against `uv pip list` output
before submission. Every library used at runtime is open source.
