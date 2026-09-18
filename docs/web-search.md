# Web search tool

`WebSearchTool` exposes a provider-neutral, deterministic search contract. The production
adapter calls the JSON endpoint of a self-hosted SearxNG instance; it does not call MIA or
any other model. Start the local service from the repository root:

```bash
docker compose up -d searxng
```

The backend reads `SEARXNG_URL` (default `http://localhost:8888`). Docker is not required
for unit tests: provider responses are mocked at the HTTP boundary.

Requests contain a non-blank query, a result limit from 1 through 10 (default 5), and an
optional language. Results are immutable `WebSearchResult` objects containing normalized
`title`, `url`, and `snippet` values plus title/URL evidence references suitable for
verification citations. Missing title or snippet remains `None`; no text is fabricated.

SearxNG order is preserved. Result URLs pass through the existing public-URL/SSRF safety
boundary, fragments are removed, and duplicates are discarded by normalized URL. Unsafe
or malformed result URLs are filtered. An empty result list is a successful search.

Provider timeout, connection failure, malformed JSON, and invalid response shape become
typed domain errors. The generic router maps them to a short Turkish refusal without
exposing transport details. Successful generic searches retain the existing non-frame
behavior: their title/URL evidence is emitted as a safe, cited summary after a verification
stage, because the frozen `ResultPayload` still requires an `AnalysisFrame`.
