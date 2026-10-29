import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { DataTable, type Column } from "../components/DataTable";
import { Button, Card, DefinitionList, Field, FilterBar, inputCls, PageHeader, QueryState, SelectFilter, SeverityBadge, StatusBadge } from "../components/ui";
import { useLive } from "../hooks/live";
import { usePreferences } from "../hooks/preferences";
import { useToast } from "../hooks/toast";
import { useSearchState } from "../hooks/useSearchState";
import { api } from "../lib/api";
import { fmtDateTime, fmtNum, fmtRelative, humanize } from "../lib/format";
import { METRICS, METRIC_META, type Alert, type AlertDetail } from "../lib/types";

const PAGE_SIZE = 20;
const RULE_TEXT: Record<string, string> = {
  threshold_high: "Reading exceeded a configured high limit for this equipment.",
  threshold_low: "Reading fell below a configured low limit for this equipment.",
  spc_3sigma: "Reading fell outside the Shewhart control limits (baseline mean ± 3σ) while running.",
  machine_fault: "The equipment reported machine_state = fault.",
};

export function AlertsPage() {
  const { clearNewAlerts } = useLive();
  useEffect(() => clearNewAlerts(), [clearNewAlerts]);
  const [f, setF, reset] = useSearchState({ status: "active", severity: "", metric: "", rule: "", equipment_id: "", sort: "-triggered_at", page: "1", selected: "" });
  const page = Number(f.page) || 1;
  const params = { status: f.status === "all" ? "" : f.status, severity: f.severity, metric: f.metric, rule: f.rule, equipment_id: f.equipment_id, sort: f.sort, page, page_size: PAGE_SIZE };
  const list = useQuery({ queryKey: ["alerts", "list", params], queryFn: () => api.alerts(params), placeholderData: keepPreviousData });

  const columns: Column<Alert>[] = [
    { key: "sev", header: "Severity", sortKey: "severity", render: (a) => <SeverityBadge value={a.severity} /> },
    { key: "eq", header: "Equipment", render: (a) => <span className="font-medium">{a.equipment.code}</span> },
    { key: "metric", header: "Metric", render: (a) => humanize(a.metric) },
    { key: "rule", header: "Rule", render: (a) => <span className="text-ink-2">{humanize(a.rule)}</span> },
    { key: "obs", header: "Observed / limit", numeric: true, render: (a) => (a.observed_value === null ? "—" : `${fmtNum(a.observed_value, 2)} / ${fmtNum(a.threshold_value, 2)}`) },
    { key: "count", header: "Occurrences", sortKey: "occurrence_count", numeric: true, render: (a) => fmtNum(a.occurrence_count) },
    { key: "trig", header: "Triggered", sortKey: "triggered_at", render: (a) => <span title={fmtDateTime(a.triggered_at)}>{fmtRelative(a.triggered_at)}</span> },
    { key: "status", header: "Status", render: (a) => <StatusBadge value={a.status} /> },
  ];

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Raised by threshold and SPC rules. One unresolved alert per equipment + metric + rule; repeats increase the occurrence count. Resolved keys are muted during a cooldown period."
      />
      <FilterBar onReset={reset}>
        <SelectFilter
          label="Status"
          value={f.status}
          onChange={(v) => setF({ status: v })}
          allLabel=""
          options={[
            { value: "active", label: "Active (open + acknowledged)" },
            { value: "open", label: "Open" },
            { value: "acknowledged", label: "Acknowledged" },
            { value: "resolved", label: "Resolved" },
            { value: "all", label: "All" },
          ]}
        />
        <SelectFilter label="Severity" value={f.severity} onChange={(v) => setF({ severity: v })} options={["warning", "critical"]} />
        <SelectFilter label="Metric" value={f.metric} onChange={(v) => setF({ metric: v })} options={[...METRICS, "machine_state"]} />
        <SelectFilter label="Rule" value={f.rule} onChange={(v) => setF({ rule: v })} options={Object.keys(RULE_TEXT)} />
        {f.equipment_id && (
          <Button variant="ghost" onClick={() => setF({ equipment_id: "" })}>
            Equipment filter ×
          </Button>
        )}
      </FilterBar>
      <div className="grid gap-4 2xl:grid-cols-[1fr_420px]">
        <DataTable
          caption="Alerts"
          columns={columns}
          rows={list.data?.items}
          rowKey={(a) => a.id}
          loading={list.isPending}
          fetching={list.isFetching && !list.isPending}
          error={list.isError ? list.error : null}
          onRetry={() => list.refetch()}
          emptyTitle={f.status === "active" ? "No active alerts" : "No alerts match"}
          sort={f.sort}
          onSort={(sort) => setF({ sort })}
          onRowClick={(a) => setF({ selected: a.id, page: f.page })}
          rowLabel={(a) => `Show alert ${a.equipment.code} ${a.metric}`}
          selectedKey={f.selected || null}
          page={page}
          pageSize={PAGE_SIZE}
          total={list.data?.total}
          onPage={(p) => setF({ page: String(p) })}
        />
        {f.selected ? (
          <AlertPanel id={f.selected} onClose={() => setF({ selected: "", page: f.page })} />
        ) : (
          <Card title="Alert details">
            <p className="text-sm text-ink-3">Select an alert to see why it fired, its audit history, and to acknowledge or resolve it.</p>
          </Card>
        )}
      </div>
    </>
  );
}

export function AlertPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["alerts", "detail", id], queryFn: () => api.alert(id) });
  return (
    <Card title="Alert details" actions={<Button variant="ghost" onClick={onClose} aria-label="Close alert details">×</Button>}>
      <QueryState query={q}>{(a) => <AlertBody alert={a} />}</QueryState>
    </Card>
  );
}

function AlertBody({ alert: a }: { alert: AlertDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { prefs, update } = usePreferences();
  const [actor, setActor] = useState(prefs.operatorName);
  const [note, setNote] = useState("");
  const [actorError, setActorError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (action: "acknowledge" | "resolve") =>
      action === "acknowledge" ? api.acknowledgeAlert(a.id, { actor: actor.trim(), note: note.trim() || undefined }) : api.resolveAlert(a.id, { actor: actor.trim(), note: note.trim() || undefined }),
    onSuccess: (updated) => {
      qc.setQueryData(["alerts", "detail", a.id], updated);
      void qc.invalidateQueries({ queryKey: ["alerts"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      if (actor.trim() !== prefs.operatorName) update({ operatorName: actor.trim() });
      setNote("");
      toast.success(`Alert ${updated.status}`);
    },
    onError: (err) => toast.error("Alert update failed", err),
  });

  const act = (action: "acknowledge" | "resolve") => {
    if (!actor.trim()) {
      setActorError("Enter your name for the audit trail");
      return;
    }
    setActorError(null);
    mutation.mutate(action);
  };
  const metricMeta = a.metric in METRIC_META ? METRIC_META[a.metric as keyof typeof METRIC_META] : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge value={a.severity} />
        <StatusBadge value={a.status} />
        <Link to={`/equipment/${a.equipment.id}`} className="font-medium text-accent hover:underline">
          {a.equipment.code} · {a.equipment.name}
        </Link>
      </div>

      <div className="rounded border border-line bg-panel-raised p-3 text-sm">
        <p className="text-xs font-semibold text-ink-2">Why this alert fired</p>
        <p className="mt-1">{RULE_TEXT[a.rule]}</p>
        <p className="mt-1 font-mono text-xs text-ink-2">{a.rule_detail}</p>
      </div>

      <DefinitionList
        items={[
          ["Alert ID", <span className="font-mono text-xs">{a.id}</span>],
          ["Metric", humanize(a.metric)],
          ["Observed", a.observed_value === null ? "—" : `${fmtNum(a.observed_value, 2)} ${metricMeta?.unit ?? ""}`],
          ["Limit", a.threshold_value === null ? "—" : `${fmtNum(a.threshold_value, 2)} ${metricMeta?.unit ?? ""}`],
          ["Triggered", fmtDateTime(a.triggered_at)],
          ["Last seen", `${fmtDateTime(a.last_seen_at)} (${a.occurrence_count} occurrence${a.occurrence_count === 1 ? "" : "s"})`],
          ["Acknowledged", fmtDateTime(a.acknowledged_at)],
          ["Resolved", fmtDateTime(a.resolved_at)],
          ["Triggering event", a.event_id ? <span className="font-mono text-xs">{a.event_id}</span> : "—"],
        ]}
      />

      {a.event && (
        <div>
          <p className="mb-1 text-xs font-semibold text-ink-2">Triggering event snapshot</p>
          <DefinitionList
            items={[
              ["Event time", fmtDateTime(a.event.event_time)],
              ["State", humanize(a.event.machine_state)],
              ...METRICS.map((m): [string, string] => [METRIC_META[m].label, `${fmtNum(a.event![METRIC_META[m].field] as number, 2)} ${METRIC_META[m].unit}`]),
            ]}
          />
        </div>
      )}

      <div>
        <p className="mb-1 text-xs font-semibold text-ink-2">Status history</p>
        <ol className="space-y-1.5 border-l border-line pl-3 text-xs">
          {a.history.map((h, i) => (
            <li key={i}>
              <span className="text-ink">{h.from_status ? `${h.from_status} → ${h.to_status}` : `created as ${h.to_status}`}</span>
              <span className="text-ink-3">
                {" "}
                by {h.actor} · {fmtDateTime(h.changed_at)}
              </span>
              {h.note && <p className="text-ink-2">{h.note}</p>}
            </li>
          ))}
        </ol>
      </div>

      {a.status !== "resolved" ? (
        <div className="space-y-2 border-t border-line pt-3">
          <Field label="Your name" htmlFor="alert-actor" error={actorError ?? undefined} hint="Recorded in the audit history (no authentication in this demo).">
            <input id="alert-actor" value={actor} onChange={(e) => setActor(e.target.value)} aria-invalid={Boolean(actorError)} className={inputCls} maxLength={80} />
          </Field>
          <Field label="Note (optional)" htmlFor="alert-note">
            <textarea id="alert-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} className={inputCls} maxLength={2000} />
          </Field>
          <div className="flex gap-2">
            {a.status === "open" && (
              <Button onClick={() => act("acknowledge")} disabled={mutation.isPending}>
                Acknowledge
              </Button>
            )}
            <Button variant="primary" onClick={() => act("resolve")} disabled={mutation.isPending}>
              Resolve
            </Button>
          </div>
        </div>
      ) : (
        <p className="border-t border-line pt-3 text-xs text-ink-3">Resolved alerts are read-only. If the condition recurs after the cooldown, a new alert is opened.</p>
      )}
    </div>
  );
}
