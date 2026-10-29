// Types mirroring the backend Pydantic schemas (factoryops/schemas.py).

export type EquipmentStatus = "running" | "idle" | "warning" | "critical" | "offline" | "maintenance";
export type Metric = "temperature" | "vibration" | "pressure" | "cycle_time";
export type RunStatus = "running" | "completed" | "aborted";
export type DefectCategory = "dimensional" | "surface" | "assembly" | "material" | "weld" | "other";
export type DefectSeverity = "minor" | "major" | "critical";
export type DefectStatus = "open" | "investigating" | "resolved" | "dismissed";
export type AlertSeverity = "warning" | "critical";
export type AlertStatus = "open" | "acknowledged" | "resolved";
export type AlertRule = "threshold_high" | "threshold_low" | "spc_3sigma" | "machine_fault";
export type DQCategory =
  | "malformed"
  | "missing_field"
  | "invalid_type"
  | "out_of_range"
  | "invalid_timestamp"
  | "unknown_equipment"
  | "duplicate"
  | "processing_error";

export const EQUIPMENT_STATUSES: EquipmentStatus[] = ["running", "idle", "warning", "critical", "offline", "maintenance"];
export const METRICS: Metric[] = ["temperature", "vibration", "pressure", "cycle_time"];
export const DEFECT_CATEGORIES: DefectCategory[] = ["dimensional", "surface", "assembly", "material", "weld", "other"];
export const DEFECT_SEVERITIES: DefectSeverity[] = ["minor", "major", "critical"];
export const DEFECT_STATUSES: DefectStatus[] = ["open", "investigating", "resolved", "dismissed"];
export const DQ_CATEGORIES: DQCategory[] = [
  "malformed",
  "missing_field",
  "invalid_type",
  "out_of_range",
  "invalid_timestamp",
  "unknown_equipment",
  "duplicate",
  "processing_error",
];

export const METRIC_META: Record<Metric, { label: string; unit: string; avgKey: keyof TelemetryBucket; field: keyof TelemetryPoint }> = {
  temperature: { label: "Temperature", unit: "°C", avgKey: "avg_temperature_c", field: "temperature_c" },
  vibration: { label: "Vibration", unit: "mm/s", avgKey: "avg_vibration_mm_s", field: "vibration_mm_s" },
  pressure: { label: "Pressure", unit: "bar", avgKey: "avg_pressure_bar", field: "pressure_bar" },
  cycle_time: { label: "Cycle time", unit: "s", avgKey: "avg_cycle_time_s", field: "cycle_time_s" },
};

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Threshold {
  metric: Metric;
  warning_low: number | null;
  warning_high: number | null;
  critical_low: number | null;
  critical_high: number | null;
}

export interface Equipment {
  id: string;
  code: string;
  name: string;
  station: string;
  equipment_type: string;
  status: EquipmentStatus;
  is_active: boolean;
  last_event_at: string | null;
  thresholds: Threshold[];
  created_at: string;
  updated_at: string;
}

export interface EquipmentInput {
  code?: string;
  name: string;
  station: string;
  equipment_type: string;
  status: EquipmentStatus;
  thresholds: Threshold[];
}

export interface EquipmentRef {
  id: string;
  code: string;
  name: string;
  station: string;
}

export interface Run {
  id: string;
  run_code: string;
  batch_id: string;
  equipment: EquipmentRef;
  started_at: string;
  ended_at: string | null;
  units_produced: number;
  defect_count: number;
  status: RunStatus;
  yield_pct: number | null;
  defect_rate_pct: number | null;
}

export interface RunDetail extends Run {
  telemetry: {
    event_count: number;
    avg_temperature_c: number | null;
    max_temperature_c: number | null;
    avg_vibration_mm_s: number | null;
    max_vibration_mm_s: number | null;
    avg_pressure_bar: number | null;
    avg_cycle_time_s: number | null;
  };
  defects: DefectBrief[];
}

export interface TelemetryPoint {
  event_id: string;
  equipment_id: string;
  event_time: string;
  received_at: string;
  temperature_c: number;
  vibration_mm_s: number;
  pressure_bar: number;
  cycle_time_s: number;
  machine_state: string;
  batch_id: string;
  is_late: boolean;
}

export interface TelemetryBucket {
  bucket: string;
  event_count: number;
  avg_temperature_c: number | null;
  max_temperature_c: number | null;
  avg_vibration_mm_s: number | null;
  max_vibration_mm_s: number | null;
  avg_pressure_bar: number | null;
  avg_cycle_time_s: number | null;
}

export interface TelemetrySummary {
  start: string;
  end: string;
  bucket_seconds: number;
  equipment_id: string | null;
  buckets: TelemetryBucket[];
}

export interface DefectBrief {
  id: string;
  category: DefectCategory;
  severity: DefectSeverity;
  status: DefectStatus;
  detected_at: string;
}

export interface Defect extends DefectBrief {
  description: string;
  source_event_id: string | null;
  equipment: EquipmentRef;
  production_run: { id: string; run_code: string; batch_id: string };
  created_at: string;
  updated_at: string;
}

export interface DefectNote {
  id: string;
  author: string;
  body: string;
  status_from: DefectStatus | null;
  status_to: DefectStatus | null;
  created_at: string;
}

export interface AlertBrief {
  id: string;
  metric: string;
  rule: AlertRule;
  severity: AlertSeverity;
  status: AlertStatus;
  observed_value: number | null;
  triggered_at: string;
}

export interface DefectDetail extends Defect {
  notes: DefectNote[];
  evidence: {
    window_start: string;
    window_end: string;
    source_event: TelemetryPoint | null;
    telemetry: TelemetryPoint[];
    alerts: AlertBrief[];
    thresholds: Threshold[];
  };
}

export interface Alert {
  id: string;
  equipment: EquipmentRef;
  event_id: string | null;
  metric: string;
  rule: AlertRule;
  rule_detail: string;
  observed_value: number | null;
  threshold_value: number | null;
  severity: AlertSeverity;
  status: AlertStatus;
  occurrence_count: number;
  triggered_at: string;
  last_seen_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

export interface AlertDetail extends Alert {
  history: { from_status: AlertStatus | null; to_status: AlertStatus; actor: string; note: string | null; changed_at: string }[];
  event: TelemetryPoint | null;
}

export interface DashboardSummary {
  generated_at: string;
  window_start: string;
  window_end: string;
  last_event_at: string | null;
  total_equipment: number;
  running_equipment: number;
  warning_or_critical_equipment: number;
  units_produced: number;
  defects: number;
  defect_rate_pct: number | null;
  events_received: number;
  events_rejected: number;
  open_alerts: number;
  status_distribution: { status: EquipmentStatus; count: number }[];
  calculation_notes: Record<string, string>;
}

export interface GroupCount {
  key: string;
  count: number;
}

export interface DefectAnalytics {
  window_start: string;
  window_end: string;
  by_category: GroupCount[];
  by_severity: GroupCount[];
  by_station: { station: string; units_produced: number; defects: number; defect_rate_pct: number | null }[];
  over_time: { bucket: string; count: number }[];
  bucket_seconds: number;
  calculation_notes: Record<string, string>;
}

export interface EquipmentAnalyticsRow {
  equipment_id: string;
  code: string;
  name: string;
  station: string;
  status: EquipmentStatus;
  units_produced: number;
  defects: number;
  defect_rate_pct: number | null;
  yield_pct: number | null;
  alert_count: number;
  event_count: number;
  avg_temperature_c: number | null;
  avg_vibration_mm_s: number | null;
}

export interface ControlChart {
  equipment_id: string;
  metric: Metric;
  unit: string;
  method: string;
  baseline_source: "stored" | "window" | "insufficient_data";
  center_line: number | null;
  sigma: number | null;
  ucl: number | null;
  lcl: number | null;
  baseline_sample_size: number;
  points: { event_time: string; value: number; out_of_control: boolean; rule: string | null }[];
  explanation: string;
}

export interface DataQualitySummary {
  window_start: string;
  window_end: string;
  total_received: number;
  valid: number;
  valid_late: number;
  invalid: number;
  duplicates: number;
  by_category: GroupCount[];
  over_time: { bucket: string; accepted: number; accepted_late: number; rejected: number; duplicate: number }[];
  bucket_seconds: number;
  policy: Record<string, string>;
}

export interface DataQualityFailure {
  id: string;
  event_id: string | null;
  equipment_code: string | null;
  received_at: string;
  category: DQCategory;
  field: string | null;
  reason: string;
  payload_excerpt: Record<string, unknown> | null;
  source: string | null;
}

export interface PublicSettings {
  environment: string;
  demo_controls_enabled: boolean;
  late_event_seconds: number;
  max_clock_skew_seconds: number;
  max_event_age_seconds: number;
  alert_cooldown_seconds: number;
  processing_max_retries: number;
}

export type Scenario =
  | "normal"
  | "temperature_drift"
  | "vibration_spike"
  | "shutdown"
  | "out_of_range"
  | "missing_fields"
  | "duplicates"
  | "out_of_order"
  | "defects"
  | "mixed";

export interface LiveMessage {
  type: string;
  ts: string;
  data: Record<string, unknown>;
}

// ---------------------------------------------------------------- historical benchmark (AI4I 2020)

export interface DatasetImport {
  id: string;
  dataset: string;
  title: string;
  source_url: string;
  doi: string | null;
  license: string;
  citation: string;
  file_name: string;
  file_sha256: string;
  row_count: number;
  observations: {
    rows: number;
    machine_failures: number;
    mode_counts: Record<string, number>;
    failure_without_mode: number;
    mode_without_failure: Record<string, number>;
    multi_mode_rows: number;
    type_counts: Record<string, number>;
    matches_published_checksum?: boolean;
  };
  imported_at: string;
}

export interface HistoricalSummary {
  dataset: DatasetImport;
  rows: number;
  failures: number;
  failure_rate_pct: number | null;
  by_type: { product_type: string; rows: number; failures: number; failure_rate_pct: number | null }[];
  by_mode: GroupCount[];
  notes: Record<string, string>;
}

export type HistoricalFeature =
  | "air_temp_k"
  | "process_temp_k"
  | "rotational_speed_rpm"
  | "torque_nm"
  | "tool_wear_min"
  | "temp_diff_k"
  | "power_w"
  | "strain_min_nm";

export const HISTORICAL_FEATURES: { value: HistoricalFeature; label: string }[] = [
  { value: "tool_wear_min", label: "Tool wear [min]" },
  { value: "torque_nm", label: "Torque [Nm]" },
  { value: "rotational_speed_rpm", label: "Rotational speed [rpm]" },
  { value: "air_temp_k", label: "Air temperature [K]" },
  { value: "process_temp_k", label: "Process temperature [K]" },
  { value: "temp_diff_k", label: "Derived: process − air temperature [K]" },
  { value: "power_w", label: "Derived: power [W]" },
  { value: "strain_min_nm", label: "Derived: tool wear × torque [min·Nm]" },
];

export interface FailureAnalysis {
  feature: HistoricalFeature;
  label: string;
  unit: string;
  derived: boolean;
  bins: { lower: number; upper: number; rows: number; failures: number; failure_rate_pct: number | null; modes: Record<string, number> }[];
}

export interface RuleCheck {
  mode: string;
  rule: string;
  deterministic: boolean;
  rule_matches: number;
  labeled: number;
  true_positive: number;
  false_positive: number;
  false_negative: number;
}

export interface Ai4iRecord {
  udi: number;
  product_id: string;
  product_type: string;
  air_temp_k: number;
  process_temp_k: number;
  rotational_speed_rpm: number;
  torque_nm: number;
  tool_wear_min: number;
  temp_diff_k: number;
  power_w: number;
  strain_min_nm: number;
  machine_failure: boolean;
  twf: boolean;
  hdf: boolean;
  pwf: boolean;
  osf: boolean;
  rnf: boolean;
}

export interface ModelEvaluation {
  model_name: string;
  description: string;
  params: {
    target: string;
    features: string[];
    excluded_columns: string[];
    trained: boolean;
    dataset_sha256?: string;
    library_versions: Record<string, string>;
    split: {
      method: string;
      seed: number;
      test_size: number;
      train_rows: number;
      test_rows: number;
      train_positives: number;
      test_positives: number;
      train_prevalence: number;
      test_prevalence: number;
    };
  };
  metrics: {
    threshold: number;
    precision: number;
    recall: number;
    f1: number;
    pr_auc: number;
    confusion_matrix: { tn: number; fp: number; fn: number; tp: number };
    recall_by_mode: Record<string, { failures: number; detected: number }>;
  };
  pr_curve: { recall: number; precision: number }[];
  feature_importance: { feature: string; mean: number; std: number }[];
  created_at: string;
}
