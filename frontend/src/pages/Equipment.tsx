import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { DataTable, type Column } from "../components/DataTable";
import { EquipmentFormModal } from "../components/EquipmentForm";
import { Button, FilterBar, PageHeader, SearchInput, SelectFilter, StatusBadge } from "../components/ui";
import { useDebounce } from "../hooks/useDebounce";
import { useSearchState } from "../hooks/useSearchState";
import { api } from "../lib/api";
import { fmtRelative } from "../lib/format";
import { EQUIPMENT_STATUSES, type Equipment } from "../lib/types";

const PAGE_SIZE = 20;

export function EquipmentPage() {
  const navigate = useNavigate();
  const [f, setF, reset] = useSearchState({ q: "", status: "", station: "", sort: "code", page: "1", inactive: "" });
  const [search, setSearch] = useState(f.q);
  const debounced = useDebounce(search);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (debounced !== f.q) setF({ q: debounced });
  }, [debounced, f.q, setF]);
  useEffect(() => setSearch(f.q), [f.q]);

  const page = Number(f.page) || 1;
  const params = { q: f.q, status: f.status, station: f.station, sort: f.sort, page, page_size: PAGE_SIZE, include_inactive: f.inactive === "1" || undefined };
  const list = useQuery({ queryKey: ["equipment", "list", params], queryFn: () => api.equipmentList(params), placeholderData: keepPreviousData });
  const stations = useQuery({ queryKey: ["equipment", "stations"], queryFn: api.stations, staleTime: 60_000 });

  const columns: Column<Equipment>[] = [
    { key: "code", header: "ID", sortKey: "code", render: (e) => <span className="font-mono text-xs font-medium">{e.code}</span> },
    { key: "name", header: "Name", sortKey: "name", render: (e) => e.name },
    { key: "station", header: "Station", sortKey: "station", render: (e) => e.station },
    { key: "type", header: "Type", render: (e) => <span className="text-ink-2">{e.equipment_type}</span> },
    { key: "status", header: "Status", sortKey: "status", render: (e) => (e.is_active ? <StatusBadge value={e.status} /> : <StatusBadge value="deactivated" tone="neutral" />) },
    { key: "thresholds", header: "Thresholds", render: (e) => <span className="text-ink-3">{e.thresholds.length} metrics</span> },
    { key: "last", header: "Last telemetry", sortKey: "last_event_at", render: (e) => <span className="text-ink-2">{fmtRelative(e.last_event_at)}</span> },
  ];

  return (
    <>
      <PageHeader
        title="Equipment"
        description="Registered simulated equipment, operational status and alert thresholds."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            + Add equipment
          </Button>
        }
      />
      <FilterBar onReset={reset}>
        <SearchInput value={search} onChange={setSearch} placeholder="Search ID, name or type" label="Search equipment" />
        <SelectFilter label="Status" value={f.status} onChange={(v) => setF({ status: v })} options={EQUIPMENT_STATUSES} />
        <SelectFilter label="Station" value={f.station} onChange={(v) => setF({ station: v })} options={stations.data?.map((s) => ({ value: s, label: s })) ?? []} />
        <label className="flex items-center gap-1.5 text-xs text-ink-3">
          <input type="checkbox" checked={f.inactive === "1"} onChange={(e) => setF({ inactive: e.target.checked ? "1" : "" })} />
          Include deactivated
        </label>
      </FilterBar>
      <DataTable
        caption="Equipment list"
        columns={columns}
        rows={list.data?.items}
        rowKey={(e) => e.id}
        loading={list.isPending}
        fetching={list.isFetching && !list.isPending}
        error={list.isError ? list.error : null}
        onRetry={() => list.refetch()}
        emptyTitle="No equipment matches these filters"
        emptyHint="Clear filters, or seed demo data with `make seed`."
        sort={f.sort}
        onSort={(sort) => setF({ sort })}
        onRowClick={(e) => navigate(`/equipment/${e.id}`)}
        rowLabel={(e) => `Open ${e.code} ${e.name}`}
        page={page}
        pageSize={PAGE_SIZE}
        total={list.data?.total}
        onPage={(p) => setF({ page: String(p) })}
      />
      <EquipmentFormModal open={creating} onClose={() => setCreating(false)} onSaved={(e) => navigate(`/equipment/${e.id}`)} />
    </>
  );
}
