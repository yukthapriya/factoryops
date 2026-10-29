import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { BarList, SERIES, StackedTimeBars } from "../components/charts";
import { DataTable, type Column } from "../components/DataTable";
import { Button, Card, FilterBar, KpiCard, Modal, PageHeader, QueryState, SearchInput, SelectFilter } from "../components/ui";
import { usePreferences, windowFor } from "../hooks/preferences";
import { useDebounce } from "../hooks/useDebounce";
import { useSearchState } from "../hooks/useSearchState";
import { api } from "../lib/api";
import { fmtDateTime, fmtNum, humanize, shortId } from "../lib/format";
import { DQ_CATEGORIES, type DataQualityFailure } from "../lib/types";

const PAGE_SIZE = 25;

export function DataQualityPage() {
  const { range } = usePreferences();
  const [f, setF, reset] = useSearchState({ category: "", equipment: "", page: "1" });
  const [eqSearch, setEqSearch] = useState(f.equipment);
  const debounced = useDebounce(eqSearch);
  useEffect(() => {
    if (debounced !== f.equipment) setF({ equipment: debounced });
  }, [debounced, f.equipment, setF]);
  const [inspect, setInspect] = useState<DataQualityFailure | null>(null);

  const summary = useQuery({ queryKey: ["data-quality", "summary", range], queryFn: () => api.dqSummary(windowFor(range)) });
  const page = Number(f.page) || 1;
  const failures = useQuery({
    queryKey: ["data-quality", "failures", range, f],
    queryFn: () => api.dqFailures({ ...windowFor(range), category: f.category, equipment_code: f.equipment, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  const s = summary.data;
  const pct = (n: number | undefined) => (s && s.total_received ? `${(((n ?? 0) / s.total_received) * 100).toFixed(2)}% of received` : undefined);

  const columns: Column<DataQualityFailure>[] = [
    { key: "time", header: "Received", render: (r) => fmtDateTime(r.received_at) },
    { key: "event", header: "Event ID", render: (r) => <span className="font-mono text-xs">{r.event_id ? shortId(r.event_id) : "—"}</span> },
    { key: "eq", header: "Equipment", render: (r) => r.equipment_code ?? "—" },
    { key: "cat", header: "Category", render: (r) => humanize(r.category) },
    { key: "field", header: "Field", render: (r) => <span className="font-mono text-xs text-ink-2">{r.field ?? "—"}</span> },
    { key: "reason", header: "Reason", className: "max-w-md whitespace-normal", render: (r) => <span className="text-ink-2">{r.reason}</span> },
    {
      key: "inspect",
      header: "Payload",
      render: (r) => (
        <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => setInspect(r)} aria-label={`Inspect payload for ${r.event_id ?? "event"}`}>
          Inspect
        </Button>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="Data quality" description="Every received message is accounted for: accepted, accepted-late, rejected with a reason, or ignored as a duplicate." />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <KpiCard label="Received" value={fmtNum(s?.total_received)} loading={summary.isPending} />
        <KpiCard label="Valid" value={fmtNum(s?.valid)} tone="good" sub={pct(s?.valid)} loading={summary.isPending} />
        <KpiCard label="Late / out-of-order" value={fmtNum(s?.valid_late)} sub="stored, flagged late" loading={summary.isPending} />
        <KpiCard label="Invalid" value={fmtNum(s?.invalid)} tone={s?.invalid ? "warning" : undefined} sub={pct(s?.invalid)} loading={summary.isPending} />
        <KpiCard label="Duplicates" value={fmtNum(s?.duplicates)} sub="not stored twice" loading={summary.isPending} />
        <KpiCard
          label="Missing / malformed"
          value={fmtNum(s ? s.by_category.filter((c) => ["missing_field", "malformed", "invalid_type"].includes(c.key)).reduce((n, c) => n + c.count, 0) : undefined)}
          sub={`${fmtNum(s?.by_category.find((c) => c.key === "out_of_range")?.count)} out of range`}
          loading={summary.isPending}
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Ingestion outcomes over time">
          <QueryState query={summary}>
            {(d) => (
              <StackedTimeBars
                label="Messages per time bucket by ingestion outcome"
                caption={`Per-minute counters summed into ${d.bucket_seconds / 60}-minute buckets.`}
                data={d.over_time}
                series={[
                  { key: "accepted", label: "Accepted", color: SERIES[0] },
                  { key: "accepted_late", label: "Accepted late", color: SERIES[1] },
                  { key: "rejected", label: "Rejected", color: SERIES[2] },
                  { key: "duplicate", label: "Duplicate", color: SERIES[3] },
                ]}
              />
            )}
          </QueryState>
        </Card>
        <Card title="Failures by category">
          <QueryState query={summary}>
            {(d) => (
              <BarList
                label="Data quality failures by category"
                valueLabel="Messages"
                data={d.by_category.map((c) => ({ name: c.key, value: c.count }))}
                caption="Includes duplicates. Unknown equipment = not registered or deactivated."
              />
            )}
          </QueryState>
        </Card>
        <Card className="xl:col-span-3" title="Handling policy" description="How the consumer treats each case">
          <QueryState query={summary}>
            {(d) => (
              <dl className="grid gap-3 text-sm md:grid-cols-2">
                {Object.entries(d.policy).map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-xs font-semibold text-ink-2">{humanize(k)}</dt>
                    <dd className="text-ink-3">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </QueryState>
        </Card>
      </div>

      <h2 className="mt-6 mb-2 text-sm font-semibold">Rejected & duplicate events</h2>
      <FilterBar onReset={() => { reset(); setEqSearch(""); }}>
        <SelectFilter label="Category" value={f.category} onChange={(v) => setF({ category: v })} options={DQ_CATEGORIES} />
        <SearchInput value={eqSearch} onChange={setEqSearch} placeholder="Equipment ID" label="Filter by equipment" />
      </FilterBar>
      <DataTable
        caption="Rejected and duplicate events"
        columns={columns}
        rows={failures.data?.items}
        rowKey={(r) => r.id}
        loading={failures.isPending}
        fetching={failures.isFetching && !failures.isPending}
        error={failures.isError ? failures.error : null}
        onRetry={() => failures.refetch()}
        emptyTitle="No rejected events in this time range"
        emptyHint="Try the out_of_range, missing_fields or duplicates scenario from Settings → Demo data."
        page={page}
        pageSize={PAGE_SIZE}
        total={failures.data?.total}
        onPage={(p) => setF({ page: String(p) })}
      />

      <Modal open={inspect !== null} onClose={() => setInspect(null)} title="Rejected event" wide>
        {inspect && (
          <div className="space-y-3 text-sm">
            <p>
              <strong>{humanize(inspect.category)}</strong>: {inspect.reason}
            </p>
            <p className="text-xs text-ink-3">
              Source: <span className="font-mono">{inspect.source ?? "—"}</span> · received {fmtDateTime(inspect.received_at)}
            </p>
            <p className="text-xs text-ink-3">Only known schema fields are stored, with strings truncated to 64 characters; unknown keys are listed by name only.</p>
            <pre className="max-h-80 overflow-auto rounded border border-line bg-page p-3 font-mono text-xs">{JSON.stringify(inspect.payload_excerpt, null, 2)}</pre>
          </div>
        )}
      </Modal>
    </>
  );
}
