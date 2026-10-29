import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router";
import { useLive } from "../hooks/live";
import { RANGES, usePreferences, type RangeKey } from "../hooks/preferences";
import { api } from "../lib/api";
import { fmtRelative } from "../lib/format";

const NAV = [
  { to: "/", label: "Overview", icon: "▦", range: true },
  { to: "/equipment", label: "Equipment", icon: "⚙", range: false },
  { to: "/runs", label: "Production Runs", icon: "▤", range: false },
  { to: "/defects", label: "Quality & Defects", icon: "◎", range: false },
  { to: "/alerts", label: "Alerts", icon: "△", range: false },
  { to: "/data-quality", label: "Data Quality", icon: "✓", range: true },
  { to: "/historical", label: "Historical Analysis", icon: "◷", range: false },
  { to: "/settings", label: "Settings", icon: "≡", range: false },
];

function current(pathname: string) {
  return [...NAV].reverse().find((n) => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to))) ?? NAV[0]!;
}

export function ConnectionStatus() {
  const { status, listenerConnected, nextRetryMs, lastMessageAt } = useLive();
  const { prefs } = usePreferences();
  let tone = "bg-good";
  let text = "Live";
  let detail = lastMessageAt ? `Last message ${fmtRelative(lastMessageAt)}` : "Connected";
  if (!prefs.liveUpdates) {
    tone = "bg-neutral";
    text = "Live updates off";
    detail = prefs.pollingSeconds ? `Polling every ${prefs.pollingSeconds}s` : "Manual refresh";
  } else if (status !== "open") {
    tone = "bg-warning";
    text = status === "connecting" ? "Connecting…" : "Reconnecting…";
    detail = nextRetryMs ? `Retry in ${Math.ceil(nextRetryMs / 1000)}s` : "Attempting connection";
  } else if (listenerConnected === false) {
    tone = "bg-warning";
    text = "Degraded";
    detail = "API connected but its database listener is down";
  }
  return (
    <div role="status" aria-label="Connection status" aria-live="polite" title={detail} className="flex items-center gap-2 rounded-full border border-line bg-panel-raised px-2.5 py-1 text-xs">
      <span aria-hidden className={`size-2 rounded-full ${tone} ${text === "Live" ? "animate-pulse" : ""}`} />
      <span className="text-ink">{text}</span>
      <span className="hidden text-ink-3 lg:inline">· {detail}</span>
    </div>
  );
}

function NotificationBell() {
  const { newAlerts, clearNewAlerts } = useLive();
  const q = useQuery({ queryKey: ["alerts", "active-count"], queryFn: () => api.alerts({ status: "active", page_size: 1 }) });
  const open = q.data?.total;
  const label = `${open ?? "Unknown number of"} active alerts${newAlerts ? `, ${newAlerts} new since last viewed` : ""}`;
  return (
    <Link to="/alerts?status=active" onClick={clearNewAlerts} aria-label={label} title={label} className="relative flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-ink-2 hover:bg-panel-raised hover:text-ink">
      <span aria-hidden>🔔</span>
      <span className="tabular">{open ?? "–"}</span>
      {newAlerts > 0 && (
        <span aria-hidden className="absolute -top-1 -right-1 rounded-full bg-critical px-1.5 text-[0.65rem] font-semibold text-white">
          +{newAlerts}
        </span>
      )}
    </Link>
  );
}

export function RangeSelect() {
  const { range, setRange } = usePreferences();
  return (
    <label className="flex items-center gap-1.5 text-xs text-ink-3">
      <span>Time range</span>
      <select value={range} onChange={(e) => setRange(e.target.value as RangeKey)} className="rounded-md border border-line-strong bg-panel-raised px-2 py-1 text-sm text-ink">
        {RANGES.map((r) => (
          <option key={r.key} value={r.key}>
            {r.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const page = current(pathname);
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-panel focus:px-3 focus:py-2">
        Skip to content
      </a>
      <aside
        className={`fixed inset-y-0 left-0 z-30 w-56 shrink-0 border-r border-line bg-panel transition-transform md:static md:translate-x-0 ${navOpen ? "translate-x-0" : "-translate-x-full"}`}
        aria-label="Primary"
      >
        <div className="flex h-14 items-center gap-2 border-b border-line px-4">
          <span aria-hidden className="flex size-7 items-center justify-center rounded bg-accent-strong text-sm font-bold">
            F
          </span>
          <div>
            <p className="text-sm font-semibold">FactoryOps</p>
            <p className="text-[0.65rem] tracking-wide text-ink-3 uppercase">Simulated plant</p>
          </div>
        </div>
        <nav className="p-2">
          <ul className="space-y-0.5">
            {NAV.map((n) => (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  end={n.to === "/"}
                  onClick={() => setNavOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm ${isActive ? "bg-accent/15 font-medium text-ink" : "text-ink-2 hover:bg-panel-raised hover:text-ink"}`
                  }
                >
                  <span aria-hidden className="w-4 text-center text-ink-3">
                    {n.icon}
                  </span>
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <p className="absolute right-0 bottom-0 left-0 border-t border-line p-3 text-[0.7rem] leading-snug text-ink-3">
          All equipment, telemetry and defects are synthetic demo data.
        </p>
      </aside>
      {navOpen && <div className="fixed inset-0 z-20 bg-black/50 md:hidden" onClick={() => setNavOpen(false)} aria-hidden />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center gap-3 border-b border-line bg-page/95 px-4 backdrop-blur">
          <button className="rounded p-1 text-ink-2 md:hidden" onClick={() => setNavOpen(true)} aria-label="Open navigation">
            ☰
          </button>
          <p className="truncate text-sm font-semibold">{page.label}</p>
          <span className="rounded border border-warning/40 px-1.5 py-0.5 text-[0.65rem] font-medium tracking-wide text-warning uppercase">Demo data</span>
          <div className="ml-auto flex items-center gap-3">
            {page.range && <RangeSelect />}
            <ConnectionStatus />
            <NotificationBell />
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-[1500px] flex-1 p-4 md:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
