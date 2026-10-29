import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../lib/api";

type Tone = "success" | "error" | "info";
interface Toast {
  id: number;
  tone: Tone;
  title: string;
  detail?: string;
}

interface ToastApi {
  success: (title: string, detail?: string) => void;
  error: (title: string, err?: unknown) => void;
  info: (title: string, detail?: string) => void;
}

const Ctx = createContext<ToastApi | null>(null);

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const fields = err.details.map((d) => d.msg.replace(/^Value error, /, "")).join("; ");
    return [err.message, fields].filter(Boolean).join(": ") + (err.requestId ? ` (request ${err.requestId.slice(0, 8)})` : "");
  }
  return err instanceof Error ? err.message : "Unknown error";
}

const TONE_CLASS: Record<Tone, string> = {
  success: "border-good/50",
  error: "border-critical/60",
  info: "border-line-strong",
};
const TONE_ICON: Record<Tone, string> = { success: "✓", error: "!", info: "i" };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (tone: Tone, title: string, detail?: string) => {
      const id = nextId.current++;
      setToasts((t) => [...t.slice(-3), { id, tone, title, detail }]);
      setTimeout(() => dismiss(id), tone === "error" ? 8000 : 4000);
    },
    [dismiss],
  );
  const api = useMemo<ToastApi>(
    () => ({
      success: (t, d) => push("success", t, d),
      error: (t, err) => push("error", t, err === undefined ? undefined : errorMessage(err)),
      info: (t, d) => push("info", t, d),
    }),
    [push],
  );

  return (
    <Ctx.Provider value={api}>
      {children}
      <div aria-live="polite" role="status" className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} className={`pointer-events-auto flex gap-3 rounded-md border bg-panel-raised p-3 shadow-lg ${TONE_CLASS[t.tone]}`}>
            <span aria-hidden className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-line text-xs font-bold">
              {TONE_ICON[t.tone]}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">{t.title}</p>
              {t.detail && <p className="mt-0.5 text-xs break-words text-ink-2">{t.detail}</p>}
            </div>
            <button className="text-ink-3 hover:text-ink" onClick={() => dismiss(t.id)} aria-label="Dismiss notification">
              ×
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}
