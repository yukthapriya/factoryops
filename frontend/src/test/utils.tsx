import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { LiveProvider } from "../hooks/live";
import { PreferencesProvider, type Preferences } from "../hooks/preferences";
import { ToastProvider } from "../hooks/toast";
import type { Alert, Defect, DefectDetail, Equipment, Page } from "../lib/types";

export function renderWithProviders(ui: ReactElement, { route = "/", path = "*", prefs = {} as Partial<Preferences> } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        {/* live updates off in tests: no socket is opened */}
        <PreferencesProvider initial={{ liveUpdates: false, pollingSeconds: 0, operatorName: "", ...prefs }}>
          <ToastProvider>
            <LiveProvider>
              <MemoryRouter initialEntries={[route]}>
                <Routes>
                  <Route path={path} element={ui} />
                </Routes>
              </MemoryRouter>
            </LiveProvider>
          </ToastProvider>
        </PreferencesProvider>
      </QueryClientProvider>,
    ),
  };
}

export const page = <T,>(items: T[], total = items.length): Page<T> => ({ items, total, page: 1, page_size: 20 });

export const ref = { id: "e1", code: "PRESS-01", name: "Press Line A1", station: "Stamping" };

export const equipment = (over: Partial<Equipment> = {}): Equipment => ({
  ...ref,
  equipment_type: "Stamping Press",
  status: "running",
  is_active: true,
  last_event_at: new Date().toISOString(),
  thresholds: [{ metric: "temperature", warning_low: null, warning_high: 77, critical_low: null, critical_high: 87 }],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...over,
});

export const alert = (over: Partial<Alert> = {}): Alert => ({
  id: "a1",
  equipment: ref,
  event_id: "ev1",
  metric: "temperature",
  rule: "threshold_high",
  rule_detail: "temperature 80.00 °C > warning limit 77.00 °C",
  observed_value: 80,
  threshold_value: 77,
  severity: "warning",
  status: "open",
  occurrence_count: 3,
  triggered_at: "2026-01-01T00:00:00Z",
  last_seen_at: "2026-01-01T00:05:00Z",
  acknowledged_at: null,
  resolved_at: null,
  ...over,
});

export const defect = (over: Partial<Defect> = {}): Defect => ({
  id: "d1234567-0000-0000-0000-000000000000",
  category: "surface",
  severity: "minor",
  status: "open",
  detected_at: "2026-01-01T00:00:00Z",
  description: "Synthetic surface defect flagged by inline inspection",
  source_event_id: "ev1",
  equipment: ref,
  production_run: { id: "r1", run_code: "PRESS-01-B1", batch_id: "B1" },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...over,
});

export const defectDetail = (over: Partial<DefectDetail> = {}): DefectDetail => ({
  ...defect(),
  notes: [],
  evidence: {
    window_start: "2025-12-31T23:45:00Z",
    window_end: "2026-01-01T00:15:00Z",
    source_event: null,
    telemetry: [],
    alerts: [],
    thresholds: [],
  },
  ...over,
});
