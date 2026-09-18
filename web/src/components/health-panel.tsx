"use client";

import { useEffect, useState } from "react";

import { getHealth, type Health } from "@/lib/api";

// Readiness belongs in the corner of the header, not in a panel of its own. It matters for
// about two seconds — long enough to answer "is the backend up?" before the first question —
// and the components behind that answer stay one click away for when it says no.

export function HealthPanel() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    getHealth(controller.signal)
      .then((result) => {
        if (active) setHealth(result);
      })
      .catch(() => {
        if (active) {
          setError(
            "Backend’e ulaşılamadı. FastAPI’nin çalıştığını ve backend URL ayarını kontrol edin.",
          );
        }
      })
      .finally(() => {
        clearTimeout(timer);
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [attempt]);

  const ok = health?.status === "ok";
  const dot = loading
    ? "bg-slate-500 animate-pulse"
    : error
      ? "bg-rose-400"
      : ok
        ? "bg-teal-400"
        : "bg-amber-400";
  const label = loading
    ? "Kontrol ediliyor…"
    : error
      ? "Backend kapalı"
      : ok
        ? "Sistem hazır"
        : "Bazı bileşenler hazır değil";

  return (
    <details aria-live="polite" className="group relative">
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-full border border-slate-800 bg-slate-900/60 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-700">
        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${dot}`} />
        {label}
        <span aria-hidden="true" className="text-slate-600 transition group-open:rotate-180">
          ▾
        </span>
      </summary>

      <div className="absolute right-0 z-10 mt-2 w-72 rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm shadow-xl shadow-black/40">
        {error ? (
          <p className="text-slate-300" role="alert">
            {error}
          </p>
        ) : health ? (
          <dl className="space-y-2">
            {Object.entries(health.components).map(([name, status]) => (
              <div className="flex items-center justify-between gap-6" key={name}>
                <dt className="truncate text-slate-400">{name}</dt>
                <dd className={status === "ok" ? "text-teal-400" : "text-amber-400"}>
                  {status === "ok" ? "Hazır" : "Hata"}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-slate-400">Kontrol ediliyor…</p>
        )}
        <button
          className="mt-4 w-full rounded-lg border border-slate-700 px-3 py-1.5 text-slate-300 hover:border-slate-600 disabled:opacity-50"
          disabled={loading}
          onClick={() => {
            setError("");
            setHealth(null);
            setLoading(true);
            setAttempt((value) => value + 1);
          }}
          type="button"
        >
          Yeniden kontrol et
        </button>
      </div>
    </details>
  );
}
