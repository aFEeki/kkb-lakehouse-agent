import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { AnalysisTableFrame } from "@/lib/analysis-frame";

type ResultEvent = {
  type: "result";
  payload: {
    frame: AnalysisTableFrame;
  };
};

export function loadSuccessfulReplayFrame(): AnalysisTableFrame {
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
  return result.payload.frame;
}
