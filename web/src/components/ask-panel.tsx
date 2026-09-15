"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AnalysisTable } from "@/components/analysis-table";
import {
  STAGE_LABELS,
  STAGE_ORDER,
  TOOL_LABELS,
  type AskEvent,
  type ResultFrame,
  type StageName,
  askStream,
} from "@/lib/ask";

type StageState = {
  stage: StageName;
  status: "running" | "succeeded" | "failed";
  tools: string[];
  startedAt: number;
  endedAt?: number;
};

type Turn = {
  id: string;
  question: string;
  stages: StageState[];
  answer?: string;
  frame?: ResultFrame;
  error?: { code: string; message: string; retryable: boolean };
  outcome?: "succeeded" | "failed";
  startedAt: number;
};

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

function StageList({ turn, now }: { turn: Turn; now: number }) {
  const seen = new Map(turn.stages.map((stage) => [stage.stage, stage]));

  return (
    <ol className="mt-4 space-y-2">
      {STAGE_ORDER.map((name) => {
        const stage = seen.get(name);
        const status = stage?.status ?? "pending";
        return (
          <li className="flex items-start gap-3 text-sm" key={name}>
            <span
              aria-hidden="true"
              className={
                status === "running"
                  ? "mt-1.5 h-2 w-2 shrink-0 animate-pulse rounded-full bg-teal-400"
                  : status === "succeeded"
                    ? "mt-1.5 h-2 w-2 shrink-0 rounded-full bg-teal-400"
                    : status === "failed"
                      ? "mt-1.5 h-2 w-2 shrink-0 rounded-full bg-amber-400"
                      : "mt-1.5 h-2 w-2 shrink-0 rounded-full border border-slate-600"
              }
            />
            <span className={status === "pending" ? "text-slate-600" : "text-slate-200"}>
              {STAGE_LABELS[name]}
              {stage?.tools.length ? (
                <span className="ml-2 text-slate-400">
                  · {stage.tools.map((tool) => TOOL_LABELS[tool as never] ?? tool).join(", ")}
                </span>
              ) : null}
            </span>
            {stage ? (
              <span className="ml-auto shrink-0 tabular-nums text-slate-500">
                {status === "running"
                  ? elapsed(stage.startedAt, now)
                  : elapsed(stage.startedAt, stage.endedAt ?? now)}
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

export function AskPanel() {
  const [question, setQuestion] = useState(
    "2021-2025 arasında konut kredileri ve faiz oranlarını aylık göster.",
  );
  const [turns, setTurns] = useState<Turn[]>([]);
  const [running, setRunning] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const now = useClock(running);

  useEffect(() => () => controllerRef.current?.abort(), []);

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
            return { ...turn, answer: event.payload.answer, frame: event.payload.frame };
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
    const previous = turns[turns.length - 1];
    setTurns((current) => [
      ...current,
      { id: turnId, question: asked, stages: [], startedAt: Date.now() },
    ]);
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
            ? { ...turn, outcome: turn.answer ? "succeeded" : "failed" }
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
          className="w-full rounded-xl border border-slate-700 bg-slate-900 p-4 text-slate-100 placeholder:text-slate-500"
          disabled={running}
          id="question"
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Türkçe bir soru yazın…"
          rows={3}
          value={question}
        />
        <div className="mt-3 flex items-center gap-3">
          <button
            className="rounded-lg bg-teal-400 px-4 py-2 font-medium text-slate-950 disabled:opacity-50"
            disabled={running || !question.trim()}
            type="submit"
          >
            {running ? "Çalışıyor…" : "Gönder"}
          </button>
          {running ? (
            <button
              className="rounded-lg border border-slate-700 px-4 py-2 text-slate-300"
              onClick={() => controllerRef.current?.abort()}
              type="button"
            >
              Durdur
            </button>
          ) : null}
        </div>
      </form>

      <div aria-live="polite" className="mt-10 space-y-10">
        {turns.map((turn) => {
          const active = running && !turn.outcome;
          return (
            <article className="rounded-xl border border-slate-700 p-6" key={turn.id}>
              <p className="font-medium text-slate-100">{turn.question}</p>

              <StageList now={now} turn={turn} />

              {active ? (
                <p className="mt-4 text-sm text-slate-400">
                  {turn.stages.length
                    ? `${STAGE_LABELS[turn.stages[turn.stages.length - 1].stage]} sürüyor…`
                    : "Başlatılıyor…"}{" "}
                  <span className="tabular-nums">{elapsed(turn.startedAt, now)}</span>
                </p>
              ) : null}

              {turn.error ? (
                <p
                  className="mt-5 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-amber-200"
                  role="alert"
                >
                  {turn.error.message}
                  <span className="mt-1 block text-xs text-amber-300/70">
                    {turn.error.code}
                    {turn.error.retryable ? " · tekrar denenebilir" : " · tekrar denemeyin"}
                  </span>
                </p>
              ) : null}

              {turn.answer ? (
                <p className="mt-5 whitespace-pre-wrap leading-relaxed text-slate-200">
                  {turn.answer}
                </p>
              ) : null}

              {turn.frame && turn.frame.columns.length > 0 ? (
                <div className="mt-6 min-w-0">
                  <AnalysisTable frame={turn.frame} />
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
