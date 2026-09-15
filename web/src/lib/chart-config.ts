import type { Config, Data, Layout } from "plotly.js";

import type {
  AnalysisChartSpec,
  AnalysisColumn,
  AnalysisTableFrame,
  AnalysisUnit,
} from "@/lib/analysis-frame";

export type ChartTheme = "light" | "dark";

export type PlotlyChartConfiguration = {
  data: Data[];
  layout: Partial<Layout>;
  config: Partial<Config>;
};

export type ChartConfigurationErrorCode =
  | "axis_assignment_mismatch"
  | "duplicate_column_reference"
  | "invalid_axis"
  | "invalid_axis_policy"
  | "invalid_chart_spec"
  | "invalid_chart_type"
  | "invalid_column_values"
  | "invalid_unit"
  | "missing_axis_assignments"
  | "missing_left_axis"
  | "unknown_column"
  | "unknown_spine";

export class ChartConfigurationError extends Error {
  readonly code: ChartConfigurationErrorCode;

  constructor(
    code: ChartConfigurationErrorCode,
    message: string,
  ) {
    super(message);
    this.code = code;
    this.name = "ChartConfigurationError";
  }
}

const SCALE_FORMATTER = new Intl.NumberFormat("tr-TR", {
  maximumFractionDigits: 20,
  useGrouping: true,
});

const THEME_PALETTES = {
  dark: {
    grid: "#334155",
    paper: "#020617",
    plot: "#0f172a",
    text: "#e2e8f0",
    zero: "#64748b",
  },
  light: {
    grid: "#cbd5e1",
    paper: "#ffffff",
    plot: "#f8fafc",
    text: "#0f172a",
    zero: "#94a3b8",
  },
} as const;

function unitLabel(unit: AnalysisUnit): string {
  return `${unit.symbol} · ölçek ${SCALE_FORMATTER.format(unit.scale)}`;
}

function requireRenderableUnit(column: AnalysisColumn): AnalysisUnit {
  const unit = column.unit;
  if (
    unit === null ||
    typeof unit.symbol !== "string" ||
    unit.symbol.trim().length === 0 ||
    typeof unit.scale !== "number" ||
    !Number.isFinite(unit.scale) ||
    unit.scale <= 0
  ) {
    throw new ChartConfigurationError(
      "invalid_unit",
      `Column ${column.key} has no renderable unit and scale`,
    );
  }
  return unit;
}

function requireNumericValues(
  column: AnalysisColumn,
  spineLength: number,
): readonly (number | null)[] {
  if (
    column.values.length !== spineLength ||
    column.values.some(
      (value) => value !== null && (typeof value !== "number" || !Number.isFinite(value)),
    )
  ) {
    throw new ChartConfigurationError(
      "invalid_column_values",
      `Column ${column.key} is not an aligned finite numeric series`,
    );
  }
  return column.values as readonly (number | null)[];
}

function traceFor(
  chart: AnalysisChartSpec,
  column: AnalysisColumn,
  values: readonly (number | null)[],
  spineValues: readonly string[],
  axis: "left" | "right",
): Data {
  const yaxis: "y" | "y2" = axis === "left" ? "y" : "y2";
  const shared = {
    x: [...spineValues],
    y: [...values],
    name: column.label,
    meta: { column_key: column.key },
    uid: `${chart.chart_id}:${column.key}`,
    yaxis,
  };

  switch (chart.chart_type) {
    case "line":
      return { ...shared, type: "scatter", mode: "lines", connectgaps: false };
    case "scatter":
      return { ...shared, type: "scatter", mode: "markers", connectgaps: false };
    case "bar":
      return { ...shared, type: "bar" };
    default:
      throw new ChartConfigurationError(
        "invalid_chart_type",
        `Chart ${chart.chart_id} has an unsupported chart type`,
      );
  }
}

function axisTitle(
  axis: "left" | "right",
  chart: AnalysisChartSpec,
  columns: ReadonlyMap<string, AnalysisColumn>,
): string | undefined {
  const labels: string[] = [];
  const seen = new Set<string>();

  for (const assignment of chart.axis_assignments) {
    if (assignment.axis !== axis) continue;
    const column = columns.get(assignment.column_key);
    if (!column) continue;
    const label = unitLabel(requireRenderableUnit(column));
    if (!seen.has(label)) {
      labels.push(label);
      seen.add(label);
    }
  }
  return labels.length > 0 ? labels.join(" / ") : undefined;
}

export function buildPlotlyChartConfiguration(
  frame: AnalysisTableFrame,
  chart: AnalysisChartSpec,
  theme: ChartTheme,
): PlotlyChartConfiguration {
  if (
    typeof chart.chart_id !== "string" ||
    chart.chart_id.length === 0 ||
    typeof chart.spine_key !== "string" ||
    !Array.isArray(chart.column_keys) ||
    chart.column_keys.length === 0 ||
    chart.column_keys.some((columnKey) => typeof columnKey !== "string" || columnKey.length === 0) ||
    !Array.isArray(chart.axis_assignments) ||
    typeof chart.indexing_recommended !== "boolean" ||
    (chart.title !== null && typeof chart.title !== "string")
  ) {
    throw new ChartConfigurationError(
      "invalid_chart_spec",
      "ChartSpec is incomplete or structurally invalid",
    );
  }
  if (chart.spine_key !== frame.spine.key) {
    throw new ChartConfigurationError(
      "unknown_spine",
      `Chart ${chart.chart_id} does not reference the frame spine`,
    );
  }
  if (chart.axis_policy !== "by_unit") {
    throw new ChartConfigurationError(
      "invalid_axis_policy",
      `Chart ${chart.chart_id} has an unsupported axis policy`,
    );
  }
  if (!(["line", "bar", "scatter"] as readonly unknown[]).includes(chart.chart_type)) {
    throw new ChartConfigurationError(
      "invalid_chart_type",
      `Chart ${chart.chart_id} has an unsupported chart type`,
    );
  }
  if (chart.column_keys.length !== new Set(chart.column_keys).size) {
    throw new ChartConfigurationError(
      "duplicate_column_reference",
      `Chart ${chart.chart_id} contains duplicate column references`,
    );
  }
  if (chart.axis_assignments.length === 0) {
    throw new ChartConfigurationError(
      "missing_axis_assignments",
      `Chart ${chart.chart_id} has no explicit axis assignments`,
    );
  }
  if (chart.axis_assignments.length !== chart.column_keys.length) {
    throw new ChartConfigurationError(
      "axis_assignment_mismatch",
      `Chart ${chart.chart_id} axis assignments do not match its columns`,
    );
  }

  const frameColumns = new Map(frame.columns.map((column) => [column.key, column]));
  const data: Data[] = [];

  for (const [index, columnKey] of chart.column_keys.entries()) {
    const assignment = chart.axis_assignments[index];
    if (!assignment || assignment.column_key !== columnKey) {
      throw new ChartConfigurationError(
        "axis_assignment_mismatch",
        `Chart ${chart.chart_id} axis assignments do not preserve column order`,
      );
    }
    if (assignment.axis !== "left" && assignment.axis !== "right") {
      throw new ChartConfigurationError(
        "invalid_axis",
        `Chart ${chart.chart_id} contains an invalid axis assignment`,
      );
    }

    const column = frameColumns.get(columnKey);
    if (!column) {
      throw new ChartConfigurationError(
        "unknown_column",
        `Chart ${chart.chart_id} references unknown column ${columnKey}`,
      );
    }
    requireRenderableUnit(column);
    const values = requireNumericValues(column, frame.spine.values.length);
    data.push(traceFor(chart, column, values, frame.spine.values, assignment.axis));
  }

  if (!chart.axis_assignments.some((assignment) => assignment.axis === "left")) {
    throw new ChartConfigurationError(
      "missing_left_axis",
      `Chart ${chart.chart_id} has no left-axis series`,
    );
  }

  const palette = THEME_PALETTES[theme];
  const usesRightAxis = chart.axis_assignments.some((assignment) => assignment.axis === "right");
  const layout: Partial<Layout> = {
    autosize: true,
    barmode: "group",
    font: { color: palette.text },
    hovermode: "x unified",
    legend: { orientation: "h", x: 0, y: 1.14 },
    margin: { b: 64, l: 80, r: usesRightAxis ? 80 : 32, t: chart.title ? 72 : 44 },
    paper_bgcolor: palette.paper,
    plot_bgcolor: palette.plot,
    showlegend: true,
    title: chart.title ? { text: chart.title } : undefined,
    uirevision: chart.chart_id,
    xaxis: {
      gridcolor: palette.grid,
      title: { text: frame.spine.label ?? frame.spine.key },
      zerolinecolor: palette.zero,
    },
    yaxis: {
      gridcolor: palette.grid,
      title: { text: axisTitle("left", chart, frameColumns) },
      zerolinecolor: palette.zero,
    },
    yaxis2: usesRightAxis
      ? {
          gridcolor: palette.grid,
          overlaying: "y",
          side: "right",
          showgrid: false,
          title: { text: axisTitle("right", chart, frameColumns) },
          zerolinecolor: palette.zero,
        }
      : undefined,
  };

  return {
    data,
    layout,
    config: {
      displaylogo: false,
      responsive: true,
      scrollZoom: false,
    },
  };
}
