// The brief's five stages, as the pipeline they are. A vertical list of names reads as a
// log; the point on screen is that a question moves through a known sequence, and that the
// stage it is in right now is visibly still moving.

import { STAGE_LABELS, STAGE_ORDER, TOOL_LABELS, type StageName } from "@/lib/ask";

export type StageState = {
  stage: StageName;
  status: "running" | "succeeded" | "failed";
  tools: string[];
  startedAt: number;
  endedAt?: number;
};

function seconds(from: number, to: number): string {
  return `${((to - from) / 1000).toFixed(1)} sn`;
}

const DOT: Record<string, string> = {
  running: "bg-teal-400 animate-pulse ring-4 ring-teal-400/20",
  succeeded: "bg-teal-400",
  failed: "bg-amber-400",
  pending: "bg-slate-700",
};

export function StagePipeline({ stages, now }: { stages: StageState[]; now: number }) {
  const seen = new Map(stages.map((stage) => [stage.stage, stage]));

  return (
    <ol className="mt-5 flex flex-wrap gap-y-4">
      {STAGE_ORDER.map((name, index) => {
        const stage = seen.get(name);
        const status = stage?.status ?? "pending";
        const done = status === "succeeded" || status === "failed";
        return (
          <li className="flex min-w-0 flex-1 basis-40 items-start gap-3" key={name}>
            <div className="flex flex-col items-center pt-1">
              <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${DOT[status]}`} />
              {index < STAGE_ORDER.length - 1 ? (
                <span
                  aria-hidden="true"
                  className={`mt-1 h-full min-h-6 w-px ${done ? "bg-teal-400/30" : "bg-slate-800"}`}
                />
              ) : null}
            </div>
            <div className="min-w-0 pr-4">
              <p
                className={`truncate text-sm ${
                  status === "pending"
                    ? "text-slate-600"
                    : status === "running"
                      ? "font-medium text-teal-300"
                      : "text-slate-200"
                }`}
              >
                {STAGE_LABELS[name]}
              </p>
              {stage?.tools.length ? (
                <p className="mt-0.5 truncate text-xs text-slate-400">
                  {stage.tools.map((tool) => TOOL_LABELS[tool as never] ?? tool).join(", ")}
                </p>
              ) : null}
              {stage ? (
                <p className="mt-0.5 text-xs tabular-nums text-slate-500">
                  {seconds(stage.startedAt, stage.endedAt ?? now)}
                </p>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
