const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usdCompact = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
const int = new Intl.NumberFormat("en-US");

export const DASH = "—";
const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

export function fmtUsd(n: unknown): string {
  return finite(n) ? usd.format(n) : DASH;
}
export function fmtUsdCompact(n: unknown): string {
  if (!finite(n)) return DASH;
  return n >= 10000 ? usdCompact.format(n) : usd.format(n);
}
export function fmtInt(n: unknown): string {
  return finite(n) ? int.format(n) : DASH;
}
export function fmtProb(n: unknown): string {
  return finite(n) ? n.toFixed(2) : DASH;
}
export function fmtPct(n: unknown): string {
  return finite(n) ? `${Math.round(n * 100)}%` : DASH;
}
export function fmtSeconds(n: unknown): string {
  return finite(n) ? `${n.toFixed(1)} s` : DASH;
}
export function fmtMs(n: unknown): string {
  if (!finite(n)) return DASH;
  return n >= 1000 ? `${(n / 1000).toFixed(2)} s` : `${Math.round(n)} ms`;
}
export function humanize(value: string): string {
  if (!value) return DASH;
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
export function shortSha(sha?: string): string {
  return sha ? sha.slice(0, 7) : DASH;
}
