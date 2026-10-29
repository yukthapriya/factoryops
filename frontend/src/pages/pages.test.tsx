import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionStatus } from "../components/Layout";
import { api, ApiError } from "../lib/api";
import { alert, defect, defectDetail, equipment, page, renderWithProviders } from "../test/utils";
import { AlertsPage } from "./Alerts";
import { DefectDetailPage } from "./DefectDetail";
import { EquipmentPage } from "./Equipment";
import { OverviewPage } from "./Overview";

vi.mock("../lib/api", async (orig) => {
  const actual = await orig<typeof import("../lib/api")>();
  const fns = Object.fromEntries(Object.keys(actual.api).map((k) => [k, vi.fn()]));
  return { ...actual, api: fns };
});
const mocked = api as unknown as Record<keyof typeof api, ReturnType<typeof vi.fn>>;

beforeEach(() => {
  vi.clearAllMocks();
  mocked.stations.mockResolvedValue(["Stamping", "Paint"]);
});

describe("Equipment page", () => {
  it("shows loading, then rows from the API", async () => {
    let resolve!: (v: unknown) => void;
    mocked.equipmentList.mockReturnValue(new Promise((r) => (resolve = r)));
    renderWithProviders(<EquipmentPage />);
    expect(screen.getByRole("table", { name: "Equipment list" })).toBeInTheDocument();
    expect(screen.queryByText("PRESS-01")).not.toBeInTheDocument();
    resolve(page([equipment()]));
    expect(await screen.findByText("PRESS-01")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Equipment list" })).getByText("Running")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    mocked.equipmentList.mockResolvedValue(page([]));
    renderWithProviders(<EquipmentPage />);
    expect(await screen.findByText("No equipment matches these filters")).toBeInTheDocument();
  });

  it("shows an error with retry", async () => {
    mocked.equipmentList.mockRejectedValueOnce(new ApiError(0, "network_error", "Cannot reach the FactoryOps API.")).mockResolvedValueOnce(page([equipment()]));
    renderWithProviders(<EquipmentPage />);
    expect(await screen.findByText(/Cannot reach the FactoryOps API/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("PRESS-01")).toBeInTheDocument();
  });

  it("sends debounced search and filters to the backend", async () => {
    mocked.equipmentList.mockResolvedValue(page([equipment()]));
    renderWithProviders(<EquipmentPage />);
    await screen.findByText("PRESS-01");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search equipment" }), "weld");
    await waitFor(() => expect(mocked.equipmentList).toHaveBeenLastCalledWith(expect.objectContaining({ q: "weld", page: 1 })));
    const searchCalls = mocked.equipmentList.mock.calls.filter(([p]) => (p as { q: string }).q.length > 0);
    expect(searchCalls).toHaveLength(1); // debounced: not one request per keystroke
    await userEvent.selectOptions(screen.getByLabelText("Status"), "warning");
    await waitFor(() => expect(mocked.equipmentList).toHaveBeenLastCalledWith(expect.objectContaining({ status: "warning", q: "weld" })));
  });

  it("validates the create form and submits valid data", async () => {
    mocked.equipmentList.mockResolvedValue(page([]));
    mocked.createEquipment.mockResolvedValue(equipment({ id: "new", code: "CNC-09" }));
    renderWithProviders(<EquipmentPage />);
    await userEvent.click(screen.getByRole("button", { name: "+ Add equipment" }));
    const dialog = screen.getByRole("dialog", { name: "Add equipment" });
    const d = within(dialog);

    await userEvent.type(d.getByLabelText("Equipment ID"), "bad id");
    await userEvent.type(d.getByLabelText("Temperature warning high"), "95");
    await userEvent.type(d.getByLabelText("Temperature critical high"), "90");
    await userEvent.click(d.getByRole("button", { name: "Create equipment" }));
    expect(await d.findByText(/uppercase letters, digits and hyphens/)).toBeInTheDocument();
    expect(d.getByText("Name")).toBeInTheDocument();
    expect(d.getByText("Warning high must be ≤ critical high")).toBeInTheDocument();
    expect(mocked.createEquipment).not.toHaveBeenCalled();

    await userEvent.clear(d.getByLabelText("Equipment ID"));
    await userEvent.type(d.getByLabelText("Equipment ID"), "CNC-09");
    await userEvent.type(d.getByLabelText("Name"), "Spare Mill");
    await userEvent.type(d.getByLabelText("Station"), "Machining");
    await userEvent.type(d.getByLabelText("Equipment type"), "CNC Mill");
    await userEvent.clear(d.getByLabelText("Temperature warning high"));
    await userEvent.type(d.getByLabelText("Temperature warning high"), "80");
    await userEvent.click(d.getByRole("button", { name: "Create equipment" }));
    await waitFor(() =>
      expect(mocked.createEquipment).toHaveBeenCalledWith({
        code: "CNC-09",
        name: "Spare Mill",
        station: "Machining",
        equipment_type: "CNC Mill",
        status: "idle",
        thresholds: [{ metric: "temperature", warning_low: null, warning_high: 80, critical_low: null, critical_high: 90 }],
      }),
    );
  });

  it("surfaces a server-side conflict on the field", async () => {
    mocked.equipmentList.mockResolvedValue(page([]));
    mocked.createEquipment.mockRejectedValue(new ApiError(409, "conflict", "equipment code 'CNC-09' already exists"));
    renderWithProviders(<EquipmentPage />);
    await userEvent.click(screen.getByRole("button", { name: "+ Add equipment" }));
    const d = within(screen.getByRole("dialog"));
    await userEvent.type(d.getByLabelText("Equipment ID"), "CNC-09");
    await userEvent.type(d.getByLabelText("Name"), "Spare Mill");
    await userEvent.type(d.getByLabelText("Station"), "Machining");
    await userEvent.type(d.getByLabelText("Equipment type"), "CNC Mill");
    await userEvent.click(d.getByRole("button", { name: "Create equipment" }));
    expect(await d.findByText("equipment code 'CNC-09' already exists")).toBeInTheDocument();
  });
});

describe("Alerts page", () => {
  it("acknowledges an alert with attribution and invalidates", async () => {
    mocked.alerts.mockResolvedValue(page([alert()]));
    mocked.alert.mockResolvedValue({ ...alert(), history: [{ from_status: null, to_status: "open", actor: "system", note: null, changed_at: "2026-01-01T00:00:00Z" }], event: null });
    mocked.acknowledgeAlert.mockResolvedValue({ ...alert({ status: "acknowledged", acknowledged_at: "2026-01-01T00:06:00Z" }), history: [], event: null });
    renderWithProviders(<AlertsPage />, { route: "/alerts?selected=a1" });

    expect(await screen.findByText("Why this alert fired")).toBeInTheDocument();
    expect(screen.getByText("temperature 80.00 °C > warning limit 77.00 °C")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    expect(await screen.findByText("Enter your name for the audit trail")).toBeInTheDocument();
    expect(mocked.acknowledgeAlert).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText("Your name"), "Dana");
    await userEvent.type(screen.getByLabelText("Note (optional)"), "checking coolant");
    await userEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(mocked.acknowledgeAlert).toHaveBeenCalledWith("a1", { actor: "Dana", note: "checking coolant" }));
    expect(await screen.findByText("Alert acknowledged")).toBeInTheDocument();
  });

  it("shows failure feedback for an invalid transition", async () => {
    mocked.alerts.mockResolvedValue(page([alert()]));
    mocked.alert.mockResolvedValue({ ...alert(), history: [], event: null });
    mocked.resolveAlert.mockRejectedValue(new ApiError(409, "invalid_transition", "cannot change alert status from resolved to resolved"));
    renderWithProviders(<AlertsPage />, { route: "/alerts?selected=a1", prefs: { operatorName: "Dana" } });
    await screen.findByText("Why this alert fired");
    await userEvent.click(screen.getByRole("button", { name: "Resolve" }));
    expect(await screen.findByText("Alert update failed")).toBeInTheDocument();
    expect(screen.getByText(/cannot change alert status/)).toBeInTheDocument();
  });
});

describe("Defect investigation", () => {
  it("requires a note to resolve, then updates status", async () => {
    const updated = defectDetail({ ...defect({ status: "resolved" }), notes: [{ id: "n1", author: "Quinn", body: "status open → resolved\nFixture shimmed", status_from: "open", status_to: "resolved", created_at: "2026-01-01T01:00:00Z" }] });
    // The server returns the updated record on refetch after the mutation.
    mocked.defect.mockResolvedValueOnce(defectDetail()).mockResolvedValue(updated);
    mocked.updateDefect.mockResolvedValue(updated);
    renderWithProviders(<DefectDetailPage />, { route: "/defects/d1", path: "/defects/:id", prefs: { operatorName: "Quinn" } });

    await screen.findByText("Investigation status");
    await userEvent.selectOptions(screen.getByLabelText("Status"), "resolved");
    await userEvent.click(screen.getByRole("button", { name: "Update investigation" }));
    expect(await screen.findByText("Explain the evidence before resolving or dismissing")).toBeInTheDocument();
    expect(mocked.updateDefect).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText("Note / evidence"), "Fixture shimmed");
    await userEvent.click(screen.getByRole("button", { name: "Update investigation" }));
    await waitFor(() => expect(mocked.updateDefect).toHaveBeenCalledWith(expect.stringMatching(/^d1/), { status: "resolved", severity: undefined, author: "Quinn", note: "Fixture shimmed" }));
    expect(await screen.findByText(/Fixture shimmed/, { selector: "p" })).toBeInTheDocument();
  });

  it("adds a timestamped note", async () => {
    mocked.defect.mockResolvedValue(defectDetail());
    mocked.addDefectNote.mockResolvedValue({ id: "n1", author: "Quinn", body: "Checked fixture", status_from: null, status_to: null, created_at: "2026-01-01T01:00:00Z" });
    renderWithProviders(<DefectDetailPage />, { route: "/defects/d1", path: "/defects/:id" });
    await screen.findByText("No notes yet.");
    await userEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(await screen.findAllByText("Your name is required")).not.toHaveLength(0);
    const authors = screen.getAllByLabelText("Your name");
    await userEvent.type(authors[authors.length - 1]!, "Quinn");
    await userEvent.type(screen.getByLabelText("Add a note"), "Checked fixture");
    await userEvent.click(screen.getByRole("button", { name: "Add note" }));
    await waitFor(() => expect(mocked.addDefectNote).toHaveBeenCalledWith(expect.any(String), { author: "Quinn", body: "Checked fixture" }));
    expect(await screen.findByText("Note added")).toBeInTheDocument();
  });
});

describe("Overview", () => {
  it("renders KPI values from the dashboard API", async () => {
    mocked.dashboard.mockResolvedValue({
      generated_at: "2026-01-01T00:00:00Z",
      window_start: "2025-12-31T00:00:00Z",
      window_end: "2026-01-01T00:00:00Z",
      last_event_at: "2026-01-01T00:00:00Z",
      total_equipment: 15,
      running_equipment: 12,
      warning_or_critical_equipment: 2,
      units_produced: 5047,
      defects: 94,
      defect_rate_pct: 1.86,
      events_received: 5376,
      events_rejected: 23,
      open_alerts: 7,
      status_distribution: [{ status: "running", count: 12 }],
      calculation_notes: { units_produced: "x", equipment: "y" },
    });
    mocked.telemetrySummary.mockResolvedValue({ start: "", end: "", bucket_seconds: 900, equipment_id: null, buckets: [] });
    mocked.defectAnalytics.mockResolvedValue({ by_station: [], by_category: [], by_severity: [], over_time: [], bucket_seconds: 900, calculation_notes: {}, window_start: "", window_end: "" });
    mocked.dqSummary.mockResolvedValue({ total_received: 0, valid: 0, valid_late: 0, invalid: 0, duplicates: 0, by_category: [], over_time: [], bucket_seconds: 900, policy: {}, window_start: "", window_end: "" });
    mocked.alerts.mockResolvedValue(page([]));
    mocked.runs.mockResolvedValue(page([]));
    renderWithProviders(<OverviewPage />);
    expect(await screen.findByText("5,047")).toBeInTheDocument();
    expect(screen.getByText("1.86%")).toBeInTheDocument();
    expect(screen.getByText("of 5,376 received")).toBeInTheDocument();
    expect(screen.getByText("7 active alerts")).toBeInTheDocument();
    expect(screen.getByText("No data in this time range")).toBeInTheDocument();
  });
});

describe("Connection status", () => {
  it("reports when live updates are disabled", () => {
    renderWithProviders(<ConnectionStatus />, { prefs: { liveUpdates: false, pollingSeconds: 30 } });
    const status = screen.getByRole("status", { name: "Connection status" });
    expect(status).toHaveTextContent("Live updates off");
    expect(status).toHaveTextContent("Polling every 30s");
  });
});

describe("Historical analysis", () => {
  const evaluation = (model_name: string, trained: boolean, precision: number) => ({
    model_name,
    description: "d",
    params: {
      target: "machine_failure",
      features: ["product_type", "air_temp_k", "process_temp_k", "rotational_speed_rpm", "torque_nm", "tool_wear_min"],
      excluded_columns: ["twf", "hdf", "pwf", "osf", "rnf", "udi", "product_id"],
      trained,
      dataset_sha256: "dc6630cd9b1f0f85",
      library_versions: { "scikit-learn": "1.9.1" },
      split: { method: "stratified train_test_split", seed: 42, test_size: 0.25, train_rows: 7500, test_rows: 2500, train_positives: 254, test_positives: 85, train_prevalence: 0.0339, test_prevalence: 0.034 },
    },
    metrics: {
      threshold: 0.5,
      precision,
      recall: 0.729,
      f1: 0.785,
      pr_auc: 0.829,
      confusion_matrix: { tn: 2404, fp: 11, fn: 23, tp: 62 },
      recall_by_mode: { HDF: { failures: 30, detected: 29 } },
    },
    pr_curve: [{ recall: 1, precision: 0.03 }, { recall: 0.5, precision: 0.9 }],
    feature_importance: [{ feature: "torque_nm", mean: 0.3, std: 0.01 }],
    created_at: "2026-10-29T00:00:00Z",
  });

  it("explains how to import when the dataset is missing", async () => {
    const { HistoricalPage } = await import("./Historical");
    mocked.historicalSummary.mockRejectedValue(new ApiError(404, "not_found", "AI4I 2020 has not been imported"));
    renderWithProviders(<HistoricalPage />);
    expect(await screen.findByText("The AI4I 2020 dataset has not been imported yet")).toBeInTheDocument();
    expect(screen.getByText(/ai4i-setup/)).toBeInTheDocument();
  });

  it("shows provenance, the synthetic-data notice and test-set metrics", async () => {
    const { HistoricalPage } = await import("./Historical");
    mocked.historicalSummary.mockResolvedValue({
      dataset: {
        id: "i1",
        dataset: "ai4i2020",
        title: "AI4I 2020 Predictive Maintenance Dataset",
        source_url: "https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset",
        doi: "10.24432/C5HS5C",
        license: "Creative Commons Attribution 4.0 International (CC BY 4.0)",
        citation: "Matzka, S. (2020).",
        file_name: "ai4i2020.csv",
        file_sha256: "dc6630cd9b1f0f853922fad78a1b6436570d3f1ec863f1dd5c4340ac56bc8a8e",
        row_count: 10000,
        observations: { rows: 10000, machine_failures: 339, mode_counts: {}, failure_without_mode: 9, mode_without_failure: { RNF: 18 }, multi_mode_rows: 24, type_counts: {}, matches_published_checksum: true },
        imported_at: "2026-10-29T00:00:00Z",
      },
      rows: 10000,
      failures: 339,
      failure_rate_pct: 3.39,
      by_type: [{ product_type: "L", rows: 6000, failures: 235, failure_rate_pct: 3.92 }],
      by_mode: [{ key: "HDF", count: 115 }],
      notes: {},
    });
    mocked.failureAnalysis.mockResolvedValue({ feature: "tool_wear_min", label: "Tool wear", unit: "min", derived: false, bins: [{ lower: 0, upper: 10, rows: 100, failures: 2, failure_rate_pct: 2, modes: {} }] });
    mocked.failureRules.mockResolvedValue([{ mode: "HDF", rule: "process − air temperature < 8.6 K and speed < 1380 rpm", deterministic: true, rule_matches: 115, labeled: 115, true_positive: 115, false_positive: 0, false_negative: 0 }]);
    mocked.modelEvaluations.mockResolvedValue([evaluation("logistic_regression", true, 0.139), evaluation("gradient_boosting", true, 0.849), evaluation("documented_rules", false, 1)]);
    mocked.historicalRecords.mockResolvedValue(page([]));
    renderWithProviders(<HistoricalPage />);

    expect(await screen.findByText(/Historical benchmark, not live data/)).toBeInTheDocument();
    expect(screen.getByText("synthetic")).toBeInTheDocument();
    expect(screen.getByText("10.24432/C5HS5C")).toBeInTheDocument();
    expect(screen.getByText("18 rows flag RNF while Machine failure = 0")).toBeInTheDocument();
    expect(await screen.findByText("0.849")).toBeInTheDocument();
    expect(screen.getByText("0.139")).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument(); // PR-AUC not shown for the untrained rule baseline
    expect(screen.getByText("twf, hdf, pwf, osf, rnf, udi, product_id")).toBeInTheDocument();
    expect(screen.getByText("rule true 115 · labeled 115 · agree 115 · rule-only 0 · label-only 0")).toBeInTheDocument();
  });
});
