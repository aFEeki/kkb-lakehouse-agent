// Shared backend URL for HTTP and future EventSource/SSE consumers.
const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export function backendUrl(path: `/${string}`): string {
  return `${baseUrl}${path}`;
}

export type Health = {
  status: "ok" | "degraded";
  components: {
    application: "ok" | "error";
    duckdb: "ok" | "error";
    lancedb: "ok" | "error";
  };
};

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch(backendUrl("/health"), { cache: "no-store", signal });
  if (!response.ok && response.status !== 503) {
    throw new Error(`Backend HTTP ${response.status}`);
  }
  const data = await response.json();
  if (
    !["ok", "degraded"].includes(data?.status) ||
    !["application", "duckdb", "lancedb"].every(
      (name) => ["ok", "error"].includes(data?.components?.[name]),
    )
  ) {
    throw new Error("Backend sağlık yanıtı beklenen biçimde değil.");
  }
  return data as Health;
}
