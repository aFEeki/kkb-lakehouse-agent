import { AskPanel } from "@/components/ask-panel";
import { HealthPanel } from "@/components/health-panel";

export default function Home() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-5">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-teal-400">
              KKB Hackathon 2026
            </p>
            <h1 className="mt-1 text-xl font-semibold">Lakehouse Agent</h1>
          </div>
          <HealthPanel />
        </div>
      </header>

      <main className="mx-auto min-w-0 max-w-5xl px-6 py-10">
        <p className="max-w-2xl text-slate-400">
          BDDK ve TCMB EVDS verileri üzerinde Türkçe soru sorun. Sistem soruyu ilgili araca
          yönlendirir, analizi hesaplar ve her sayının hangi seriden geldiğini gösterir.
        </p>
        <div className="mt-10">
          <AskPanel />
        </div>
      </main>

      <footer className="mx-auto max-w-5xl px-6 pb-12 text-xs text-slate-600">
        Veriler 2021-01 – 2026-06 aralığında sabit bir anlık görüntüden okunur; hiçbir sayı
        model tarafından üretilmez.
      </footer>
    </div>
  );
}
