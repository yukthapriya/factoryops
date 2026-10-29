import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setTimeZoneMode, type TimeZoneMode } from "../lib/format";

export type RangeKey = "1h" | "6h" | "24h" | "7d";
export const RANGES: { key: RangeKey; label: string; ms: number }[] = [
  { key: "1h", label: "Last hour", ms: 3600e3 },
  { key: "6h", label: "Last 6 hours", ms: 6 * 3600e3 },
  { key: "24h", label: "Last 24 hours", ms: 24 * 3600e3 },
  { key: "7d", label: "Last 7 days", ms: 7 * 24 * 3600e3 },
];

/** Per-browser display preferences. Stored in localStorage; nothing here is system config. */
export interface Preferences {
  timeZone: TimeZoneMode;
  density: "comfortable" | "compact";
  defaultRange: RangeKey;
  liveUpdates: boolean;
  pollingSeconds: 0 | 15 | 30 | 60;
  operatorName: string;
}

export const DEFAULT_PREFERENCES: Preferences = {
  timeZone: "local",
  density: "comfortable",
  defaultRange: "24h",
  liveUpdates: true,
  pollingSeconds: 30,
  operatorName: "",
};

const STORAGE_KEY = "factoryops.preferences.v1";

function load(): Preferences {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_PREFERENCES, ...(JSON.parse(raw) as Partial<Preferences>) };
  } catch {
    /* storage unavailable or corrupt: fall back to defaults */
  }
  return DEFAULT_PREFERENCES;
}

interface PrefsContext {
  prefs: Preferences;
  update: (patch: Partial<Preferences>) => void;
  reset: () => void;
  range: RangeKey;
  setRange: (r: RangeKey) => void;
}

const Ctx = createContext<PrefsContext | null>(null);

export function PreferencesProvider({ children, initial }: { children: ReactNode; initial?: Partial<Preferences> }) {
  const [prefs, setPrefs] = useState<Preferences>(() => ({ ...load(), ...initial }));
  const [range, setRange] = useState<RangeKey>(prefs.defaultRange);

  useEffect(() => {
    setTimeZoneMode(prefs.timeZone);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch {
      /* ignore */
    }
  }, [prefs]);

  const update = useCallback((patch: Partial<Preferences>) => setPrefs((p) => ({ ...p, ...patch })), []);
  const reset = useCallback(() => setPrefs(DEFAULT_PREFERENCES), []);
  const value = useMemo(() => ({ prefs, update, reset, range, setRange }), [prefs, update, reset, range]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePreferences(): PrefsContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePreferences must be used inside PreferencesProvider");
  return ctx;
}

/** Converts a range key into concrete ISO bounds at call time (used inside query functions). */
export function windowFor(range: RangeKey, now = Date.now()): { start: string; end: string } {
  const ms = RANGES.find((r) => r.key === range)?.ms ?? 24 * 3600e3;
  return { start: new Date(now - ms).toISOString(), end: new Date(now).toISOString() };
}
