import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { BarList, SERIES } from "../components/charts";
import { DataTable, type Column } from "../components/DataTable";
import { RangeSelect } from "../components/Layout";
import { Card, FilterBar, PageHeader, QueryState, SearchInput, SelectFilter, StatusBadge, dateRangeParams, DateRangeInputs } from "../components/ui";
import { usePreferences, windowFor } from "../hooks/preferences";
import { useDebounce } from "../hooks/useDebounce";
import { useSearchState } from "../hooks/useSearchState";
import { api } from "../lib/api";
import { fmtDateTime, fmtNum, humanize, shortId } from "../lib/format";
import { DEFECT_CATEGORIES, DEFECT_SEVERITIES, DEFECT_STATUSES, type Defect } from "../lib/types";

const PAGE_SIZE = 20;

export function DefectsPage() {
  const navigate = useNavigate();
  const { range } = usePreferences();
  const [f, setF, reset] = useSearchState({ q: "", status: "", category: "", severity: "", station: "", from: "", to: "", sort: "-detected_at", page: "1" });
  const [search, setSearch] = useState(f.q);
  const debounced = useDebounce(search);
  useEffect(() => {
    if (debounced !== f.q) setF({ q: debounced });
  }, [debounced, f.q, setF]);
  useEffect(() => setSearch(f.q), [f.q]);

  const page = Number(f.page) || 1;
  const params = { q: f.q, status: f.status, category: f.category, severity: f.severity, station: f.station, sort: f.sort, page, page_size: PAGE_SIZE, ...dateRangeParams(f.from, f.to) };
  const list = useQuery({ queryKey: ["defects", "list", params], queryFn: () => api.defects(params), placeholderData: keepPreviousData });
  const stations = useQuery({ queryKey: ["equipment", "stations"], queryFn: api.stations, staleTime: 60_000 });
  const agg = useQuery({ queryKey: ["analytics", "defects", range], queryFn: () => api.defectAnalytics(windowFor(range)) });

  const columns: Column<Defect>[] = [
    { key: "id", header: "Defect", render: (d) => <span className="font-mono text-xs">{shortId(d.id)}</span> },
    { key: "detected", header: "Detected", sortKey: "detected_at", render: (d) => fmtDateTime(d.detected_at) },
    { key: "category", header: "Category", sortKey: "category", render: (d) => humanize(d.category) },
    { key: "severity", header: "Severity", sortKey: "severity", render: (d) => <StatusBadge value={d.severity} tone={d.severity === "critical" ? "critical" : undefined} /> },
    { key: "eq", header: "Equipment", render: (d) => d.equipment.code },
    { key: "station", header: "Station", render: (d) => <span className="text-ink-2">{d.equipment.station}</span> },
    { key: "run", header: "Run", render: (d) => <span className="font-mono text-xs text-ink-2">{d.production_run.run_code}</span> },
    { key: "status", header: "Status", sortKey: "status", render: (d) => <StatusBadge value={d.status} /> },
  ];

  return (
    <>
      <PageHeader title="Quality & defects" description="Defects flagged by synthetic inline inspection. Investigations record evidence and notes; no root cause is inferred automatically." />

      <div className="mb-4 grid gap-4 lg:grid-cols-3">
        <Card title="By category" actions={<RangeSelect />}>
          <QueryState query={agg}>
            {(a) => <BarList label="Defect count by category" valueLabel="Defects" data={a.by_category.map((c) => ({ name: c.key, value: c.count }))} />}
          </QueryState>
        </Card>
        <Card title="By station">
          <QueryState query={agg}>
            {(a) => (
              <BarList
                label="Defect count by station"
                valueLabel="Defects"
                caption={a.calculation_notes.defect_rate_pct}
                data={a.by_station.map((s) => ({ name: s.station, value: s.defects, detail: `rate ${s.defect_rate_pct?.toFixed(2) ?? "—"}% of ${fmtNum(s.units_produced)} units` }))}
              />
            )}
          </QueryState>
        </Card>
        <Card title="By severity">
          <QueryState query={agg}>
            {(a) => (
              <BarList
                label="Defect count by severity"
                valueLabel="Defects"
                data={DEFECT_SEVERITIES.map((s) => ({ name: s, value: a.by_severity.find((x) => x.key === s)?.count ?? 0 }))}
                colorFor={() => SERIES[0]}
              />
            )}
          </QueryState>
        </Card>
      </div>

      <FilterBar onReset={reset}>
        <SearchInput value={search} onChange={setSearch} placeholder="Description, equipment, run or batch" label="Search defects" />
        <SelectFilter label="Status" value={f.status} onChange={(v) => setF({ status: v })} options={DEFECT_STATUSES} />
        <SelectFilter label="Category" value={f.category} onChange={(v) => setF({ category: v })} options={DEFECT_CATEGORIES} />
        <SelectFilter label="Severity" value={f.severity} onChange={(v) => setF({ severity: v })} options={DEFECT_SEVERITIES} />
        <SelectFilter label="Station" value={f.station} onChange={(v) => setF({ station: v })} options={stations.data?.map((s) => ({ value: s, label: s })) ?? []} />
        <DateRangeInputs start={f.from} end={f.to} onChange={(from, to) => setF({ from, to })} />
      </FilterBar>
      <DataTable
        caption="Defects"
        columns={columns}
        rows={list.data?.items}
        rowKey={(d) => d.id}
        loading={list.isPending}
        fetching={list.isFetching && !list.isPending}
        error={list.isError ? list.error : null}
        onRetry={() => list.refetch()}
        emptyTitle="No defects match these filters"
        sort={f.sort}
        onSort={(sort) => setF({ sort })}
        onRowClick={(d) => navigate(`/defects/${d.id}`)}
        rowLabel={(d) => `Investigate defect ${shortId(d.id)}`}
        page={page}
        pageSize={PAGE_SIZE}
        total={list.data?.total}
        onPage={(p) => setF({ page: String(p) })}
      />
    </>
  );
}
