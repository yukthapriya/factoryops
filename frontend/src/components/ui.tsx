import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { ApiError } from "../lib/api";
import { humanize } from "../lib/format";

// ---------------------------------------------------------------- buttons

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent-strong text-white hover:bg-accent disabled:bg-line",
  secondary: "border border-line-strong bg-panel-raised text-ink hover:border-ink-3",
  danger: "bg-critical/90 text-white hover:bg-critical",
  ghost: "text-ink-2 hover:bg-panel-raised hover:text-ink",
};

export function Button({ variant = "secondary", className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      {...rest}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${VARIANTS[variant]} ${className}`}
    />
  );
}

// ---------------------------------------------------------------- status

type Tone = "good" | "warning" | "serious" | "critical" | "info" | "neutral";
const TONE: Record<Tone, { dot: string; text: string; icon: string }> = {
  good: { dot: "bg-good", text: "text-good", icon: "●" },
  warning: { dot: "bg-warning", text: "text-warning", icon: "▲" },
  serious: { dot: "bg-serious", text: "text-serious", icon: "◆" },
  critical: { dot: "bg-critical", text: "text-critical", icon: "■" },
  info: { dot: "bg-info", text: "text-info", icon: "●" },
  neutral: { dot: "bg-neutral", text: "text-ink-2", icon: "○" },
};

const STATUS_TONE: Record<string, Tone> = {
  running: "good",
  idle: "neutral",
  warning: "warning",
  critical: "critical",
  offline: "neutral",
  maintenance: "info",
  open: "critical",
  acknowledged: "warning",
  resolved: "good",
  investigating: "warning",
  dismissed: "neutral",
  completed: "good",
  aborted: "serious",
  minor: "neutral",
  major: "serious",
};

export function toneFor(value: string): Tone {
  return STATUS_TONE[value] ?? "neutral";
}

/** Status is never color alone: a shape icon + text label accompany the color. */
export function StatusBadge({ value, tone }: { value: string; tone?: Tone }) {
  const t = TONE[tone ?? toneFor(value)];
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-panel-raised px-2 py-0.5 text-xs font-medium whitespace-nowrap">
      <span aria-hidden className={`${t.text} text-[0.6rem] leading-none`}>
        {t.icon}
      </span>
      <span className="text-ink">{humanize(value)}</span>
    </span>
  );
}

export function SeverityBadge({ value }: { value: string }) {
  return <StatusBadge value={value} tone={value === "critical" ? "critical" : value === "warning" ? "warning" : toneFor(value)} />;
}

// ---------------------------------------------------------------- layout pieces

export function Card({ title, actions, children, className = "", description }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; description?: ReactNode }) {
  return (
    <section className={`rounded-lg border border-line bg-panel ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-line px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-ink-3">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function KpiCard({ label, value, sub, loading, tone }: { label: string; value: ReactNode; sub?: ReactNode; loading?: boolean; tone?: Tone }) {
  return (
    <div className="rounded-lg border border-line bg-panel p-4">
      <p className="text-xs font-medium tracking-wide text-ink-3 uppercase">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-8 w-20" />
      ) : (
        <p className="mt-1 flex items-center gap-2 text-3xl font-semibold text-ink">
          {tone && (
            <span aria-hidden className={`${TONE[tone].text} text-sm`}>
              {TONE[tone].icon}
            </span>
          )}
          {value}
        </p>
      )}
      {sub && <p className="mt-1 text-xs text-ink-3">{sub}</p>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`animate-pulse rounded bg-line/70 ${className}`} />;
}

export function LoadingBlock({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-6 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-line px-4 py-10 text-center">
      <p className="text-sm font-medium text-ink-2">{title}</p>
      {children && <div className="mt-1 max-w-md text-xs text-ink-3">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, title = "Could not load data" }: { error: unknown; onRetry?: () => void; title?: string }) {
  const err = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className="rounded-md border border-critical/40 bg-critical/10 px-4 py-3">
      <p className="text-sm font-medium text-ink">{title}</p>
      <p className="mt-1 text-xs text-ink-2">
        {err?.status === 0 ? err.message : error instanceof Error ? error.message : "Unknown error"}
        {err?.requestId && <span className="ml-1 font-mono text-ink-3">(request {err.requestId.slice(0, 8)})</span>}
      </p>
      {onRetry && (
        <Button className="mt-2" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

/** Renders the right state for a TanStack query result. */
export function QueryState<T>({
  query,
  children,
  isEmpty,
  empty,
  loading,
}: {
  query: { data: T | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
  children: (data: T) => ReactNode;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  loading?: ReactNode;
}) {
  if (query.isPending) return <>{loading ?? <LoadingBlock />}</>;
  if (query.isError || query.data === undefined) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (isEmpty?.(query.data)) return <>{empty ?? <EmptyState title="Nothing to show" />}</>;
  return <>{children(query.data)}</>;
}

// ---------------------------------------------------------------- form controls

const inputCls =
  "w-full rounded-md border border-line-strong bg-panel-raised px-2.5 py-1.5 text-sm text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none aria-[invalid=true]:border-critical";

export function SearchInput({ value, onChange, placeholder = "Search…", label = "Search" }: { value: string; onChange: (v: string) => void; placeholder?: string; label?: string }) {
  return (
    <label className="relative block min-w-48 flex-1 sm:max-w-xs">
      <span className="sr-only">{label}</span>
      <span aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3">
        ⌕
      </span>
      <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className={`${inputCls} pl-7`} />
    </label>
  );
}

export function SelectFilter({
  label,
  value,
  onChange,
  options,
  allLabel = "All",
  ...rest
}: { label: string; value: string; onChange: (v: string) => void; options: (string | { value: string; label: string })[]; allLabel?: string } & Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  "onChange" | "value"
>) {
  const id = useId();
  return (
    <div className="flex items-center gap-1.5">
      <label htmlFor={id} className="text-xs text-ink-3">
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={`${inputCls} w-auto py-1`} {...rest}>
        {allLabel !== "" && <option value="">{allLabel}</option>}
        {options.map((o) => {
          const opt = typeof o === "string" ? { value: o, label: humanize(o) } : o;
          return (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          );
        })}
      </select>
    </div>
  );
}

export function FilterBar({ children, onReset }: { children: ReactNode; onReset?: () => void }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-3" role="search">
      {children}
      {onReset && (
        <Button variant="ghost" onClick={onReset}>
          Clear filters
        </Button>
      )}
    </div>
  );
}

export function DateRangeInputs({ start, end, onChange }: { start: string; end: string; onChange: (start: string, end: string) => void }) {
  return (
    <div className="flex items-center gap-1.5 text-xs text-ink-3">
      <label className="flex items-center gap-1.5">
        From
        <input type="date" value={start} max={end || undefined} onChange={(e) => onChange(e.target.value, end)} className={`${inputCls} w-auto py-1`} />
      </label>
      <label className="flex items-center gap-1.5">
        To
        <input type="date" value={end} min={start || undefined} onChange={(e) => onChange(start, e.target.value)} className={`${inputCls} w-auto py-1`} />
      </label>
    </div>
  );
}

/** Converts YYYY-MM-DD local dates into an inclusive ISO range for the API. */
export function dateRangeParams(start: string, end: string): { start?: string; end?: string } {
  const out: { start?: string; end?: string } = {};
  if (start) out.start = new Date(`${start}T00:00:00`).toISOString();
  if (end) {
    const d = new Date(`${end}T00:00:00`);
    d.setDate(d.getDate() + 1);
    out.end = d.toISOString();
  }
  return out;
}

export function Field({ label, error, children, hint, htmlFor }: { label: string; error?: string; children: ReactNode; hint?: string; htmlFor?: string }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-ink-2">
        {label}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-ink-3">{hint}</p>}
      {error && (
        <p role="alert" className="mt-1 text-xs text-critical">
          {error}
        </p>
      )}
    </div>
  );
}

export { inputCls };

// ---------------------------------------------------------------- dialogs

export function Modal({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    node?.querySelector<HTMLElement>("input, select, textarea, button:not([data-close])")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && node) {
        const items = node.querySelectorAll<HTMLElement>("button, input, select, textarea, a[href]");
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} className={`max-h-[90vh] w-full overflow-y-auto rounded-lg border border-line-strong bg-panel shadow-2xl ${wide ? "max-w-3xl" : "max-w-md"}`}>
        <header className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 id={titleId} className="text-base font-semibold">
            {title}
          </h2>
          <button data-close className="text-xl leading-none text-ink-3 hover:text-ink" onClick={onClose} aria-label="Close dialog">
            ×
          </button>
        </header>
        <div className="px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  danger,
  pending,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      footer={
        <>
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} disabled={pending}>
            {pending ? "Working…" : confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-sm text-ink-2">{message}</div>
    </Modal>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-ink-3">{description}</p>}
      </div>
      {actions}
    </div>
  );
}

export function DefinitionList({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
