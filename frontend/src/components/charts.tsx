import { useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
  ComposedChart,
} from "recharts";
import { fmtDateTime, fmtNum, fmtTime, humanize } from "../lib/format";
import { Button, EmptyState } from "./ui";

// Validated dark-mode categorical steps (see index.css). Fixed order, never cycled.
export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500"] as const;
export const STATUS_COLORS: Record<string, string> = {
  running: "#0ca30c",
  idle: "#8f8d86",
  warning: "#fab219",
  critical: "#d03b3b",
  offline: "#5f5d58",
  maintenance: "#6da7ec",
};
const GRID = "#2a2d33";
const AXIS = { fill: "#8f8d86", fontSize: 11 };

interface TableSpec {
  columns: string[];
  rows: (string | number)[][];
}

/** Accessible figure with caption and a "view as table" alternative to the chart. */
export function ChartFrame({ label, caption, table, height = 240, children, empty }: { label: string; caption?: ReactNode; table: TableSpec; height?: number; children: ReactNode; empty?: boolean }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <figure aria-label={label} className="m-0">
      {empty ? (
        <EmptyState title="No data in this time range" />
      ) : asTable ? (
        <div className="max-h-72 overflow-auto rounded border border-line">
          <table className="w-full text-xs">
            <caption className="sr-only">{label}</caption>
            <thead className="sticky top-0 bg-panel-raised text-ink-3">
              <tr>
                {table.columns.map((c) => (
                  <th key={c} scope="col" className="px-2 py-1 text-left font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i} className="border-t border-line">
                  {r.map((v, j) => (
                    <td key={j} className="tabular px-2 py-1">
                      {v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div role="img" aria-label={label} style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            {children as React.ReactElement}
          </ResponsiveContainer>
        </div>
      )}
      <figcaption className="mt-2 flex flex-wrap items-start justify-between gap-2 text-xs text-ink-3">
        <span className="max-w-prose">{caption}</span>
        {!empty && (
          <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
            {asTable ? "Show chart" : "Show table"}
          </Button>
        )}
      </figcaption>
    </figure>
  );
}

function TooltipBox({ title, rows }: { title: string; rows: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="rounded-md border border-line-strong bg-panel-raised px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-ink">{title}</p>
      {rows.map((r) => (
        <p key={r.label} className="flex items-center gap-2 text-ink-2">
          {r.color && <span aria-hidden className="inline-block h-0.5 w-3" style={{ background: r.color }} />}
          <span>{r.label}</span>
          <span className="tabular ml-auto pl-3 text-ink">{r.value}</span>
        </p>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2" aria-label="Legend">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block w-4 border-t-2" style={{ borderColor: i.color, borderStyle: i.dashed ? "dashed" : "solid" }} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- time-series line

export interface SeriesSpec {
  key: string;
  label: string;
  color: string;
}
export interface RefLine {
  y: number;
  label: string;
  color: string;
}

export function TrendChart({
  label,
  caption,
  data,
  xKey,
  series,
  unit,
  refLines = [],
  height,
}: {
  label: string;
  caption?: ReactNode;
  data: Record<string, unknown>[];
  xKey: string;
  series: SeriesSpec[];
  unit: string;
  refLines?: RefLine[];
  height?: number;
}) {
  const table: TableSpec = {
    columns: ["Time", ...series.map((s) => `${s.label} (${unit})`)],
    rows: data.map((d) => [fmtDateTime(String(d[xKey])), ...series.map((s) => fmtNum(d[s.key] as number, 2))]),
  };
  return (
    <>
      {(series.length > 1 || refLines.length > 0) && (
        <Legend items={[...series.map((s) => ({ label: s.label, color: s.color })), ...refLines.map((r) => ({ label: r.label, color: r.color, dashed: true }))]} />
      )}
      <ChartFrame label={label} caption={caption} table={table} height={height} empty={data.length === 0}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey={xKey} tickFormatter={(v: string) => fmtTime(v)} tick={AXIS} stroke={GRID} minTickGap={40} />
          <YAxis tick={AXIS} stroke={GRID} width={48} domain={["auto", "auto"]} unit={unit === "°C" ? "°" : ""} />
          <Tooltip
            cursor={{ stroke: "#8f8d86", strokeDasharray: "3 3" }}
            content={({ active, payload, label: x }) =>
              active && payload?.length ? (
                <TooltipBox
                  title={fmtDateTime(String(x))}
                  rows={series.map((s) => ({ label: s.label, value: `${fmtNum(payload[0]?.payload?.[s.key] as number, 2)} ${unit}`, color: s.color }))}
                />
              ) : null
            }
          />
          {refLines.map((r) => (
            <ReferenceLine key={r.label} y={r.y} stroke={r.color} strokeDasharray="5 4" ifOverflow="extendDomain" />
          ))}
          {series.map((s) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ChartFrame>
    </>
  );
}

// ---------------------------------------------------------------- categorical bars

export function BarList({
  label,
  caption,
  data,
  valueLabel,
  format = (v) => fmtNum(v),
  colorFor,
  layout = "vertical",
  height,
  allowDecimals = false,
}: {
  label: string;
  caption?: ReactNode;
  data: { name: string; value: number; detail?: string }[];
  valueLabel: string;
  format?: (v: number) => string;
  colorFor?: (name: string) => string;
  layout?: "vertical" | "horizontal";
  height?: number;
  /** Fractional value axis (rates, scores); integer ticks otherwise (counts). */
  allowDecimals?: boolean;
}) {
  const table: TableSpec = { columns: ["Category", valueLabel], rows: data.map((d) => [humanize(d.name), format(d.value)]) };
  const horizontalBars = layout === "vertical";
  return (
    <ChartFrame label={label} caption={caption} table={table} height={height ?? (horizontalBars ? Math.max(120, data.length * 34 + 20) : 240)} empty={data.length === 0}>
      <BarChart data={data} layout={layout} margin={{ top: horizontalBars ? 4 : 20, right: 48, bottom: 0, left: 0 }} barCategoryGap={horizontalBars ? 6 : "20%"}>
        <CartesianGrid stroke={GRID} horizontal={!horizontalBars} vertical={horizontalBars} />
        {horizontalBars ? (
          <>
            <XAxis type="number" tick={AXIS} stroke={GRID} allowDecimals={allowDecimals} />
            <YAxis type="category" dataKey="name" tick={AXIS} stroke={GRID} width={110} tickFormatter={humanize} />
          </>
        ) : (
          <>
            <XAxis dataKey="name" tick={AXIS} stroke={GRID} tickFormatter={humanize} />
            <YAxis tick={AXIS} stroke={GRID} width={40} />
          </>
        )}
        <Tooltip
          cursor={{ fill: "rgba(255,255,255,0.04)" }}
          content={({ active, payload }) => {
            const d = payload?.[0]?.payload as { name: string; value: number; detail?: string } | undefined;
            return active && d ? <TooltipBox title={humanize(d.name)} rows={[{ label: valueLabel, value: format(d.value) }, ...(d.detail ? [{ label: "Detail", value: d.detail }] : [])]} /> : null;
          }}
        />
        <Bar dataKey="value" name={valueLabel} radius={horizontalBars ? [0, 4, 4, 0] : [4, 4, 0, 0]} isAnimationActive={false} maxBarSize={28}>
          {data.map((d) => (
            <Cell key={d.name} fill={colorFor?.(d.name) ?? SERIES[0]} />
          ))}
          <LabelList dataKey="value" position={horizontalBars ? "right" : "top"} formatter={(v: unknown) => format(Number(v))} fill="#c3c2b7" fontSize={11} />
        </Bar>
      </BarChart>
    </ChartFrame>
  );
}

// ---------------------------------------------------------------- stacked bars over time

export function StackedTimeBars({ label, caption, data, series }: { label: string; caption?: ReactNode; data: Record<string, unknown>[]; series: SeriesSpec[] }) {
  const table: TableSpec = {
    columns: ["Time", ...series.map((s) => s.label)],
    rows: data.map((d) => [fmtDateTime(String(d.bucket)), ...series.map((s) => Number(d[s.key] ?? 0))]),
  };
  return (
    <>
      <Legend items={series.map((s) => ({ label: s.label, color: s.color }))} />
      <ChartFrame label={label} caption={caption} table={table} empty={data.length === 0}>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="bucket" tickFormatter={(v: string) => fmtTime(v)} tick={AXIS} stroke={GRID} minTickGap={40} />
          <YAxis tick={AXIS} stroke={GRID} width={44} allowDecimals={false} />
          <Tooltip
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
            content={({ active, payload, label: x }) =>
              active && payload?.length ? (
                <TooltipBox title={fmtDateTime(String(x))} rows={series.map((s) => ({ label: s.label, value: fmtNum(Number(payload[0]?.payload?.[s.key] ?? 0)), color: s.color }))} />
              ) : null
            }
          />
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} stackId="a" fill={s.color} stroke="#14161a" strokeWidth={1} radius={i === series.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />
          ))}
        </BarChart>
      </ChartFrame>
    </>
  );
}

// ---------------------------------------------------------------- control chart

export function ControlChartView({
  label,
  caption,
  points,
  unit,
  cl,
  ucl,
  lcl,
}: {
  label: string;
  caption?: ReactNode;
  points: { event_time: string; value: number; out_of_control: boolean; rule: string | null }[];
  unit: string;
  cl: number | null;
  ucl: number | null;
  lcl: number | null;
}) {
  const data = points.map((p) => ({ ...p, flagged: p.out_of_control ? p.value : null }));
  const table: TableSpec = {
    columns: ["Time", `Value (${unit})`, "Rule triggered"],
    rows: points.map((p) => [fmtDateTime(p.event_time), fmtNum(p.value, 2), p.rule ? humanize(p.rule) : "—"]),
  };
  return (
    <>
      <Legend
        items={[
          { label: "Reading", color: SERIES[0] },
          ...(cl !== null ? [{ label: "Center line", color: "#8f8d86", dashed: true }] : []),
          ...(ucl !== null ? [{ label: "Control limits (±3σ)", color: SERIES[1], dashed: true }] : []),
          { label: "Out of control (■)", color: "#d03b3b" },
        ]}
      />
      <ChartFrame label={label} caption={caption} table={table} height={260} empty={points.length === 0}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="event_time" tickFormatter={(v: string) => fmtTime(v)} tick={AXIS} stroke={GRID} minTickGap={40} />
          <YAxis tick={AXIS} stroke={GRID} width={48} domain={["auto", "auto"]} />
          <Tooltip
            content={({ active, payload }) => {
              const p = payload?.[0]?.payload as (typeof data)[number] | undefined;
              return active && p ? (
                <TooltipBox
                  title={fmtDateTime(p.event_time)}
                  rows={[{ label: "Value", value: `${fmtNum(p.value, 2)} ${unit}`, color: SERIES[0] }, ...(p.rule ? [{ label: "Rule", value: humanize(p.rule) }] : [])]}
                />
              ) : null;
            }}
          />
          {cl !== null && <ReferenceLine y={cl} stroke="#8f8d86" strokeDasharray="4 4" />}
          {ucl !== null && <ReferenceLine y={ucl} stroke={SERIES[1]} strokeDasharray="5 4" ifOverflow="extendDomain" />}
          {lcl !== null && <ReferenceLine y={lcl} stroke={SERIES[1]} strokeDasharray="5 4" ifOverflow="extendDomain" />}
          <Line dataKey="value" stroke={SERIES[0]} strokeWidth={1.5} dot={false} isAnimationActive={false} />
          <Scatter dataKey="flagged" fill="#d03b3b" shape="square" isAnimationActive={false} />
        </ComposedChart>
      </ChartFrame>
    </>
  );
}

// ---------------------------------------------------------------- precision-recall curves

export function PrCurveChart({
  label,
  caption,
  curves,
  baseline,
}: {
  label: string;
  caption?: ReactNode;
  curves: { name: string; color: string; points: { recall: number; precision: number }[] }[];
  baseline: number;
}) {
  const table: TableSpec = {
    columns: ["Model", "Recall", "Precision"],
    rows: curves.flatMap((c) => c.points.map((p) => [c.name, p.recall.toFixed(3), p.precision.toFixed(3)])),
  };
  return (
    <>
      <Legend items={[...curves.map((c) => ({ label: c.name, color: c.color })), { label: `No-skill baseline (prevalence ${(baseline * 100).toFixed(2)}%)`, color: "#8f8d86", dashed: true }]} />
      <ChartFrame label={label} caption={caption} table={table} height={280} empty={curves.length === 0}>
        <LineChart margin={{ top: 8, right: 16, bottom: 16, left: 0 }}>
          <CartesianGrid stroke={GRID} />
          <XAxis type="number" dataKey="recall" domain={[0, 1]} tick={AXIS} stroke={GRID} tickCount={6} label={{ value: "Recall", position: "insideBottom", offset: -8, fill: "#8f8d86", fontSize: 11 }} />
          <YAxis type="number" dataKey="precision" domain={[0, 1]} tick={AXIS} stroke={GRID} width={44} tickCount={6} />
          <Tooltip
            content={({ active, payload }) => {
              const p = payload?.[0];
              const d = p?.payload as { recall: number; precision: number } | undefined;
              return active && d ? <TooltipBox title={String(p?.name)} rows={[{ label: "Recall", value: d.recall.toFixed(3) }, { label: "Precision", value: d.precision.toFixed(3) }]} /> : null;
            }}
          />
          <ReferenceLine y={baseline} stroke="#8f8d86" strokeDasharray="4 4" />
          {curves.map((c) => (
            <Line key={c.name} data={c.points} dataKey="precision" name={c.name} stroke={c.color} strokeWidth={2} dot={false} type="stepAfter" isAnimationActive={false} />
          ))}
        </LineChart>
      </ChartFrame>
    </>
  );
}
