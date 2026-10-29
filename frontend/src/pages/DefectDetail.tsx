import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useParams } from "react-router";
import { z } from "zod";
import { SERIES, TrendChart, type RefLine } from "../components/charts";
import { Button, Card, DefinitionList, ErrorState, Field, inputCls, LoadingBlock, PageHeader, SeverityBadge, StatusBadge } from "../components/ui";
import { usePreferences } from "../hooks/preferences";
import { useToast } from "../hooks/toast";
import { api } from "../lib/api";
import { fmtDateTime, fmtNum, humanize, shortId } from "../lib/format";
import { DEFECT_SEVERITIES, METRICS, METRIC_META, type DefectDetail, type DefectStatus, type Metric } from "../lib/types";

// Mirrors backend ALLOWED_TRANSITIONS (services/defects.py).
export const NEXT_STATUSES: Record<DefectStatus, DefectStatus[]> = {
  open: ["investigating", "resolved", "dismissed"],
  investigating: ["open", "resolved", "dismissed"],
  resolved: ["investigating"],
  dismissed: ["investigating"],
};

const updateSchema = z
  .object({
    status: z.string(),
    severity: z.string(),
    author: z.string().trim().min(1, "Your name is required").max(80),
    note: z.string().trim().max(4000),
  })
  .refine((v) => !["resolved", "dismissed"].includes(v.status) || v.note.length > 0, { path: ["note"], message: "Explain the evidence before resolving or dismissing" });

const noteSchema = z.object({
  author: z.string().trim().min(1, "Your name is required").max(80),
  body: z.string().trim().min(1, "Note cannot be empty").max(4000),
});

export function DefectDetailPage() {
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["defects", "detail", id], queryFn: () => api.defect(id) });
  if (q.isPending) return <LoadingBlock rows={8} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} title="Could not load defect" />;
  return <DefectInvestigation defect={q.data} />;
}

function DefectInvestigation({ defect: d }: { defect: DefectDetail }) {
  const [metric, setMetric] = useState<Metric>("temperature");
  const meta = METRIC_META[metric];
  const t = d.evidence.thresholds.find((x) => x.metric === metric);
  const refs: RefLine[] = [];
  if (t?.warning_high != null) refs.push({ y: t.warning_high, label: "Warning limit", color: "#fab219" });
  if (t?.critical_high != null) refs.push({ y: t.critical_high, label: "Critical limit", color: "#d03b3b" });
  if (t?.warning_low != null) refs.push({ y: t.warning_low, label: "Warning low", color: "#fab219" });
  const src = d.evidence.source_event;

  return (
    <>
      <p className="mb-2 text-xs text-ink-3">
        <Link to="/defects" className="hover:underline">
          Quality & defects
        </Link>{" "}
        / {shortId(d.id)}
      </p>
      <PageHeader
        title={`${humanize(d.category)} defect · ${d.equipment.code}`}
        description={d.description}
        actions={
          <div className="flex items-center gap-2">
            <SeverityBadge value={d.severity} />
            <StatusBadge value={d.status} />
          </div>
        }
      />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Original record">
          <DefinitionList
            items={[
              ["Defect ID", <span className="font-mono text-xs">{d.id}</span>],
              ["Detected", fmtDateTime(d.detected_at)],
              ["Category", humanize(d.category)],
              ["Severity", humanize(d.severity)],
              ["Equipment", <Link to={`/equipment/${d.equipment.id}`} className="text-accent hover:underline">{d.equipment.code} · {d.equipment.name}</Link>],
              ["Station", d.equipment.station],
              ["Production run", <Link to={`/runs?selected=${d.production_run.id}`} className="font-mono text-xs text-accent hover:underline">{d.production_run.run_code}</Link>],
              ["Batch", d.production_run.batch_id],
              ["Source event", d.source_event_id ? <span className="font-mono text-xs">{d.source_event_id}</span> : "—"],
              ["Last updated", fmtDateTime(d.updated_at)],
            ]}
          />
          {src && (
            <>
              <h3 className="mt-4 mb-1 text-xs font-semibold text-ink-2">Telemetry at detection</h3>
              <DefinitionList
                items={[
                  ["Machine state", humanize(src.machine_state)],
                  ...METRICS.map((m): [string, string] => [METRIC_META[m].label, `${fmtNum(src[METRIC_META[m].field] as number, 2)} ${METRIC_META[m].unit}`]),
                  ["Late event", src.is_late ? "yes" : "no"],
                ]}
              />
            </>
          )}
        </Card>

        <Card
          className="xl:col-span-2"
          title="Evidence: telemetry around detection"
          description={`${fmtDateTime(d.evidence.window_start)} – ${fmtDateTime(d.evidence.window_end)} on ${d.equipment.code} (${d.evidence.telemetry.length} readings)`}
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
          <TrendChart
            label={`${meta.label} readings in the 30 minutes around the defect`}
            caption="Raw readings with this unit's alert limits. Evidence only — correlation with the defect is for the investigator to judge."
            data={d.evidence.telemetry as unknown as Record<string, unknown>[]}
            xKey="event_time"
            unit={meta.unit}
            series={[{ key: meta.field, label: meta.label, color: SERIES[0] }]}
            refLines={refs}
          />
          <h3 className="mt-4 mb-1 text-xs font-semibold text-ink-2">Alerts on this equipment in the window ({d.evidence.alerts.length})</h3>
          {d.evidence.alerts.length === 0 ? (
            <p className="text-sm text-ink-3">No alerts overlapped the evidence window.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {d.evidence.alerts.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-2">
                  <SeverityBadge value={a.severity} />
                  <Link to={`/alerts?selected=${a.id}`} className="hover:underline">
                    {humanize(a.metric)} · {humanize(a.rule)}
                  </Link>
                  <span className="text-xs text-ink-3">
                    observed {fmtNum(a.observed_value, 2)} at {fmtDateTime(a.triggered_at)}
                  </span>
                  <StatusBadge value={a.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <StatusForm defect={d} />
        <NotesPanel defect={d} />
      </div>
    </>
  );
}

function StatusForm({ defect: d }: { defect: DefectDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { prefs, update } = usePreferences();
  const form = useForm({ resolver: zodResolver(updateSchema), defaultValues: { status: d.status, severity: d.severity, author: prefs.operatorName, note: "" } });
  const mutation = useMutation({
    mutationFn: (v: z.output<typeof updateSchema>) =>
      api.updateDefect(d.id, {
        status: v.status !== d.status ? v.status : undefined,
        severity: v.severity !== d.severity ? v.severity : undefined,
        author: v.author,
        note: v.note || undefined,
      }),
    onSuccess: (updated, v) => {
      qc.setQueryData(["defects", "detail", d.id], updated);
      void qc.invalidateQueries({ queryKey: ["defects"] });
      if (v.author !== prefs.operatorName) update({ operatorName: v.author });
      form.reset({ status: updated.status, severity: updated.severity, author: v.author, note: "" });
      toast.success("Investigation updated", `Status: ${humanize(updated.status)}`);
    },
    onError: (err) => toast.error("Update failed", err),
  });
  const errs = form.formState.errors;
  const status = form.watch("status");
  const severity = form.watch("severity");
  const unchanged = status === d.status && severity === d.severity;

  return (
    <Card title="Investigation status">
      <form noValidate className="space-y-3" onSubmit={form.handleSubmit((v) => mutation.mutate(v))}>
        <Field label="Status" htmlFor="df-status" hint={`Allowed from ${d.status}: ${NEXT_STATUSES[d.status].join(", ")}`}>
          <select id="df-status" {...form.register("status")} className={inputCls}>
            {[d.status, ...NEXT_STATUSES[d.status]].map((s) => (
              <option key={s} value={s}>
                {humanize(s)}
                {s === d.status ? " (current)" : ""}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Severity" htmlFor="df-sev">
          <select id="df-sev" {...form.register("severity")} className={inputCls}>
            {DEFECT_SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {humanize(s)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Your name" htmlFor="df-author" error={errs.author?.message} hint="Recorded for attribution; this demo has no authentication.">
          <input id="df-author" {...form.register("author")} aria-invalid={Boolean(errs.author)} className={inputCls} />
        </Field>
        <Field label="Note / evidence" htmlFor="df-note" error={errs.note?.message}>
          <textarea id="df-note" rows={3} {...form.register("note")} aria-invalid={Boolean(errs.note)} className={inputCls} />
        </Field>
        <Button type="submit" variant="primary" disabled={mutation.isPending || unchanged}>
          {mutation.isPending ? "Saving…" : "Update investigation"}
        </Button>
      </form>
    </Card>
  );
}

function NotesPanel({ defect: d }: { defect: DefectDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { prefs } = usePreferences();
  const form = useForm({ resolver: zodResolver(noteSchema), defaultValues: { author: prefs.operatorName, body: "" } });
  const mutation = useMutation({
    mutationFn: (v: z.output<typeof noteSchema>) => api.addDefectNote(d.id, v),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["defects", "detail", d.id] });
      form.reset({ author: form.getValues("author"), body: "" });
      toast.success("Note added");
    },
    onError: (err) => toast.error("Could not add note", err),
  });
  const errs = form.formState.errors;
  return (
    <Card className="xl:col-span-2" title={`Investigation notes (${d.notes.length})`}>
      {d.notes.length === 0 ? (
        <p className="mb-3 text-sm text-ink-3">No notes yet.</p>
      ) : (
        <ol className="mb-4 space-y-3">
          {d.notes.map((n) => (
            <li key={n.id} className="rounded border border-line p-3">
              <p className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
                <span className="font-medium text-ink-2">{n.author}</span>
                <time dateTime={n.created_at}>{fmtDateTime(n.created_at)}</time>
                {n.status_to && (
                  <span>
                    status {n.status_from} → <strong className="text-ink">{n.status_to}</strong>
                  </span>
                )}
              </p>
              <p className="mt-1 text-sm whitespace-pre-wrap">{n.body}</p>
            </li>
          ))}
        </ol>
      )}
      <form noValidate className="grid gap-3 sm:grid-cols-[200px_1fr_auto] sm:items-start" onSubmit={form.handleSubmit((v) => mutation.mutate(v))}>
        <Field label="Your name" htmlFor="note-author" error={errs.author?.message}>
          <input id="note-author" {...form.register("author")} aria-invalid={Boolean(errs.author)} className={inputCls} />
        </Field>
        <Field label="Add a note" htmlFor="note-body" error={errs.body?.message}>
          <textarea id="note-body" rows={2} {...form.register("body")} aria-invalid={Boolean(errs.body)} className={inputCls} />
        </Field>
        <Button type="submit" variant="primary" className="sm:mt-5" disabled={mutation.isPending}>
          {mutation.isPending ? "Adding…" : "Add note"}
        </Button>
      </form>
    </Card>
  );
}
