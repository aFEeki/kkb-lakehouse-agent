// Client for POST /ask. The endpoint is a POST, so the browser's EventSource (GET only)
// cannot be used and the event stream is parsed from the fetch body directly.

import type { AnalysisReplayFrame } from "@/lib/analysis-frame";
import { backendUrl } from "@/lib/api";

// The wire frame carries more than the table and chart components read; the extra fields
// are simply not described here.
export type ResultFrame = AnalysisReplayFrame & {
  frame_id: string;
  version: number;
};

export type StageName =
  | "data_discovery"
  | "data_preparation"
  | "agentic_analytics"
  | "analysis"
  | "verification";

export type ToolName =
  | "lakehouse"
  | "web_search"
  | "web_url"
  | "anomaly"
  | "causality"
  | "change_detection";

export type InformationalResult = {
  tool: ToolName;
  evidence: { title: string; url: string; snippet?: string | null }[];
  caveats: string[];
};

export type Outcome = "succeeded" | "failed";

type Envelope = {
  event_id: string;
  analysis_id: string;
  sequence: number;
  frame_version: number;
  occurred_at: string;
};

export type AskEvent =
  | (Envelope & { type: "stage_start"; payload: { stage: StageName } })
  | (Envelope & { type: "stage_end"; payload: { stage: StageName; outcome: Outcome } })
  | (Envelope & { type: "tool_selected"; payload: { tool: ToolName } })
  | (Envelope & { type: "result"; payload: { frame?: ResultFrame | null; answer: string; information?: InformationalResult | null } })
  | (Envelope & {
      type: "error";
      payload: { code: string; user_message: string; retryable: boolean };
    })
  | (Envelope & { type: "completion"; payload: { outcome: Outcome } });

export type AskRequest = {
  analysis_id: string;
  version: number;
  question: string;
};

// The five published pipeline stages, in the order the backend emits them.
export const STAGE_ORDER: StageName[] = [
  "data_discovery",
  "data_preparation",
  "agentic_analytics",
  "analysis",
  "verification",
];

export const STAGE_LABELS: Record<StageName, string> = {
  data_discovery: "Veri keşfi",
  data_preparation: "Veri hazırlığı",
  agentic_analytics: "Analiz planlama",
  analysis: "Analiz",
  verification: "Doğrulama",
};

export const TOOL_LABELS: Record<ToolName, string> = {
  lakehouse: "Lakehouse",
  web_search: "Web araması",
  web_url: "URL okuma",
  anomaly: "Anomali",
  causality: "Nedensellik",
  change_detection: "Değişim tespiti",
};

const EVENT_TYPES = new Set([
  "stage_start",
  "stage_end",
  "tool_selected",
  "result",
  "error",
  "completion",
]);

function parseEvent(frame: string): AskEvent | null {
  // One SSE frame. Only `data:` carries the payload; `id:` and `event:` duplicate fields
  // that are already inside it, so the JSON stays the single source of truth.
  const data = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    throw new Error("Sunucudan bozuk bir olay geldi.");
  }
  const event = parsed as AskEvent;
  if (!event || typeof event !== "object" || !EVENT_TYPES.has(event.type)) {
    throw new Error("Sunucudan tanınmayan bir olay geldi.");
  }
  return event;
}

export async function* askStream(
  request: AskRequest,
  signal?: AbortSignal,
): AsyncGenerator<AskEvent> {
  const response = await fetch(backendUrl("/ask"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(request),
    cache: "no-store",
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Backend isteği reddetti (HTTP ${response.status}).`);
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;

      // Frames are separated by a blank line; a chunk can split one in half, so only
      // whole frames are consumed and the remainder stays buffered.
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseEvent(frame);
        if (event) yield event;
        boundary = buffer.indexOf("\n\n");
      }
    }
    const trailing = parseEvent(buffer);
    if (trailing) yield trailing;
  } finally {
    reader.cancel().catch(() => undefined);
  }
}
