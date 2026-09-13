# URL agent safety boundary

`SafeURLFetcher` is the network and resource-safety boundary for future URL-agent handlers.
It accepts only HTTP(S), resolves the initial hostname and every redirect target, and rejects
private, loopback, link-local and other non-global addresses before issuing that request.
Redirects are followed manually so every hop passes the same validation.

`URLSafetyLimits` carries the deterministic maximum redirect count, response byte count and
PDF page count. The byte limit is checked against `Content-Length` when present and again
while the response body is streamed, so a missing or false header cannot bypass the limit.
Declared PDFs are inspected only to enforce page count. Encrypted and malformed PDFs fail
with explicit safe errors; text or OCR extraction belongs to separate URL-agent tasks.

Fetched bytes are returned in the frozen `UntrustedContent` data object with
`trust_level="untrusted"`. Document text is data and must never be promoted to a system,
developer or planner instruction. This module does not invoke the planner or an LLM. A test
uses instruction-injection text and verifies that it remains immutable untrusted bytes.

Application-level DNS validation reduces SSRF risk but cannot completely eliminate DNS
rebinding between validation and socket connection. Production deployment must also restrict
outbound network access. Network firewall and deployment changes are outside SCRUM-61.

This boundary does not implement content routing, parsing, OCR, link discovery, browser
rendering, end-to-end timeout orchestration, upload handling, API endpoints or UI behavior.
