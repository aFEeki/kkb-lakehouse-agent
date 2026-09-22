"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AnalysisChart } from "@/components/analysis-chart";
import { AnalysisTable } from "@/components/analysis-table";
import { Answer } from "@/components/answer";
import { StagePipeline, type StageState } from "@/components/stage-pipeline";
import { STAGE_LABELS, type AskEvent, type InformationalResult, type ResultFrame, askStream } from "@/lib/ask";

type Turn = {
  id: string;
  question: string;
  stages: StageState[];
  answer?: string;
  frame?: ResultFrame;
  information?: InformationalResult;
  error?: { code: string; message: string; retryable: boolean };
  outcome?: "succeeded" | "failed";
  startedAt: number;
};

// The three published questions, in the order they have to be asked: turns 2 and 3 modify
// the table turn 1 builds, so asking one first is refused. Offering them as an ordered
// sequence is the difference between a demo that walks the reviewer through the analysis
// and one that makes them guess the wording.
const SUGGESTIONS = [
  {
    label: "Tabloyu kur",
    question: "2021-2025 arasında konut kredileri ve faiz oranlarını aylık göster.",
  },
  {
    label: "Enflasyondan arındır",
    question: "Konut kredisi tutarlarını enflasyondan arındırır mısın?",
  },
  {
    label: "Sütun ekle",
    question:
      "Bu tabloyu hiç bozmadan, konut fiyat endeksini yeni sütun olarak ekle. Kredilerin artmamasının nedeni fiyat artışları olabilir mi?",
  },
];

function elapsed(from: number, to: number): string {
  return `${((to - from) / 1000).toFixed(1)} sn`;
}

/** Ticks while a turn is running, so the elapsed time on screen keeps moving. */
function useClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export function AskPanel() {
  const [question, setQuestion] = useState(
    "2021-2025 arasında konut kredileri ve faiz oranlarını aylık göster.",
  );
  const [turns, setTurns] = useState<Turn[]>([]);
  const [running, setRunning] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const latestTurnRef = useRef<HTMLElement | null>(null);
  const now = useClock(running);

  // A step counts as taken once it produced a table, not once it was typed: a turn that
  // failed leaves the sequence where it was, which is also where the backend left it.
  const taken = SUGGESTIONS.map((item) =>
    turns.some((turn) => turn.question === item.question && Boolean(turn.frame)),
  );
  const nextStep = taken.indexOf(false);

  useEffect(() => () => controllerRef.current?.abort(), []);

  // Bring the new turn into view. The question box sits above the conversation, so a
  // turn appended below it lands off-screen and the system looks like it did nothing.
  useEffect(() => {
    if (turns.length === 0) return;
    latestTurnRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length]);

  const applyEvent = useCallback((turnId: string, event: AskEvent) => {
    setTurns((current) =>
      current.map((turn) => {
        if (turn.id !== turnId) return turn;
        switch (event.type) {
          case "stage_start":
            return {
              ...turn,
              stages: [
                ...turn.stages,
                {
                  stage: event.payload.stage,
                  status: "running",
                  tools: [],
                  startedAt: Date.now(),
                },
              ],
            };
          case "tool_selected":
            return {
              ...turn,
              stages: turn.stages.map((stage, index) =>
                index === turn.stages.length - 1
                  ? { ...stage, tools: [...stage.tools, event.payload.tool] }
                  : stage,
              ),
            };
          case "stage_end":
            return {
              ...turn,
              stages: turn.stages.map((stage) =>
                stage.stage === event.payload.stage && stage.status === "running"
                  ? { ...stage, status: event.payload.outcome, endedAt: Date.now() }
                  : stage,
              ),
            };
          case "result":
            return { ...turn, answer: event.payload.answer, frame: event.payload.frame ?? undefined, information: event.payload.information ?? undefined };
          case "error":
            return {
              ...turn,
              error: {
                code: event.payload.code,
                message: event.payload.user_message,
                retryable: event.payload.retryable,
              },
            };
          case "completion":
            return { ...turn, outcome: event.payload.outcome };
          default:
            return turn;
        }
      }),
    );
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || running) return;

    const turnId = `turn-${turns.length + 1}-${Date.now()}`;
    // Continue from the most recent turn that actually produced a table, not simply the
    // previous one: a question that failed in between must not throw away the analysis
    // the turns before it built.
    const previous = [...turns].reverse().find((turn) => turn.frame && turn.outcome === "succeeded");
    setTurns((current) => [
      ...current,
      { id: turnId, question: asked, stages: [], startedAt: Date.now() },
    ]);
    setQuestion("");
    setRunning(true);

    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      // The next turn continues the same analysis at the version last seen, which is what
      // "bu tabloyu bozmadan" requires of turns 2 and 3.
      const stream = askStream(
        {
          analysis_id: previous?.frame?.frame_id ?? turnId,
          version: previous?.frame?.version ?? 0,
          question: asked,
        },
        controller.signal,
      );
      for await (const item of stream) {
        applyEvent(turnId, item);
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        applyEvent(turnId, {
          event_id: `${turnId}-local`,
          analysis_id: turnId,
          sequence: -1,
          frame_version: 0,
          occurred_at: new Date().toISOString(),
          type: "error",
          payload: {
            code: "CONNECTION_FAILED",
            user_message:
              failure instanceof Error ? failure.message : "Backend'e bağlanılamadı.",
            retryable: true,
          },
        });
      }
    } finally {
      setRunning(false);
      controllerRef.current = null;
      setTurns((current) =>
        current.map((turn) =>
          turn.id === turnId && !turn.outcome
            ? { ...turn, outcome: "failed" }
            : turn,
        ),
      );
    }
  }

  return (
    <section aria-labelledby="ask-title">
      <h2 className="text-xl font-semibold" id="ask-title">
        Soru sor
      </h2>

      <form className="mt-4" onSubmit={submit}>
        <label className="sr-only" htmlFor="question">
          Sorunuz
        </label>
        <textarea
          className="w-full rounded-xl border border-slate-700 bg-slate-900 p-4 text-slate-100 outline-none transition placeholder:text-slate-500 focus:border-teal-400/60 focus:ring-2 focus:ring-teal-400/20 disabled:opacity-60"
          disabled={running}
          id="question"
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Türkçe bir soru yazın…"
          rows={3}
          value={question}
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            className="rounded-lg bg-teal-400 px-4 py-2 font-medium text-slate-950 transition hover:bg-teal-300 disabled:opacity-50 disabled:hover:bg-teal-400"
            disabled={running || !question.trim()}
            type="submit"
          >
            {running ? "Çalışıyor…" : "Gönder"}
          </button>
          {running ? (
            <button
              className="rounded-lg border border-slate-700 px-4 py-2 text-slate-300 transition hover:border-slate-600"
              onClick={() => controllerRef.current?.abort()}
              type="button"
            >
              Durdur
            </button>
          ) : null}
        </div>
      </form>

      <div className="mt-6">
        <p className="text-xs font-medium uppercase tracking-wider text-slate-500">
          Örnek akış — sırayla
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {SUGGESTIONS.map((item, index) => {
            const isNext = index === nextStep;
            return (
              <button
                className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition disabled:opacity-50 ${
                  isNext
                    ? "border-teal-400/60 bg-teal-400/10 text-teal-200 hover:bg-teal-400/20"
                    : taken[index]
                      ? "border-slate-800 bg-slate-900/40 text-slate-500"
                      : "border-slate-800 bg-slate-900/40 text-slate-400 hover:border-slate-700"
                }`}
                disabled={running}
                key={item.label}
                onClick={() => setQuestion(item.question)}
                title={item.question}
                type="button"
              >
                <span className="tabular-nums text-xs text-slate-600">
                  {taken[index] ? "✓" : index + 1}
                </span>
                {item.label}
              </button>
            );
          })}
        </div>
      </div>

      <div aria-live="polite" className="mt-10 space-y-10">
        {turns.map((turn, index) => {
          const active = running && !turn.outcome;
          const isLatest = index === turns.length - 1;
          return (
            <article
              className="scroll-mt-6 rounded-xl border border-slate-800 bg-slate-900/40 p-6 shadow-lg shadow-black/20"
              data-outcome={turn.outcome}
              data-version={turn.frame?.version}
              key={turn.id}
              ref={isLatest ? latestTurnRef : null}
            >
              <p className="font-medium text-slate-100">{turn.question}</p>

              <StagePipeline now={now} stages={turn.stages} />

              {active ? (
                <p className="mt-4 text-sm text-slate-400">
                  {turn.stages.length
                    ? `${STAGE_LABELS[turn.stages[turn.stages.length - 1].stage]} sürüyor…`
                    : "Başlatılıyor…"}{" "}
                  <span className="tabular-nums">{elapsed(turn.startedAt, now)}</span>
                </p>
              ) : null}

              {turn.error ? (
                <p role="alert" className="mt-5 rounded-lg border border-amber-500/40 p-4 text-amber-200">
                  {turn.error.message}
                </p>
              ) : null}
              {turn.information ? (
                <div className="mt-5 space-y-3" data-testid="information-result">
                  <p>Kaynaklar · {turn.information.tool}</p>
                  {turn.information.evidence.map((item, i) => (
                    <div key={`${item.url}-${i}`}>
                      {/^https?:\/\//i.test(item.url) ? (
                        <a className="text-teal-300 underline" href={item.url} target="_blank" rel="noopener noreferrer">
                          {item.title}
                        </a>
                      ) : <span>{item.title}</span>}
                      <p className="break-all text-xs text-slate-400">{item.url}</p>
                      {item.snippet ? <p className="whitespace-pre-wrap">{item.snippet}</p> : null}
                    </div>
                  ))}
                  {turn.information.caveats.map((text, i) => <p key={i}>{text}</p>)}
                </div>
              ) : null}

              {turn.answer ? <Answer answer={turn.answer} /> : null}

              {turn.frame && turn.frame.columns.length > 0 ? (
                <div className="mt-6 min-w-0">
                  <AnalysisTable frame={turn.frame} />
                </div>
              ) : null}

              {turn.frame?.charts.length ? (
                // The chart comes from the frame's own ChartSpec, so it plots the table
                // above it rather than a second, separately derived view of the data.
                <div className="mt-6 min-w-0 space-y-6">
                  {turn.frame.charts.map((chart) => (
                    <AnalysisChart
                      chart={chart}
                      frame={{ spine: turn.frame!.spine, columns: turn.frame!.columns }}
                      key={chart.chart_id}
                    />
                  ))}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
