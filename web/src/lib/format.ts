// Turkish number presentation. The catalog's values are exact; what reaches the screen
// through IEEE-754 is not always, and `18.387999999999998` on a table a judge is reading
// looks like a calculation error rather than the binary representation of 18.388.

const DISPLAY = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 4,
});

const EXACT = new Intl.NumberFormat("tr-TR", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 20,
});

/** What the cell shows: grouped, at most four decimals, Turkish separators. */
export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return DISPLAY.format(value);
}

/**
 * What the cell carries on hover.
 *
 * Rounding for display is a presentation choice, so the unrounded value stays one hover
 * away rather than disappearing. Returns null when the two agree and a tooltip would only
 * repeat what is already on screen.
 */
export function exactNumber(value: number): string | null {
  if (!Number.isFinite(value)) return null;
  const exact = EXACT.format(value);
  return exact === DISPLAY.format(value) ? null : exact;
}
