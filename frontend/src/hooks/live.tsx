import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { LiveConnection, liveUrl, type LiveStatus } from "../lib/live";
import type { LiveMessage } from "../lib/types";
import { usePreferences } from "./preferences";

/** Query-key roots invalidated by each live message type. */
export const INVALIDATIONS: Record<string, string[]> = {
  telemetry: ["dashboard", "telemetry", "equipment", "runs", "analytics"],
  alert: ["alerts", "dashboard", "analytics"],
  defect: ["defects", "dashboard", "analytics", "runs"],
  data_quality: ["data-quality", "dashboard"],
  equipment_status: ["equipment", "dashboard"],
  equipment: ["equipment", "dashboard"],
};
const THROTTLE_MS = 3000;

interface LiveState {
  status: LiveStatus;
  listenerConnected: boolean | null;
  nextRetryMs: number | null;
  lastMessageAt: string | null;
  feed: LiveMessage[];
  newAlerts: number;
  clearNewAlerts: () => void;
}

const Ctx = createContext<LiveState | null>(null);

/** Coalesces invalidations per key root: at most one every THROTTLE_MS (trailing). */
export function createInvalidator(qc: QueryClient, throttleMs = THROTTLE_MS) {
  const pending = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  return {
    add(keys: string[]) {
      keys.forEach((k) => pending.add(k));
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        const roots = [...pending];
        pending.clear();
        roots.forEach((root) => void qc.invalidateQueries({ queryKey: [root] }));
      }, throttleMs);
    },
    cancel() {
      if (timer) clearTimeout(timer);
      timer = null;
      pending.clear();
    },
  };
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { prefs } = usePreferences();
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [listenerConnected, setListener] = useState<boolean | null>(null);
  const [nextRetryMs, setNextRetry] = useState<number | null>(null);
  const [lastMessageAt, setLast] = useState<string | null>(null);
  const [feed, setFeed] = useState<LiveMessage[]>([]);
  const [newAlerts, setNewAlerts] = useState(0);
  const invalidator = useRef(createInvalidator(qc));

  useEffect(() => {
    if (!prefs.liveUpdates) {
      setStatus("closed");
      return;
    }
    const inv = invalidator.current;
    const conn = new LiveConnection(liveUrl(), {
      onStatus: (s, info) => {
        setStatus(s);
        setNextRetry(info.nextRetryMs);
      },
      onReconnect: () => void qc.invalidateQueries(), // refresh anything missed while offline
      onMessage: (msg) => {
        setLast(msg.ts);
        if (msg.type === "hello" || msg.type === "heartbeat") {
          setListener(Boolean(msg.data.listener_connected));
          return;
        }
        if (msg.type === "listener") {
          setListener(Boolean(msg.data.connected));
          if (msg.data.connected) void qc.invalidateQueries();
          return;
        }
        if (msg.type === "lagged") {
          void qc.invalidateQueries();
          return;
        }
        if (msg.type === "alert" && msg.data.action === "opened") setNewAlerts((n) => n + 1);
        if (msg.type !== "telemetry") setFeed((f) => [msg, ...f].slice(0, 40));
        const keys = INVALIDATIONS[msg.type];
        if (keys) inv.add(keys);
      },
    });
    conn.start();
    return () => {
      conn.stop();
      inv.cancel();
    };
  }, [prefs.liveUpdates, qc]);

  // Polling fallback while live updates are off or disconnected.
  useEffect(() => {
    if (status === "open" || prefs.pollingSeconds === 0) return;
    const id = setInterval(() => void qc.invalidateQueries({ type: "active" }), prefs.pollingSeconds * 1000);
    return () => clearInterval(id);
  }, [status, prefs.pollingSeconds, qc]);

  const clearNewAlerts = useCallback(() => setNewAlerts(0), []);
  const value = useMemo(
    () => ({ status, listenerConnected, nextRetryMs, lastMessageAt, feed, newAlerts, clearNewAlerts }),
    [status, listenerConnected, nextRetryMs, lastMessageAt, feed, newAlerts, clearNewAlerts],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLive(): LiveState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useLive must be used inside LiveProvider");
  return ctx;
}
