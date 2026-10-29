import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ControlChartView, SERIES, TrendChart, type RefLine } from "../components/charts";
import { EquipmentFormModal } from "../components/EquipmentForm";
import { Button, Card, ConfirmDialog, DefinitionList, ErrorState, LoadingBlock, PageHeader, QueryState, SeverityBadge, StatusBadge } from "../components/ui";
import { RangeSelect } from "../components/Layout";
import { usePreferences, windowFor } from "../hooks/preferences";
import { useToast } from "../hooks/toast";
import { api } from "../lib/api";
import { fmtDateTime, fmtNum, fmtPct, fmtRelative, humanize } from "../lib/format";
import { METRICS, METRIC_META, type Metric, type Threshold } from "../lib/types";

function refLines(t: Threshold | undefined): RefLine[] {
  if (!t) return [];
  const out: RefLine[] = [];
  if (t.warning_high !== null) out.push({ y: t.warning_high, label: "Warning limit", color: "#fab219" });
  if (t.critical_high !== null) out.push({ y: t.critical_high, label: "Critical limit", color: "#d03b3b" });
  if (t.warning_low !== null) out.push({ y: t.warning_low, label: "Warning low limit", color: "#fab219" });
  if (t.critical_low !== null) out.push({ y: t.critical_low, label: "Critical low limit", color: "#d03b3b" });
  return out;
}

export function EquipmentDetailPage() {
  const { id = "" } = useParams();
  const { range } = usePreferences();
  const qc = useQueryClient();
  const toast = useToast();
  const [metric, setMetric] = useState<Metric>("temperature");
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const eq = useQuery({ queryKey: ["equipment", id], queryFn: () => api.equipment(id) });
  const recent = useQuery({ queryKey: ["telemetry", "recent", id], queryFn: () => api.telemetry({ equipment_id: id, limit: 120, ...windowFor("24h") }) });
  const trend = useQuery({ queryKey: ["telemetry", "summary", id, range], queryFn: () => api.telemetrySummary({ equipment_id: id, ...windowFor(range) }) });
  const chart = useQuery({
    queryKey: ["analytics", "control", id, metric, range],
    queryFn: () => api.controlChart({ equipment_id: id, metric, ...windowFor(range === "7d" ? "7d" : range) }),
  });
  const alerts = useQuery({ queryKey: ["alerts", "equipment", id], queryFn: () => api.alerts({ equipment_id: id, page_size: 8 }) });
  const runs = useQuery({ queryKey: ["runs", "equipment", id], queryFn: () => api.runs({ equipment_id: id, page_size: 8 }) });

  const deactivate = useMutation({
    mutationFn: () => api.deactivateEquipment(id),
    onSuccess: (e) => {
      void qc.invalidateQueries({ queryKey: ["equipment"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success(`${e.code} deactivated`, "Its history is kept; new telemetry for it will be rejected.");
      setConfirming(false);
    },
    onError: (err) => toast.error("Could not deactivate equipment", err),
  });

  const reactivate = useMutation({
    mutationFn: () => api.updateEquipment(id, { is_active: true, status: "idle" }),
    onSuccess: (e) => {
      void qc.invalidateQueries({ queryKey: ["equipment"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success(`${e.code} reactivated`);
    },
    onError: (err) => toast.error("Could not reactivate equipment", err),
  });

  if (eq.isPending) return <LoadingBlock rows={8} />;
  if (eq.isError) return <ErrorState error={eq.error} onRetry={() => eq.refetch()} title="Could not load equipment" />;
  const e = eq.data;
  const meta = METRIC_META[metric];
  const threshold = e.thresholds.find((t) => t.metric === metric);
  const latest = recent.data?.[0];

  return (
    <>
      <p className="mb-2 text-xs text-ink-3">
        <Link to="/equipment" className="hover:underline">
          Equipment
        </Link>{" "}
        / {e.code}
      </p>
      <PageHeader
        title={`${e.code} · ${e.name}`}
        description={`${e.station} · ${e.equipment_type}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <RangeSelect />
            <Button onClick={() => setEditing(true)}>Edit</Button>
            {e.is_active ? (
              <Button variant="danger" onClick={() => setConfirming(true)}>
                Deactivate
              </Button>
            ) : (
              <Button variant="primary" onClick={() => reactivate.mutate()} disabled={reactivate.isPending}>
                Reactivate
              </Button>
            )}
          </div>
        }
      />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Details">
          <DefinitionList
            items={[
              ["Status", e.is_active ? <StatusBadge value={e.status} /> : <StatusBadge value="deactivated" tone="neutral" />],
              ["Last telemetry", e.last_event_at ? `${fmtDateTime(e.last_event_at)} (${fmtRelative(e.last_event_at)})` : "none"],
              ["Created", fmtDateTime(e.created_at)],
              ["Updated", fmtDateTime(e.updated_at)],
              ["Internal ID", <span className="font-mono text-xs">{e.id}</span>],
            ]}
          />
          <h3 className="mt-4 mb-1 text-xs font-semibold text-ink-2">Latest reading</h3>
          {latest ? (
            <DefinitionList
              items={[
                ["Time", fmtDateTime(latest.event_time)],
                ["State", humanize(latest.machine_state)],
                ...METRICS.map((m): [string, string] => [METRIC_META[m].label, `${fmtNum(latest[METRIC_META[m].field] as number, 2)} ${METRIC_META[m].unit}`]),
              ]}
            />
          ) : (
            <p className="text-sm text-ink-3">{recent.isPending ? "Loading…" : "No telemetry in the last 24 hours."}</p>
          )}
          <h3 className="mt-4 mb-1 text-xs font-semibold text-ink-2">Alert thresholds</h3>
          {e.thresholds.length === 0 ? (
            <p className="text-sm text-ink-3">No thresholds configured.</p>
          ) : (
            <ul className="space-y-1 text-xs text-ink-2">
              {e.thresholds.map((t) => (
                <li key={t.metric}>
                  <span className="text-ink">{METRIC_META[t.metric].label}</span>:{" "}
                  {[
                    t.critical_low !== null && `crit < ${t.critical_low}`,
                    t.warning_low !== null && `warn < ${t.warning_low}`,
                    t.warning_high !== null && `warn > ${t.warning_high}`,
                    t.critical_high !== null && `crit > ${t.critical_high}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}{" "}
                  {METRIC_META[t.metric].unit}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          className="xl:col-span-2"
          title={`${meta.label} trend`}
          actions={
            <div role="tablist" aria-label="Metric" className="flex gap-1">
              {METRICS.map((m) => (
                <button key={m} role="tab" aria-selected={metric === m} onClick={() => setMetric(m)} className={`rounded px-2 py-1 text-xs ${metric === m ? "bg-accent/20 text-ink" : "text-ink-3 hover:text-ink"}`}>
                  {METRIC_META[m].label}
                </button>
              ))}
            </div>
          }
        >
          <QueryState query={trend}>
            {(t) => (
              <TrendChart
                label={`${e.code} ${meta.label.toLowerCase()} trend with alert thresholds`}
                caption={`Bucket averages (${t.bucket_seconds / 60} min). Dashed lines are this unit's configured alert limits.`}
                data={t.buckets as unknown as Record<string, unknown>[]}
                xKey="bucket"
                unit={meta.unit}
                series={[{ key: meta.avgKey, label: `Average ${meta.label.toLowerCase()}`, color: SERIES[0] }]}
                refLines={refLines(threshold)}
              />
            )}
          </QueryState>
        </Card>

        <Card className="xl:col-span-2" title={`Statistical process control · ${meta.label}`} description="Shewhart individuals chart on running-state readings (latest 500 in range)">
          <QueryState query={chart}>
            {(c) =>
              c.baseline_source === "insufficient_data" ? (
                <p className="text-sm text-ink-3">{c.explanation}</p>
              ) : (
                <>
                  <ControlChartView
                    label={`${e.code} ${meta.label.toLowerCase()} control chart`}
                    points={c.points}
                    unit={c.unit}
                    cl={c.center_line}
                    ucl={c.ucl}
                    lcl={c.lcl}
                    caption={
                      <>
                        {c.method}. CL {fmtNum(c.center_line, 2)}, UCL {fmtNum(c.ucl, 2)}, LCL {fmtNum(c.lcl, 2)} {c.unit} (σ={fmtNum(c.sigma, 3)}). {c.explanation}{" "}
                        {c.points.filter((p) => p.out_of_control).length} of {c.points.length} points flagged.
                      </>
                    }
                  />
                </>
              )
            }
          </QueryState>
        </Card>

        <Card title="Alert history" actions={<Link to={`/alerts?equipment_id=${e.id}`} className="text-xs text-accent hover:underline">All →</Link>}>
          <QueryState query={alerts} isEmpty={(d) => d.items.length === 0} empty={<p className="text-sm text-ink-3">No alerts for this equipment.</p>}>
            {(d) => (
              <ul className="divide-y divide-line text-sm">
                {d.items.map((a) => (
                  <li key={a.id} className="py-2">
                    <div className="flex items-center gap-2">
                      <SeverityBadge value={a.severity} />
                      <Link to={`/alerts?selected=${a.id}`} className="font-medium hover:underline">
                        {humanize(a.metric)} · {humanize(a.rule)}
                      </Link>
                      <span className="ml-auto">
                        <StatusBadge value={a.status} />
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-ink-3">
                      {fmtDateTime(a.triggered_at)} · ×{a.occurrence_count}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </Card>

        <Card className="xl:col-span-3" title="Related production runs" actions={<Link to={`/runs?equipment_id=${e.id}`} className="text-xs text-accent hover:underline">All →</Link>}>
          <QueryState query={runs} isEmpty={(d) => d.items.length === 0} empty={<p className="text-sm text-ink-3">No production runs recorded.</p>}>
            {(d) => (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <caption className="sr-only">Production runs on {e.code}</caption>
                  <thead className="text-left text-xs text-ink-3">
                    <tr>
                      <th className="py-1 pr-3 font-medium">Run</th>
                      <th className="py-1 pr-3 font-medium">Started</th>
                      <th className="py-1 pr-3 font-medium">Ended</th>
                      <th className="py-1 pr-3 text-right font-medium">Units</th>
                      <th className="py-1 pr-3 text-right font-medium">Defects</th>
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
                        <td className="py-1.5 pr-3 text-ink-2">{fmtDateTime(r.started_at)}</td>
                        <td className="py-1.5 pr-3 text-ink-2">{fmtDateTime(r.ended_at)}</td>
                        <td className="tabular py-1.5 pr-3 text-right">{fmtNum(r.units_produced)}</td>
                        <td className="tabular py-1.5 pr-3 text-right">{fmtNum(r.defect_count)}</td>
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
      </div>

      <EquipmentFormModal open={editing} onClose={() => setEditing(false)} equipment={e} />
      <ConfirmDialog
        open={confirming}
        danger
        title={`Deactivate ${e.code}?`}
        confirmLabel="Deactivate"
        pending={deactivate.isPending}
        onCancel={() => setConfirming(false)}
        onConfirm={() => deactivate.mutate()}
        message={
          <>
            <p>The unit is hidden from active lists and set to offline. Telemetry, runs, alerts and defects are retained for traceability.</p>
            <p className="mt-2">New telemetry from {e.code} will be rejected as "unknown equipment" until it is reactivated.</p>
          </>
        }
      />
    </>
  );
}
