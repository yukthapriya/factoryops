export type TimeZoneMode = "local" | "utc";

let tzMode: TimeZoneMode = "local";
export function setTimeZoneMode(mode: TimeZoneMode) {
  tzMode = mode;
}

function opts(extra: Intl.DateTimeFormatOptions): Intl.DateTimeFormatOptions {
  return tzMode === "utc" ? { ...extra, timeZone: "UTC" } : extra;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = new Date(iso).toLocaleString(undefined, opts({ month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }));
  return tzMode === "utc" ? `${s} UTC` : s;
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, opts({ hour: "2-digit", minute: "2-digit" }));
}

export function fmtRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.round((now - new Date(iso).getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function fmtPct(n: number | null | undefined, digits = 2): string {
  return n === null || n === undefined ? "—" : `${fmtNum(n, digits)}%`;
}

const LABELS: Record<string, string> = {
  spc_3sigma: "SPC ±3σ",
  rule1_beyond_3sigma: "WE-1: beyond 3σ",
  rule2_run_of_8: "WE-2: run of 8",
  cycle_time: "Cycle time",
};

export function humanize(s: string): string {
  if (s in LABELS) return LABELS[s]!;
  return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
