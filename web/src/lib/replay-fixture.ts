import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { AnalysisReplayFrame } from "@/lib/analysis-frame";

type ResultEvent = {
  type: "result";
  payload: {
    frame: AnalysisReplayFrame;
  };
};

export function loadSuccessfulReplayFrame(): AnalysisReplayFrame {
  const fixturePath = resolve(
    process.cwd(),
    "..",
    "tests",
    "fixtures",
    "api",
    "successful-stream.jsonl",
  );
  const result = readFileSync(fixturePath, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line) as { type?: string })
    .find((event) => event.type === "result") as ResultEvent | undefined;

  if (!result) {
    throw new Error("SCRUM-68 successful replay fixture has no result event");
  }

  const frame = result.payload.frame;
  return {
    spine: {
      key: frame.spine.key,
      label: frame.spine.label,
      values: frame.spine.values,
    },
    columns: frame.columns.map((column) => ({
      key: column.key,
      label: column.label,
      values: column.values,
      unit: column.unit === null ? null : { symbol: column.unit.symbol, scale: column.unit.scale },
    })),
    charts: frame.charts.map((chart) => ({
      chart_id: chart.chart_id,
      chart_type: chart.chart_type,
      spine_key: chart.spine_key,
      column_keys: chart.column_keys,
      axis_policy: chart.axis_policy,
      axis_assignments: chart.axis_assignments.map((assignment) => ({
        column_key: assignment.column_key,
        axis: assignment.axis,
      })),
      indexing_recommended: chart.indexing_recommended,
      title: chart.title,
    })),
  };
}
