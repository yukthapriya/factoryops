import type { ReactNode } from "react";
import { usePreferences } from "../hooks/preferences";
import { Button, EmptyState, ErrorState, Skeleton } from "./ui";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  sortKey?: string;
  className?: string;
  numeric?: boolean;
}

interface Props<T> {
  caption: string;
  columns: Column<T>[];
  rows: T[] | undefined;
  rowKey: (row: T) => string;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyHint?: ReactNode;
  sort?: string;
  onSort?: (sort: string) => void;
  onRowClick?: (row: T) => void;
  rowLabel?: (row: T) => string;
  selectedKey?: string | null;
  page?: number;
  pageSize?: number;
  total?: number;
  onPage?: (page: number) => void;
  fetching?: boolean;
}

export function DataTable<T>(p: Props<T>) {
  const { prefs } = usePreferences();
  const sortField = p.sort?.replace(/^-/, "");
  const desc = p.sort?.startsWith("-");

  return (
    <div data-density={prefs.density}>
      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full min-w-max border-collapse text-sm">
          <caption className="sr-only">{p.caption}</caption>
          <thead className="bg-panel-raised text-left text-xs text-ink-3">
            <tr>
              {p.columns.map((c) => {
                const active = c.sortKey && c.sortKey === sortField;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    aria-sort={active ? (desc ? "descending" : "ascending") : undefined}
                    className={`border-b border-line px-3 py-2 font-medium ${c.numeric ? "text-right" : ""}`}
                  >
                    {c.sortKey && p.onSort ? (
                      <button
                        className="inline-flex items-center gap-1 hover:text-ink"
                        onClick={() => p.onSort?.(active && !desc ? `-${c.sortKey}` : (c.sortKey as string))}
                      >
                        {c.header}
                        <span aria-hidden className="text-[0.6rem]">
                          {active ? (desc ? "▼" : "▲") : "↕"}
                        </span>
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className={p.fetching ? "opacity-70" : ""}>
            {p.loading &&
              Array.from({ length: 6 }, (_, i) => (
                <tr key={`sk-${i}`}>
                  {p.columns.map((c) => (
                    <td key={c.key} className="border-b border-line px-3 py-2.5">
                      <Skeleton className="h-4 w-full" />
                    </td>
                  ))}
                </tr>
              ))}
            {!p.loading &&
              p.rows?.map((row) => {
                const key = p.rowKey(row);
                const clickable = Boolean(p.onRowClick);
                return (
                  <tr
                    key={key}
                    onClick={clickable ? () => p.onRowClick?.(row) : undefined}
                    onKeyDown={clickable ? (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), p.onRowClick?.(row)) : undefined}
                    tabIndex={clickable ? 0 : undefined}
                    aria-label={clickable ? p.rowLabel?.(row) : undefined}
                    aria-selected={p.selectedKey === undefined ? undefined : p.selectedKey === key}
                    className={`${clickable ? "cursor-pointer hover:bg-panel-raised" : ""} ${p.selectedKey === key ? "bg-accent/10" : ""}`}
                  >
                    {p.columns.map((c) => (
                      <td key={c.key} className={`density-cell border-b border-line px-3 py-2 align-middle ${c.numeric ? "tabular text-right" : ""} ${c.className ?? ""}`}>
                        {c.render(row)}
                      </td>
                    ))}
                  </tr>
                );
              })}
          </tbody>
        </table>
        {!p.loading && p.error !== undefined && p.error !== null && (
          <div className="p-3">
            <ErrorState error={p.error} onRetry={p.onRetry} />
          </div>
        )}
        {!p.loading && !p.error && p.rows?.length === 0 && (
          <div className="p-3">
            <EmptyState title={p.emptyTitle ?? "No results"}>{p.emptyHint}</EmptyState>
          </div>
        )}
      </div>
      {p.onPage && p.total !== undefined && p.page && p.pageSize && <Pagination page={p.page} pageSize={p.pageSize} total={p.total} onPage={p.onPage} />}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav aria-label="Pagination" className="mt-2 flex items-center justify-between text-xs text-ink-3">
      <span className="tabular">
        {from}–{to} of {total.toLocaleString()}
      </span>
      <div className="flex items-center gap-2">
        <Button variant="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          ‹ Prev
        </Button>
        <span className="tabular">
          Page {page} / {pages}
        </span>
        <Button variant="ghost" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">
          Next ›
        </Button>
      </div>
    </nav>
  );
}
