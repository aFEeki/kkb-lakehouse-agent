"use client";

import { useEffect, useState } from "react";

import { getHealth, type Health } from "@/lib/api";

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

  return (
    <>
      <section aria-live="polite" className="mt-8 rounded-xl border border-slate-700 p-6">
        {loading ? (
          <p>Kontrol ediliyor…</p>
        ) : error ? (
          <p role="alert">{error}</p>
        ) : health ? (
          <>
            <p className="mb-5 font-semibold">
              {health.status === "ok" ? "Sistem hazır" : "Bazı bileşenler hazır değil"}
            </p>
            <dl className="space-y-3">
              {Object.entries(health.components).map(([name, status]) => (
                <div className="flex justify-between gap-6" key={name}>
                  <dt>{name}</dt>
                  <dd className={status === "ok" ? "text-teal-400" : "text-amber-400"}>
                    {status === "ok" ? "Hazır" : "Hata"}
                  </dd>
                </div>
              ))}
            </dl>
          </>
        ) : null}
      </section>
      <button
        className="mt-5 rounded-lg bg-teal-400 px-4 py-2 font-medium text-slate-950 disabled:opacity-50"
        disabled={loading}
        onClick={() => {
          setError("");
          setHealth(null);
          setLoading(true);
          setAttempt((value) => value + 1);
        }}
      >
        Yeniden kontrol et
      </button>
    </>
  );
}
