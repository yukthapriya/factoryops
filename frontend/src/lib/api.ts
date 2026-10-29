import type {
  Ai4iRecord,
  Alert,
  AlertDetail,
  ControlChart,
  DashboardSummary,
  DataQualityFailure,
  DataQualitySummary,
  DatasetImport,
  Defect,
  DefectAnalytics,
  DefectDetail,
  DefectNote,
  Equipment,
  EquipmentAnalyticsRow,
  EquipmentInput,
  FailureAnalysis,
  HistoricalFeature,
  HistoricalSummary,
  Metric,
  ModelEvaluation,
  Page,
  PublicSettings,
  RuleCheck,
  Run,
  RunDetail,
  Scenario,
  TelemetryPoint,
  TelemetrySummary,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

export interface ErrorDetail {
  loc: string[];
  msg: string;
  type?: string;
}

/** Error carrying the backend's consistent error envelope. status 0 = network failure. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ErrorDetail[];
  readonly requestId: string | null;

  constructor(status: number, code: string, message: string, details: ErrorDetail[] = [], requestId: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  /** Field-level messages keyed by the last path segment (for forms). */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const d of this.details) {
      const field = d.loc.filter((p) => p !== "body").join(".") || "_";
      out[field] = d.msg.replace(/^Value error, /, "");
    }
    return out;
  }
}

export type Params = Record<string, string | number | boolean | null | undefined>;

export function buildQuery(params: Params = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(method: string, path: string, opts: { params?: Params; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}${buildQuery(opts.params)}`, {
      method,
      headers: opts.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "network_error", "Cannot reach the FactoryOps API. Is the backend running?");
  }
  if (res.ok) return (await res.json()) as T;
  let envelope: { error?: { code?: string; message?: string; details?: ErrorDetail[]; request_id?: string } } = {};
  try {
    envelope = await res.json();
  } catch {
    /* non-JSON error body (e.g. proxy error) */
  }
  const e = envelope.error;
  throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? `Request failed (${res.status})`, e?.details ?? [], e?.request_id ?? null);
}

const get = <T>(path: string, params?: Params, signal?: AbortSignal) => request<T>("GET", path, { params, signal });

export const api = {
  dashboard: (p: Params) => get<DashboardSummary>("/api/dashboard/summary", p),
  telemetrySummary: (p: Params) => get<TelemetrySummary>("/api/telemetry/summary", p),
  telemetry: (p: Params) => get<TelemetryPoint[]>("/api/telemetry", p),

  equipmentList: (p: Params) => get<Page<Equipment>>("/api/equipment", p),
  stations: () => get<string[]>("/api/equipment/stations"),
  equipment: (id: string) => get<Equipment>(`/api/equipment/${id}`),
  createEquipment: (body: EquipmentInput) => request<Equipment>("POST", "/api/equipment", { body }),
  updateEquipment: (id: string, body: Partial<EquipmentInput> & { is_active?: boolean }) => request<Equipment>("PATCH", `/api/equipment/${id}`, { body }),
  deactivateEquipment: (id: string) => request<Equipment>("DELETE", `/api/equipment/${id}`),

  runs: (p: Params) => get<Page<Run>>("/api/production-runs", p),
  run: (id: string) => get<RunDetail>(`/api/production-runs/${id}`),

  defects: (p: Params) => get<Page<Defect>>("/api/defects", p),
  defect: (id: string) => get<DefectDetail>(`/api/defects/${id}`),
  updateDefect: (id: string, body: { status?: string; severity?: string; author: string; note?: string }) =>
    request<DefectDetail>("PATCH", `/api/defects/${id}`, { body }),
  addDefectNote: (id: string, body: { author: string; body: string }) => request<DefectNote>("POST", `/api/defects/${id}/notes`, { body }),

  alerts: (p: Params) => get<Page<Alert>>("/api/alerts", p),
  alert: (id: string) => get<AlertDetail>(`/api/alerts/${id}`),
  acknowledgeAlert: (id: string, body: { actor: string; note?: string }) => request<AlertDetail>("POST", `/api/alerts/${id}/acknowledge`, { body }),
  resolveAlert: (id: string, body: { actor: string; note?: string }) => request<AlertDetail>("POST", `/api/alerts/${id}/resolve`, { body }),

  defectAnalytics: (p: Params) => get<DefectAnalytics>("/api/analytics/defects", p),
  equipmentAnalytics: (p: Params) => get<{ rows: EquipmentAnalyticsRow[] }>("/api/analytics/equipment", p),
  controlChart: (p: Params & { equipment_id: string; metric: Metric }) => get<ControlChart>("/api/analytics/control-chart", p),

  dqSummary: (p: Params) => get<DataQualitySummary>("/api/data-quality/summary", p),
  dqFailures: (p: Params) => get<Page<DataQualityFailure>>("/api/data-quality/failures", p),

  historicalDatasets: () => get<DatasetImport[]>("/api/historical/datasets"),
  historicalSummary: () => get<HistoricalSummary>("/api/historical/summary"),
  failureAnalysis: (feature: HistoricalFeature, bins: number) => get<FailureAnalysis>("/api/historical/failure-analysis", { feature, bins }),
  failureRules: () => get<RuleCheck[]>("/api/historical/failure-rules"),
  historicalRecords: (p: Params) => get<Page<Ai4iRecord>>("/api/historical/records", p),
  modelEvaluations: () => get<ModelEvaluation[]>("/api/historical/model-evaluations"),

  publicSettings: () => get<PublicSettings>("/api/settings/public"),
  simulate: (body: { scenario: Scenario; ticks: number; seed?: number }) =>
    request<{ scenario: string; events_sent: number; outcomes: Record<string, number> }>("POST", "/api/demo/simulate", { body }),
  recomputeBaselines: (hours: number) => request<{ baselines_updated: number }>("POST", "/api/demo/baselines", { params: { hours } }),
};
