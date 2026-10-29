"""Pydantic request/response schemas (the public API contract)."""

import uuid
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from factoryops.domain import (
    PLAUSIBLE_RANGES,
    AlertRule,
    AlertSeverity,
    AlertStatus,
    DefectCategory,
    DefectSeverity,
    DefectStatus,
    DQCategory,
    EquipmentStatus,
    Metric,
    RunStatus,
)

T = TypeVar("T")

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------- equipment


class Threshold(ORM):
    metric: Metric
    warning_low: float | None = None
    warning_high: float | None = None
    critical_low: float | None = None
    critical_high: float | None = None

    @model_validator(mode="after")
    def _check(self) -> "Threshold":
        lo, hi = PLAUSIBLE_RANGES[self.metric]
        values = {
            "warning_low": self.warning_low,
            "warning_high": self.warning_high,
            "critical_low": self.critical_low,
            "critical_high": self.critical_high,
        }
        if all(v is None for v in values.values()):
            raise ValueError(f"{self.metric}: at least one limit must be set")
        for name, v in values.items():
            if v is not None and not lo <= v <= hi:
                raise ValueError(
                    f"{self.metric}.{name}={v} is outside the plausible sensor range [{lo}, {hi}]"
                )
        if (
            self.warning_high is not None
            and self.critical_high is not None
            and self.warning_high > self.critical_high
        ):
            raise ValueError(f"{self.metric}: warning_high must be <= critical_high")
        if (
            self.warning_low is not None
            and self.critical_low is not None
            and self.warning_low < self.critical_low
        ):
            raise ValueError(f"{self.metric}: warning_low must be >= critical_low")
        lows = [v for v in (self.warning_low, self.critical_low) if v is not None]
        highs = [v for v in (self.warning_high, self.critical_high) if v is not None]
        if lows and highs and max(lows) >= min(highs):
            raise ValueError(f"{self.metric}: low limits must be below high limits")
        return self


def _unique_metrics(thresholds: list[Threshold] | None) -> None:
    if thresholds is None:
        return
    metrics = [t.metric for t in thresholds]
    if len(metrics) != len(set(metrics)):
        raise ValueError("each metric may appear at most once in thresholds")


EquipmentCode = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z0-9][A-Z0-9-]{1,31}$")
]


class EquipmentCreate(BaseModel):
    code: EquipmentCode = Field(description="Human-readable unique ID, e.g. PRESS-01")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    station: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    equipment_type: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    status: EquipmentStatus = EquipmentStatus.IDLE
    thresholds: list[Threshold] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "EquipmentCreate":
        _unique_metrics(self.thresholds)
        return self


class EquipmentUpdate(BaseModel):
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
        | None
    ) = None
    station: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None
    ) = None
    equipment_type: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] | None
    ) = None
    status: EquipmentStatus | None = None
    is_active: bool | None = None
    thresholds: list[Threshold] | None = Field(
        default=None, description="If provided, replaces the full threshold set"
    )

    @model_validator(mode="after")
    def _check(self) -> "EquipmentUpdate":
        _unique_metrics(self.thresholds)
        return self


class EquipmentOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    station: str
    equipment_type: str
    status: EquipmentStatus
    is_active: bool
    last_event_at: datetime | None
    thresholds: list[Threshold]
    created_at: datetime
    updated_at: datetime


class EquipmentRef(ORM):
    id: uuid.UUID
    code: str
    name: str
    station: str


# ---------------------------------------------------------------- production runs


class RunOut(ORM):
    id: uuid.UUID
    run_code: str
    batch_id: str
    equipment: EquipmentRef
    started_at: datetime
    ended_at: datetime | None
    units_produced: int
    defect_count: int
    status: RunStatus
    yield_pct: float | None = Field(description="(units - defects) / units * 100; null if 0 units")
    defect_rate_pct: float | None = Field(description="defects / units * 100; null if 0 units")


class RunTelemetryStats(BaseModel):
    event_count: int
    avg_temperature_c: float | None
    max_temperature_c: float | None
    avg_vibration_mm_s: float | None
    max_vibration_mm_s: float | None
    avg_pressure_bar: float | None
    avg_cycle_time_s: float | None


class DefectBrief(ORM):
    id: uuid.UUID
    category: DefectCategory
    severity: DefectSeverity
    status: DefectStatus
    detected_at: datetime


class RunDetail(RunOut):
    telemetry: RunTelemetryStats
    defects: list[DefectBrief]


# ---------------------------------------------------------------- telemetry


class TelemetryPoint(ORM):
    event_id: uuid.UUID
    equipment_id: uuid.UUID
    event_time: datetime
    received_at: datetime
    temperature_c: float
    vibration_mm_s: float
    pressure_bar: float
    cycle_time_s: float
    machine_state: str
    batch_id: str
    is_late: bool


class TelemetryBucket(BaseModel):
    bucket: datetime
    event_count: int
    avg_temperature_c: float | None
    max_temperature_c: float | None
    avg_vibration_mm_s: float | None
    max_vibration_mm_s: float | None
    avg_pressure_bar: float | None
    avg_cycle_time_s: float | None


class TelemetrySummary(BaseModel):
    start: datetime
    end: datetime
    bucket_seconds: int
    equipment_id: uuid.UUID | None
    buckets: list[TelemetryBucket]


# ---------------------------------------------------------------- defects


class DefectNoteCreate(BaseModel):
    author: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DefectNoteOut(ORM):
    id: uuid.UUID
    author: str
    body: str
    status_from: DefectStatus | None
    status_to: DefectStatus | None
    created_at: datetime


class RunRef(ORM):
    id: uuid.UUID
    run_code: str
    batch_id: str


class DefectOut(ORM):
    id: uuid.UUID
    category: DefectCategory
    severity: DefectSeverity
    status: DefectStatus
    description: str
    detected_at: datetime
    source_event_id: uuid.UUID | None
    equipment: EquipmentRef
    production_run: RunRef
    created_at: datetime
    updated_at: datetime


class AlertBrief(ORM):
    id: uuid.UUID
    metric: str
    rule: AlertRule
    severity: AlertSeverity
    status: AlertStatus
    observed_value: float | None
    triggered_at: datetime


class DefectEvidence(BaseModel):
    """Raw evidence surrounding a defect. No automated root-cause inference is made."""

    window_start: datetime
    window_end: datetime
    source_event: TelemetryPoint | None
    telemetry: list[TelemetryPoint]
    alerts: list[AlertBrief]
    thresholds: list[Threshold]


class DefectDetail(DefectOut):
    notes: list[DefectNoteOut]
    evidence: DefectEvidence


class DefectUpdate(BaseModel):
    status: DefectStatus | None = None
    severity: DefectSeverity | None = None
    author: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] | None = None

    @model_validator(mode="after")
    def _check(self) -> "DefectUpdate":
        if self.status is None and self.severity is None:
            raise ValueError("provide status and/or severity")
        if self.status in (DefectStatus.RESOLVED, DefectStatus.DISMISSED) and not self.note:
            raise ValueError("a note explaining the evidence is required to resolve or dismiss")
        return self


# ---------------------------------------------------------------- alerts


class AlertHistoryOut(ORM):
    from_status: AlertStatus | None
    to_status: AlertStatus
    actor: str
    note: str | None
    changed_at: datetime


class AlertOut(ORM):
    id: uuid.UUID
    equipment: EquipmentRef
    event_id: uuid.UUID | None
    metric: str
    rule: AlertRule
    rule_detail: str
    observed_value: float | None
    threshold_value: float | None
    severity: AlertSeverity
    status: AlertStatus
    occurrence_count: int
    triggered_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None


class AlertDetail(AlertOut):
    history: list[AlertHistoryOut]
    event: TelemetryPoint | None


class AlertAction(BaseModel):
    actor: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


# ---------------------------------------------------------------- dashboard / analytics


class StatusCount(BaseModel):
    status: str
    count: int


class DashboardSummary(BaseModel):
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    last_event_at: datetime | None
    total_equipment: int
    running_equipment: int
    warning_or_critical_equipment: int
    units_produced: int
    defects: int
    defect_rate_pct: float | None
    events_received: int
    events_rejected: int
    open_alerts: int
    status_distribution: list[StatusCount]
    calculation_notes: dict[str, str]


class GroupCount(BaseModel):
    key: str
    count: int


class StationDefectRate(BaseModel):
    station: str
    units_produced: int
    defects: int
    defect_rate_pct: float | None


class TimeCount(BaseModel):
    bucket: datetime
    count: int


class DefectAnalytics(BaseModel):
    window_start: datetime
    window_end: datetime
    by_category: list[GroupCount]
    by_severity: list[GroupCount]
    by_station: list[StationDefectRate]
    over_time: list[TimeCount]
    bucket_seconds: int
    calculation_notes: dict[str, str]


class EquipmentAnalyticsRow(BaseModel):
    equipment_id: uuid.UUID
    code: str
    name: str
    station: str
    status: str
    units_produced: int
    defects: int
    defect_rate_pct: float | None
    yield_pct: float | None
    alert_count: int
    event_count: int
    avg_temperature_c: float | None
    avg_vibration_mm_s: float | None


class EquipmentAnalytics(BaseModel):
    window_start: datetime
    window_end: datetime
    rows: list[EquipmentAnalyticsRow]


class ControlChartPoint(BaseModel):
    event_time: datetime
    value: float
    out_of_control: bool
    rule: str | None


class ControlChart(BaseModel):
    equipment_id: uuid.UUID
    metric: Metric
    unit: str
    method: str
    baseline_source: Literal["stored", "window", "insufficient_data"]
    center_line: float | None
    sigma: float | None
    ucl: float | None
    lcl: float | None
    baseline_sample_size: int
    points: list[ControlChartPoint]
    explanation: str


# ---------------------------------------------------------------- data quality


class DataQualitySummary(BaseModel):
    window_start: datetime
    window_end: datetime
    total_received: int
    valid: int
    valid_late: int
    invalid: int
    duplicates: int
    by_category: list[GroupCount]
    over_time: list[dict]
    bucket_seconds: int
    policy: dict[str, str]


class DataQualityFailureOut(ORM):
    id: uuid.UUID
    event_id: str | None
    equipment_code: str | None
    received_at: datetime
    category: DQCategory
    field: str | None
    reason: str
    payload_excerpt: dict | None
    source: str | None


# ---------------------------------------------------------------- demo / health


class SimulateRequest(BaseModel):
    scenario: Literal[
        "normal",
        "temperature_drift",
        "vibration_spike",
        "shutdown",
        "out_of_range",
        "missing_fields",
        "duplicates",
        "out_of_order",
        "defects",
        "mixed",
    ] = "mixed"
    ticks: int = Field(default=30, ge=1, le=300)
    seed: int | None = Field(default=None, ge=0)


class SimulateResult(BaseModel):
    scenario: str
    events_sent: int
    outcomes: dict[str, int]


class HealthStatus(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, str] = {}


# ---------------------------------------------------------------- historical benchmark


class DatasetImportOut(ORM):
    id: uuid.UUID
    dataset: str
    title: str
    source_url: str
    doi: str | None
    license: str
    citation: str
    file_name: str
    file_sha256: str
    row_count: int
    observations: dict
    imported_at: datetime


class TypeBreakdown(BaseModel):
    product_type: str
    rows: int
    failures: int
    failure_rate_pct: float | None


class HistoricalSummary(BaseModel):
    dataset: DatasetImportOut
    rows: int
    failures: int
    failure_rate_pct: float | None
    by_type: list[TypeBreakdown]
    by_mode: list[GroupCount]
    notes: dict[str, str]


class FailureBin(BaseModel):
    lower: float
    upper: float
    rows: int
    failures: int
    failure_rate_pct: float | None
    modes: dict[str, int]


class FailureAnalysis(BaseModel):
    feature: str
    label: str
    unit: str
    derived: bool
    bins: list[FailureBin]


class RuleCheck(BaseModel):
    mode: str
    rule: str
    deterministic: bool
    rule_matches: int
    labeled: int
    true_positive: int
    false_positive: int
    false_negative: int


class Ai4iRecordOut(ORM):
    udi: int
    product_id: str
    product_type: str
    air_temp_k: float
    process_temp_k: float
    rotational_speed_rpm: int
    torque_nm: float
    tool_wear_min: int
    temp_diff_k: float
    power_w: float
    strain_min_nm: float
    machine_failure: bool
    twf: bool
    hdf: bool
    pwf: bool
    osf: bool
    rnf: bool


class ModelEvaluationOut(ORM):
    model_name: str
    description: str
    params: dict
    metrics: dict
    pr_curve: list
    feature_importance: list
    created_at: datetime
