export type AnalysisCell = string | number | boolean | null;

export type AnalysisUnit = {
  symbol: string | null;
  scale: number;
};

export type AnalysisLineage = {
  sources: { source_type: string; reference: string; raw_sha256?: string | null }[];
  parents: { column_key: string; lineage: AnalysisLineage }[];
};

export type AnalysisColumn = {
  lineage?: AnalysisLineage;
  key: string;
  label: string;
  values: readonly AnalysisCell[];
  unit: AnalysisUnit | null;
};

export type AnalysisTableFrame = {
  spine: {
    key: string;
    label: string | null;
    values: readonly string[];
  };
  columns: readonly AnalysisColumn[];
};

export type AnalysisChartType = "line" | "bar" | "scatter";

export type AnalysisChartAxisAssignment = {
  column_key: string;
  axis: "left" | "right";
};

export type AnalysisChartSpec = {
  chart_id: string;
  chart_type: AnalysisChartType;
  spine_key: string;
  column_keys: readonly string[];
  axis_policy: "by_unit";
  axis_assignments: readonly AnalysisChartAxisAssignment[];
  indexing_recommended: boolean;
  title: string | null;
};

export type AnalysisReplayFrame = AnalysisTableFrame & {
  charts: readonly AnalysisChartSpec[];
};
