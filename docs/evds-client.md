# EVDS client

`kkb_agent.ingest.evds.EVDSClient` fetches one TCMB EVDS series for an inclusive date range.
It returns exact `Decimal` observations and preserves missing values as `None`. It does not
persist data, populate the series catalog, or implement the `SourceAdapter` protocol.

## Documented API behavior

The current official EVDS Web Service and API Guide documents the EVDS3 series endpoint,
`dd-mm-yyyy` date parameters, JSON output through `type=json`, and the API key in the HTTP
`key` request header. It does not define a pagination parameter. Instead, it limits a web
service date range to at most 1,000 observations and returns the last 1,000 observations when
the requested range is larger. Splitting longer ranges belongs to the later ingestion task.

The guide recommends one data pull per day and combining required series from the same data
group into one request. It does not publish a numeric request quota or a retry policy. The
client therefore does not invent retries or throttling, and no request burst is used to seek
an HTTP 429 response.

Official references:

- <https://evds3.tcmb.gov.tr/igmevdsms-dis/documents/showDocument?docId=8>
- <https://evds3.tcmb.gov.tr/hakkinda>

## Controlled observation

Live observation is limited to small requests for `TP.DK.USD.S`. Do not generate a burst of
requests merely to provoke HTTP 429.

Observed on 2026-09-12 with the local, gitignored API key:

- `TP.DK.USD.S`, 2024-01-02 returned one observation with value `29.49130000`.
- The client result matched the date and exact decimal value in the official EVDS JSON payload.
- The response was HTTP 200 with `content-type: application/json;charset=UTF-8`.
- EVDS supplied `x-ratelimit-limit: 0` and `x-ratelimit-remaining: 0` despite the successful
  response. These values do not establish a usable numeric quota, so the documented daily-pull
  guidance remains the operative limit for this task.
- No pagination parameter was exercised. The current official guide explicitly defines the
  1,000-observation truncation behavior instead.

Local TLS configuration, invalid credentials, and transient network failures do not justify a
bulk-export fallback. That fallback is only considered after a repeatable platform or API
restriction prevents EVDS use for this task.
