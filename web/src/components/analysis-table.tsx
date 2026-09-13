import type { AnalysisCell, AnalysisTableFrame, AnalysisUnit } from "@/lib/analysis-frame";

const scaleFormatter = new Intl.NumberFormat("tr-TR", {
  maximumFractionDigits: 20,
  useGrouping: true,
});

function unitLabel(unit: AnalysisUnit | null): string {
  if (unit === null) {
    return "Birim: — · ölçek: —";
  }
  return `Birim: ${unit.symbol ?? "—"} · ölçek: ${scaleFormatter.format(unit.scale)}`;
}

function CellValue({ value }: { value: AnalysisCell }) {
  if (value === null) {
    return (
      <span
        aria-label="Eksik değer"
        className="inline-block w-7 border-t border-dashed border-slate-500 align-middle"
        role="img"
      />
    );
  }
  return <>{typeof value === "boolean" ? (value ? "Evet" : "Hayır") : String(value)}</>;
}

export function AnalysisTable({ frame }: { frame: AnalysisTableFrame }) {
  const spineLabel = frame.spine.label ?? frame.spine.key;

  return (
    <div className="max-w-full overflow-x-auto overscroll-x-contain rounded-lg border border-slate-700">
      <table className="w-max min-w-full border-collapse text-left text-sm">
        <caption className="sr-only">Analiz sonuç tablosu</caption>
        <thead className="bg-slate-900 text-slate-200">
          <tr>
            <th className="whitespace-nowrap border-b border-slate-700 px-4 py-3" scope="col">
              <span className="block font-semibold">{spineLabel}</span>
              <span className="mt-1 block text-xs font-normal text-slate-500">Tarih omurgası</span>
            </th>
            {frame.columns.map((column) => (
              <th
                className="whitespace-nowrap border-b border-l border-slate-700 px-4 py-3"
                key={column.key}
                scope="col"
              >
                <span className="block font-semibold">{column.label}</span>
                <span className="mt-1 block text-xs font-normal text-slate-400">
                  {unitLabel(column.unit)}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800 bg-slate-950/60 text-slate-200">
          {frame.spine.values.map((spineValue, rowIndex) => (
            <tr className="hover:bg-slate-900/70" key={spineValue}>
              <th className="whitespace-nowrap px-4 py-3 font-medium text-slate-300" scope="row">
                {spineValue}
              </th>
              {frame.columns.map((column) => (
                <td
                  className="whitespace-nowrap border-l border-slate-800 px-4 py-3 tabular-nums"
                  key={column.key}
                >
                  <CellValue value={column.values[rowIndex] ?? null} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
