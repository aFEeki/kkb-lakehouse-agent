"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";

import type { AnalysisChartSpec, AnalysisTableFrame } from "@/lib/analysis-frame";
import {
  buildPlotlyChartConfiguration,
  ChartConfigurationError,
  type ChartTheme,
} from "@/lib/chart-config";

const Plot = dynamic(() => import("react-plotly.js"), {
  loading: () => <p className="p-6 text-sm text-slate-400">Grafik hazırlanıyor…</p>,
  ssr: false,
});

function usePreferredChartTheme(): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>("dark");

  useEffect(() => {
    const preference = window.matchMedia("(prefers-color-scheme: dark)");
    const updateTheme = () => setTheme(preference.matches ? "dark" : "light");
    updateTheme();
    preference.addEventListener("change", updateTheme);
    return () => preference.removeEventListener("change", updateTheme);
  }, []);

  return theme;
}

type ConfigurationState =
  | { configuration: ReturnType<typeof buildPlotlyChartConfiguration>; errorCode: null }
  | { configuration: null; errorCode: string };

export function AnalysisChart({
  chart,
  frame,
}: {
  chart: AnalysisChartSpec;
  frame: AnalysisTableFrame;
}) {
  const theme = usePreferredChartTheme();
  const state = useMemo<ConfigurationState>(() => {
    try {
      return {
        configuration: buildPlotlyChartConfiguration(frame, chart, theme),
        errorCode: null,
      };
    } catch (error) {
      return {
        configuration: null,
        errorCode: error instanceof ChartConfigurationError ? error.code : "invalid_chart_spec",
      };
    }
  }, [chart, frame, theme]);

  if (state.configuration === null) {
    return (
      <div
        className="rounded-lg border border-red-700 bg-red-950/40 p-4 text-sm text-red-200"
        role="alert"
      >
        Grafik yapılandırması geçersiz: {state.errorCode}
      </div>
    );
  }

  return (
    <figure className="min-w-0 overflow-hidden rounded-lg border border-slate-700 bg-white dark:bg-slate-950">
      <Plot
        className="min-h-[28rem] w-full"
        config={state.configuration.config}
        data={state.configuration.data}
        divId={chart.chart_id}
        layout={state.configuration.layout}
        style={{ height: "28rem", width: "100%" }}
        useResizeHandler
      />
      {chart.indexing_recommended ? (
        <figcaption className="border-t border-slate-700 px-4 py-3 text-sm text-slate-600 dark:text-slate-300">
          Ölçek farkı nedeniyle indeksleme öneriliyor; gösterilen değerler değiştirilmedi.
        </figcaption>
      ) : null}
    </figure>
  );
}
