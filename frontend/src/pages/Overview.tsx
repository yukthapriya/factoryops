import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { BarList, SERIES, STATUS_COLORS, TrendChart } from "../components/charts";
import { Card, KpiCard, PageHeader, QueryState, SeverityBadge, StatusBadge } from "../components/ui";
import { useLive } from "../hooks/live";
import { usePreferences, windowFor } from "../hooks/preferences";
import { api } from "../lib/api";
import { fmtDateTime, fmtNum, fmtPct, fmtRelative, humanize } from "../lib/format";
import { METRICS, METRIC_META, type Metric, type TelemetryBucket } from "../lib/types";

const MAX_KEYS: Partial<Record<Metric, keyof TelemetryBucket>> = {
  temperature: "max_temperature_c",
  vibration: "max_vibration_mm_s",
};

export function OverviewPage() {
  const { range } = usePreferences();
  const [metric, setMetric] = useState<Metric>("temperature");

  const summary = useQuery({ queryKey: ["dashboard", range], queryFn: () => api.dashboard(windowFor(range)) });
  const trend = useQuery({ queryKey: ["telemetry", "summary", range], queryFn: () => api.telemetrySummary(windowFor(range)) });
  const defects = useQuery({ queryKey: ["analytics", "defects", range], queryFn: () => api.defectAnalytics(windowFor(range)) });
  const dq = useQuery({ queryKey: ["data-quality", "summary", range], queryFn: () => api.dqSummary(windowFor(range)) });
  const alerts = useQuery({ queryKey: ["alerts", "recent"], queryFn: () => api.alerts({ page_size: 6, sort: "-triggered_at" }) });
  const runs = useQuery({ queryKey: ["runs", "recent"], queryFn: () => api.runs({ page_size: 6, sort: "-started_at" }) });

  const s = summary.data;
  const meta = METRIC_META[metric];
  const maxKey = MAX_KEYS[metric];

  return (
    <>
      <PageHeader
        title="Operations overview"
        description={
          <>
            Synthetic demo data. Snapshot {s ? fmtDateTime(s.generated_at) : "…"} · latest telemetry {s?.last_event_at ? `${fmtDateTime(s.last_event_at)} (${fmtRelative(s.last_event_at)})` : "none yet"}
          </>
        }
      />

      {summary.isError ? (
        <QueryState query={summary}>{() => null}</QueryState>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <KpiCard label="Total equipment" value={fmtNum(s?.total_equipment)} sub="active units" loading={summary.isPending} />
          <KpiCard label="Running" value={fmtNum(s?.running_equipment)} tone="good" sub="current status" loading={summary.isPending} />
          <KpiCard
            label="Warning / critical"
            value={fmtNum(s?.warning_or_critical_equipment)}
            tone={s && s.warning_or_critical_equipment > 0 ? "critical" : undefined}
            sub={`${fmtNum(s?.open_alerts)} active alerts`}
            loading={summary.isPending}
          />
          <KpiCard label="Units processed" value={fmtNum(s?.units_produced)} sub={s?.calculation_notes.units_produced ? "running-cycle events in range" : undefined} loading={summary.isPending} />
          <KpiCard label="Defect rate" value={fmtPct(s?.defect_rate_pct)} sub={`${fmtNum(s?.defects)} defects in range`} loading={summary.isPending} />
          <KpiCard
            label="Events rejected"
            value={fmtNum(s?.events_rejected)}
            sub={`of ${fmtNum(s?.events_received)} received`}
            tone={s && s.events_rejected > 0 ? "warning" : undefined}
            loading={summary.isPending}
          />
        </div>
      )}

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Telemetry trend (all equipment)"
          description={trend.data ? `Bucketed averages every ${trend.data.bucket_seconds / 60} min` : undefined}
          actions={
            <div role="tablist" aria-label="Metric" className="flex gap-1">
              {METRICS.map((m) => (
                <button
                  key={m}
                  role="tab"
                  aria-selected={metric === m}
                  onClick={() => setMetric(m)}
                  className={`rounded px-2 py-1 text-xs ${metric === m ? "bg-accent/20 text-ink" : "text-ink-3 hover:text-ink"}`}
                >
                  {METRIC_META[m].label}
                </button>
              ))}
            </div>
          }
        >
          <QueryState query={trend}>
            {(t) => (
              <TrendChart
                label={`${meta.label} across all equipment over time`}
                caption={`Mean${maxKey ? " and maximum" : ""} ${meta.label.toLowerCase()} (${meta.unit}) per time bucket, accepted events only.`}
                data={t.buckets as unknown as Record<string, unknown>[]}
                xKey="bucket"
                unit={meta.unit}
                series={[{ key: meta.avgKey, label: "Average", color: SERIES[0] }, ...(maxKey ? [{ key: maxKey, label: "Maximum", color: SERIES[1] }] : [])]}
              />
            )}
          </QueryState>
        </Card>

        <Card title="Equipment status" description="Active units by current status">
          <QueryState query={summary}>
            {(d) => (
              <BarList
                label="Equipment count by operational status"
                caption={d.calculation_notes.equipment}
                valueLabel="Units"
                data={d.status_distribution.map((x) => ({ name: x.status, value: x.count }))}
                colorFor={(n) => STATUS_COLORS[n] ?? SERIES[0]}
              />
            )}
          </QueryState>
        </Card>

        <Card title="Defect rate by station" description="Defects ÷ units processed in range">
          <QueryState query={defects} isEmpty={(d) => d.by_station.length === 0}>
            {(d) => (
              <BarList
                label="Defect rate percentage by station"
                caption={d.calculation_notes.defect_rate_pct}
                valueLabel="Defect rate"
                format={(v) => `${v.toFixed(2)}%`}
                data={d.by_station.map((x) => ({ name: x.station, value: x.defect_rate_pct ?? 0, detail: `${x.defects} / ${fmtNum(x.units_produced)} units` }))}
              />
            )}
          </QueryState>
        </Card>

        <Card title="Recent alerts" actions={<Link to="/alerts" className="text-xs text-accent hover:underline">All alerts →</Link>}>
          <QueryState query={alerts} isEmpty={(d) => d.items.length === 0}>
            {(d) => (
              <ul className="divide-y divide-line">
                {d.items.map((a) => (
                  <li key={a.id} className="flex items-start gap-2 py-2 text-sm">
                    <SeverityBadge value={a.severity} />
                    <div className="min-w-0 flex-1">
                      <Link to={`/alerts?selected=${a.id}`} className="font-medium hover:underline">
                        {a.equipment.code} · {humanize(a.metric)}
                      </Link>
                      <p className="truncate text-xs text-ink-3" title={a.rule_detail}>
                        {a.rule_detail}
                      </p>
                    </div>
                    <div className="text-right text-xs text-ink-3">
                      <StatusBadge value={a.status} />
                      <p className="mt-1">{fmtRelative(a.triggered_at)}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </Card>

        <Card title="Ingestion & validation" actions={<Link to="/data-quality" className="text-xs text-accent hover:underline">Details →</Link>}>
          <QueryState query={dq}>
            {(d) => (
              <dl className="grid grid-cols-2 gap-3 text-sm">
                {[
                  ["Received", d.total_received],
                  ["Valid", d.valid],
                  ["Late / out-of-order (kept)", d.valid_late],
                  ["Invalid (rejected)", d.invalid],
                  ["Duplicates (ignored)", d.duplicates],
                  ["Acceptance rate", d.total_received ? `${((d.valid / d.total_received) * 100).toFixed(2)}%` : "—"],
                ].map(([k, v]) => (
                  <div key={k as string} className="rounded border border-line p-2">
                    <dt className="text-xs text-ink-3">{k}</dt>
                    <dd className="tabular text-lg font-semibold">{typeof v === "number" ? fmtNum(v) : v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </QueryState>
        </Card>

        <Card className="xl:col-span-2" title="Recent production activity" actions={<Link to="/runs" className="text-xs text-accent hover:underline">All runs →</Link>}>
          <QueryState query={runs} isEmpty={(d) => d.items.length === 0}>
            {(d) => (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="sr-only">Most recently started production runs</caption>
                  <thead className="text-left text-xs text-ink-3">
                    <tr>
                      <th className="py-1 pr-3 font-medium">Run</th>
                      <th className="py-1 pr-3 font-medium">Equipment</th>
                      <th className="py-1 pr-3 font-medium">Started</th>
                      <th className="py-1 pr-3 text-right font-medium">Units</th>
                      <th className="py-1 pr-3 text-right font-medium">Yield</th>
                      <th className="py-1 pr-3 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.items.map((r) => (
                      <tr key={r.id} className="border-t border-line">
                        <td className="py-1.5 pr-3 font-mono text-xs">
                          <Link to={`/runs?selected=${r.id}`} className="hover:underline">
                            {r.run_code}
                          </Link>
                        </td>
                        <td className="py-1.5 pr-3">{r.equipment.code}</td>
                        <td className="py-1.5 pr-3 text-ink-2">{fmtDateTime(r.started_at)}</td>
                        <td className="tabular py-1.5 pr-3 text-right">{fmtNum(r.units_produced)}</td>
                        <td className="tabular py-1.5 pr-3 text-right">{fmtPct(r.yield_pct)}</td>
                        <td className="py-1.5 pr-3">
                          <StatusBadge value={r.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </QueryState>
        </Card>

        <LiveFeed />
      </div>
    </>
  );
}

function LiveFeed() {
  const { feed, status } = useLive();
  return (
    <Card title="Live event feed" description="Alerts, defects, rejections and status changes as they happen">
      {feed.length === 0 ? (
        <p className="text-sm text-ink-3">
          {status === "open" ? "Waiting for events… start the simulator or use Settings → Demo data." : "Live connection is not open."}
        </p>
      ) : (
        <ul className="max-h-72 space-y-1.5 overflow-y-auto text-xs" aria-live="off">
          {feed.map((m, i) => (
            <li key={`${m.ts}-${i}`} className="flex gap-2">
              <span className="tabular shrink-0 text-ink-3">{new Date(m.ts).toLocaleTimeString()}</span>
              <span className="font-medium text-ink-2">{humanize(m.type)}</span>
              <span className="truncate text-ink-3">
                {[m.data.equipment_code ?? m.data.code, m.data.action, m.data.status, m.data.category, m.data.reason ?? m.data.detail].filter(Boolean).join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
