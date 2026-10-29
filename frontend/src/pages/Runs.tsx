import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { DataTable, type Column } from "../components/DataTable";
import { Button, Card, dateRangeParams, DateRangeInputs, DefinitionList, FilterBar, PageHeader, QueryState, SearchInput, SelectFilter, StatusBadge } from "../components/ui";
import { useDebounce } from "../hooks/useDebounce";
import { useSearchState } from "../hooks/useSearchState";
import { api } from "../lib/api";
import { fmtDateTime, fmtNum, fmtPct, humanize, shortId } from "../lib/format";
import type { Run } from "../lib/types";

const PAGE_SIZE = 20;

function duration(r: Run): string {
  const end = r.ended_at ? new Date(r.ended_at).getTime() : Date.now();
  const mins = Math.round((end - new Date(r.started_at).getTime()) / 60000);
  return mins < 60 ? `${mins} min` : `${(mins / 60).toFixed(1)} h`;
}

export function RunsPage() {
  const [f, setF, reset] = useSearchState({ q: "", status: "", station: "", equipment_id: "", from: "", to: "", sort: "-started_at", page: "1", selected: "" });
  const [search, setSearch] = useState(f.q);
  const debounced = useDebounce(search);
  useEffect(() => {
    if (debounced !== f.q) setF({ q: debounced });
  }, [debounced, f.q, setF]);
  useEffect(() => setSearch(f.q), [f.q]);

  const page = Number(f.page) || 1;
  const params = { q: f.q, status: f.status, station: f.station, equipment_id: f.equipment_id, sort: f.sort, page, page_size: PAGE_SIZE, ...dateRangeParams(f.from, f.to) };
  const list = useQuery({ queryKey: ["runs", "list", params], queryFn: () => api.runs(params), placeholderData: keepPreviousData });
  const stations = useQuery({ queryKey: ["equipment", "stations"], queryFn: api.stations, staleTime: 60_000 });

  const columns: Column<Run>[] = [
    { key: "code", header: "Run", sortKey: "run_code", render: (r) => <span className="font-mono text-xs">{r.run_code}</span> },
    { key: "batch", header: "Batch", render: (r) => <span className="font-mono text-xs text-ink-2">{r.batch_id}</span> },
    { key: "eq", header: "Equipment", render: (r) => `${r.equipment.code}` },
    { key: "station", header: "Station", render: (r) => <span className="text-ink-2">{r.equipment.station}</span> },
    { key: "start", header: "Started", sortKey: "started_at", render: (r) => fmtDateTime(r.started_at) },
    { key: "dur", header: "Duration", render: (r) => <span className="text-ink-2">{duration(r)}</span> },
    { key: "units", header: "Units", sortKey: "units_produced", numeric: true, render: (r) => fmtNum(r.units_produced) },
    { key: "defects", header: "Defects", sortKey: "defect_count", numeric: true, render: (r) => fmtNum(r.defect_count) },
    { key: "yield", header: "Yield", numeric: true, render: (r) => fmtPct(r.yield_pct) },
    { key: "status", header: "Status", render: (r) => <StatusBadge value={r.status} /> },
  ];

  return (
    <>
      <PageHeader title="Production runs" description="One run per batch per equipment unit. Yield = (units − defects) ÷ units." />
      <FilterBar onReset={reset}>
        <SearchInput value={search} onChange={setSearch} placeholder="Run, batch or equipment ID" label="Search runs" />
        <SelectFilter label="Status" value={f.status} onChange={(v) => setF({ status: v })} options={["running", "completed", "aborted"]} />
        <SelectFilter label="Station" value={f.station} onChange={(v) => setF({ station: v })} options={stations.data?.map((s) => ({ value: s, label: s })) ?? []} />
        <DateRangeInputs start={f.from} end={f.to} onChange={(from, to) => setF({ from, to })} />
        {f.equipment_id && (
          <Button variant="ghost" onClick={() => setF({ equipment_id: "" })}>
            Equipment filter ×
          </Button>
        )}
      </FilterBar>
      <div className="grid gap-4 2xl:grid-cols-[1fr_380px]">
        <DataTable
          caption="Production runs"
          columns={columns}
          rows={list.data?.items}
          rowKey={(r) => r.id}
          loading={list.isPending}
          fetching={list.isFetching && !list.isPending}
          error={list.isError ? list.error : null}
          onRetry={() => list.refetch()}
          emptyTitle="No production runs match"
          sort={f.sort}
          onSort={(sort) => setF({ sort })}
          onRowClick={(r) => setF({ selected: r.id, page: f.page })}
          rowLabel={(r) => `Show details for run ${r.run_code}`}
          selectedKey={f.selected || null}
          page={page}
          pageSize={PAGE_SIZE}
          total={list.data?.total}
          onPage={(p) => setF({ page: String(p) })}
        />
        {f.selected ? <RunDetailPanel id={f.selected} onClose={() => setF({ selected: "", page: f.page })} /> : (
          <Card title="Run details">
            <p className="text-sm text-ink-3">Select a run to see its telemetry summary, defects and calculations.</p>
          </Card>
        )}
      </div>
    </>
  );
}

function RunDetailPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["runs", "detail", id], queryFn: () => api.run(id) });
  return (
    <Card title="Run details" actions={<Button variant="ghost" onClick={onClose} aria-label="Close run details">×</Button>}>
      <QueryState query={q}>
        {(r) => (
          <div className="space-y-4">
            <DefinitionList
              items={[
                ["Run", <span className="font-mono text-xs">{r.run_code}</span>],
                ["Batch", r.batch_id],
                ["Equipment", <Link to={`/equipment/${r.equipment.id}`} className="text-accent hover:underline">{r.equipment.code} · {r.equipment.name}</Link>],
                ["Status", <StatusBadge value={r.status} />],
                ["Started", fmtDateTime(r.started_at)],
                ["Ended", r.ended_at ? fmtDateTime(r.ended_at) : "in progress"],
              ]}
            />
            <div className="rounded border border-line p-3 text-sm">
              <p className="mb-1 text-xs font-semibold text-ink-2">Calculations</p>
              <p>
                Yield = ({fmtNum(r.units_produced)} − {fmtNum(r.defect_count)}) ÷ {fmtNum(r.units_produced)} = <strong>{fmtPct(r.yield_pct)}</strong>
              </p>
              <p>
                Defect rate = {fmtNum(r.defect_count)} ÷ {fmtNum(r.units_produced)} = <strong>{fmtPct(r.defect_rate_pct)}</strong>
              </p>
              {r.units_produced === 0 && <p className="mt-1 text-xs text-ink-3">No units produced yet, so rates are undefined.</p>}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold text-ink-2">Telemetry during run ({fmtNum(r.telemetry.event_count)} events)</p>
              <DefinitionList
                items={[
                  ["Temperature", `avg ${fmtNum(r.telemetry.avg_temperature_c, 1)} / max ${fmtNum(r.telemetry.max_temperature_c, 1)} °C`],
                  ["Vibration", `avg ${fmtNum(r.telemetry.avg_vibration_mm_s, 2)} / max ${fmtNum(r.telemetry.max_vibration_mm_s, 2)} mm/s`],
                  ["Pressure", `avg ${fmtNum(r.telemetry.avg_pressure_bar, 2)} bar`],
                  ["Cycle time", `avg ${fmtNum(r.telemetry.avg_cycle_time_s, 1)} s`],
                ]}
              />
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold text-ink-2">Defects ({r.defects.length})</p>
              {r.defects.length === 0 ? (
                <p className="text-sm text-ink-3">No defects recorded for this run.</p>
              ) : (
                <ul className="space-y-1 text-sm">
                  {r.defects.map((d) => (
                    <li key={d.id} className="flex items-center gap-2">
                      <Link to={`/defects/${d.id}`} className="font-mono text-xs text-accent hover:underline">
                        {shortId(d.id)}
                      </Link>
                      <span>{humanize(d.category)}</span>
                      <StatusBadge value={d.severity} />
                      <span className="ml-auto">
                        <StatusBadge value={d.status} />
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </QueryState>
    </Card>
  );
}
