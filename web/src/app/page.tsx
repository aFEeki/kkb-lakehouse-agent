import { AnalysisTable } from "@/components/analysis-table";
import { HealthPanel } from "@/components/health-panel";
import { loadSuccessfulReplayFrame } from "@/lib/replay-fixture";

export default function Home() {
  const frame = loadSuccessfulReplayFrame();

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-20 text-slate-100">
      <div className="mx-auto min-w-0 max-w-6xl">
        <p className="text-sm text-teal-400">Yerel geliştirme</p>
        <h1 className="mt-3 text-3xl font-semibold">KKB Lakehouse Agent</h1>
        <p className="mt-4 text-slate-400">Uygulama ve yerel veri depolarının bağlantı durumu.</p>
        <div className="max-w-xl">
          <HealthPanel />
        </div>
        <section className="mt-12 min-w-0" aria-labelledby="analysis-table-title">
          <div className="mb-4">
            <p className="text-sm text-teal-400">SCRUM-68 replay fixture</p>
            <h2 className="mt-1 text-xl font-semibold" id="analysis-table-title">
              Analiz tablosu
            </h2>
          </div>
          <AnalysisTable frame={frame} />
        </section>
      </div>
    </main>
  );
}
