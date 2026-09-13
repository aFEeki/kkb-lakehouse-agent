export type AnalysisCell = string | number | boolean | null;

export type AnalysisUnit = {
  symbol: string | null;
  scale: number;
};

export type AnalysisColumn = {
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
