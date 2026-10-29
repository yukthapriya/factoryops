"""HTTP routes. Handlers only parse input, call services and shape output."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from factoryops.config import get_settings
from factoryops.db import get_db, get_sessionmaker
from factoryops.domain import (
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
from factoryops.errors import DomainError
from factoryops.live import hub
from factoryops.schemas import (
    Ai4iRecordOut,
    AlertAction,
    AlertDetail,
    AlertOut,
    ControlChart,
    DashboardSummary,
    DataQualityFailureOut,
    DataQualitySummary,
    DatasetImportOut,
    DefectAnalytics,
    DefectDetail,
    DefectNoteCreate,
    DefectNoteOut,
    DefectOut,
    DefectUpdate,
    EquipmentAnalytics,
    EquipmentCreate,
    EquipmentOut,
    EquipmentUpdate,
    ErrorResponse,
    FailureAnalysis,
    HealthStatus,
    HistoricalSummary,
    ModelEvaluationOut,
    Page,
    RuleCheck,
    RunDetail,
    RunOut,
    SimulateRequest,
    SimulateResult,
    TelemetryPoint,
    TelemetrySummary,
)
from factoryops.services import alerts as alert_svc
from factoryops.services import defects as defect_svc
from factoryops.services import equipment as eq_svc
from factoryops.services import historical as hist_svc
from factoryops.services import insights
from factoryops.services import production as run_svc
from factoryops.services.common import resolve_window

DB = Annotated[Session, Depends(get_db)]
PageNo = Annotated[int, Query(ge=1, le=100_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Start = Annotated[datetime | None, Query(description="ISO-8601 with timezone; default end - 24h")]
End = Annotated[datetime | None, Query(description="ISO-8601 with timezone; default now")]
Search = Annotated[str | None, Query(max_length=100)]
Sort = Annotated[str | None, Query(max_length=40, description="field or -field")]

ERRORS = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

api = APIRouter(prefix="/api", responses=ERRORS)


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


# ---------------------------------------------------------------- health


@api.get("/health/live", response_model=HealthStatus, tags=["health"])
def live() -> HealthStatus:
    """Process is up. Does not touch dependencies."""
    return HealthStatus(status="ok")


@api.get(
    "/health/ready",
    response_model=HealthStatus,
    tags=["health"],
    responses={503: {"model": HealthStatus}},
)
def ready(response: Response) -> HealthStatus:
    """Ready to serve traffic: database reachable and migrated."""
    checks: dict[str, str] = {}
    try:
        with get_sessionmaker()() as session:
            version = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["database"] = "ok"
        checks["migration"] = version or "none"
    except Exception as exc:
        checks["database"] = f"unavailable: {type(exc).__name__}"
    checks["live_listener"] = "connected" if hub.listening else "disconnected"
    ok = checks.get("database") == "ok"
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthStatus(status="ok" if ok else "unavailable", checks=checks)


# ---------------------------------------------------------------- equipment


@api.get("/equipment", response_model=Page[EquipmentOut], tags=["equipment"])
def list_equipment(
    db: DB,
    q: Search = None,
    status_: Annotated[EquipmentStatus | None, Query(alias="status")] = None,
    station: Annotated[str | None, Query(max_length=64)] = None,
    include_inactive: bool = False,
    sort: Sort = None,
    page: PageNo = 1,
    page_size: PageSize = 25,
):
    items, total = eq_svc.list_equipment(
        db,
        q=q,
        status=status_,
        station=station,
        include_inactive=include_inactive,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@api.get("/equipment/stations", response_model=list[str], tags=["equipment"])
def stations(db: DB):
    return eq_svc.list_stations(db)


@api.post("/equipment", response_model=EquipmentOut, status_code=201, tags=["equipment"])
def create_equipment(body: EquipmentCreate, db: DB):
    return eq_svc.create_equipment(db, body)


@api.get("/equipment/{equipment_id}", response_model=EquipmentOut, tags=["equipment"])
def get_equipment(equipment_id: uuid.UUID, db: DB):
    return eq_svc.get_equipment(db, equipment_id)


@api.patch("/equipment/{equipment_id}", response_model=EquipmentOut, tags=["equipment"])
def update_equipment(equipment_id: uuid.UUID, body: EquipmentUpdate, db: DB):
    return eq_svc.update_equipment(db, equipment_id, body)


@api.delete("/equipment/{equipment_id}", response_model=EquipmentOut, tags=["equipment"])
def deactivate_equipment(equipment_id: uuid.UUID, db: DB):
    """Soft-deletes (deactivates) equipment; its history is retained."""
    return eq_svc.deactivate_equipment(db, equipment_id)


# ---------------------------------------------------------------- production runs


@api.get("/production-runs", response_model=Page[RunOut], tags=["production"])
def list_runs(
    db: DB,
    q: Search = None,
    status_: Annotated[RunStatus | None, Query(alias="status")] = None,
    equipment_id: uuid.UUID | None = None,
    station: Annotated[str | None, Query(max_length=64)] = None,
    start: Start = None,
    end: End = None,
    sort: Sort = None,
    page: PageNo = 1,
    page_size: PageSize = 25,
):
    if start or end:
        start, end = resolve_window(start, end, default=timedelta(days=7))
    items, total = run_svc.list_runs(
        db,
        q=q,
        status=status_,
        equipment_id=equipment_id,
        station=station,
        start=start,
        end=end,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@api.get("/production-runs/{run_id}", response_model=RunDetail, tags=["production"])
def get_run(run_id: uuid.UUID, db: DB):
    return run_svc.get_run(db, run_id)


# ---------------------------------------------------------------- defects


@api.get("/defects", response_model=Page[DefectOut], tags=["defects"])
def list_defects(
    db: DB,
    q: Search = None,
    status_: Annotated[DefectStatus | None, Query(alias="status")] = None,
    category: DefectCategory | None = None,
    severity: DefectSeverity | None = None,
    station: Annotated[str | None, Query(max_length=64)] = None,
    equipment_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    start: Start = None,
    end: End = None,
    sort: Sort = None,
    page: PageNo = 1,
    page_size: PageSize = 25,
):
    if start or end:
        start, end = resolve_window(start, end)
    items, total = defect_svc.list_defects(
        db,
        q=q,
        status=status_,
        category=category,
        severity=severity,
        station=station,
        equipment_id=equipment_id,
        run_id=run_id,
        start=start,
        end=end,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@api.get("/defects/{defect_id}", response_model=DefectDetail, tags=["defects"])
def get_defect(defect_id: uuid.UUID, db: DB):
    return defect_svc.get_defect(db, defect_id)


@api.patch("/defects/{defect_id}", response_model=DefectDetail, tags=["defects"])
def update_defect(defect_id: uuid.UUID, body: DefectUpdate, db: DB):
    return defect_svc.update_defect(db, defect_id, body)


@api.post(
    "/defects/{defect_id}/notes", response_model=DefectNoteOut, status_code=201, tags=["defects"]
)
def add_note(defect_id: uuid.UUID, body: DefectNoteCreate, db: DB):
    return defect_svc.add_note(db, defect_id, body)


# ---------------------------------------------------------------- alerts


@api.get("/alerts", response_model=Page[AlertOut], tags=["alerts"])
def list_alerts(
    db: DB,
    status_: Annotated[
        str | None,
        Query(alias="status", pattern="^(open|acknowledged|resolved|active)$"),
    ] = None,
    severity: AlertSeverity | None = None,
    equipment_id: uuid.UUID | None = None,
    metric: Annotated[str | None, Query(max_length=16)] = None,
    rule: AlertRule | None = None,
    start: Start = None,
    end: End = None,
    sort: Sort = None,
    page: PageNo = 1,
    page_size: PageSize = 25,
):
    if start or end:
        start, end = resolve_window(start, end)
    items, total = alert_svc.list_alerts(
        db,
        status=status_,
        severity=severity,
        equipment_id=equipment_id,
        metric=metric,
        rule=rule,
        start=start,
        end=end,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@api.get("/alerts/{alert_id}", response_model=AlertDetail, tags=["alerts"])
def get_alert(alert_id: uuid.UUID, db: DB):
    return alert_svc.get_alert(db, alert_id)


@api.post("/alerts/{alert_id}/acknowledge", response_model=AlertDetail, tags=["alerts"])
def acknowledge_alert(alert_id: uuid.UUID, body: AlertAction, db: DB):
    return alert_svc.transition(db, alert_id, AlertStatus.ACKNOWLEDGED, body)


@api.post("/alerts/{alert_id}/resolve", response_model=AlertDetail, tags=["alerts"])
def resolve_alert(alert_id: uuid.UUID, body: AlertAction, db: DB):
    return alert_svc.transition(db, alert_id, AlertStatus.RESOLVED, body)


# ---------------------------------------------------------------- telemetry & analytics


@api.get("/telemetry", response_model=list[TelemetryPoint], tags=["telemetry"])
def telemetry(
    db: DB,
    equipment_id: uuid.UUID | None = None,
    start: Start = None,
    end: End = None,
    late_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    start, end = resolve_window(start, end, default=timedelta(hours=1))
    return insights.list_telemetry(db, equipment_id, start, end, limit, late_only)


@api.get("/telemetry/summary", response_model=TelemetrySummary, tags=["telemetry"])
def telemetry_summary(
    db: DB, equipment_id: uuid.UUID | None = None, start: Start = None, end: End = None
):
    start, end = resolve_window(start, end)
    return insights.telemetry_summary(db, equipment_id, start, end)


@api.get("/dashboard/summary", response_model=DashboardSummary, tags=["analytics"])
def dashboard(db: DB, start: Start = None, end: End = None):
    start, end = resolve_window(start, end)
    return insights.dashboard_summary(db, start, end)


@api.get("/analytics/defects", response_model=DefectAnalytics, tags=["analytics"])
def defect_analytics(db: DB, start: Start = None, end: End = None):
    start, end = resolve_window(start, end)
    return insights.defect_analytics(db, start, end)


@api.get("/analytics/equipment", response_model=EquipmentAnalytics, tags=["analytics"])
def equipment_analytics(db: DB, start: Start = None, end: End = None):
    start, end = resolve_window(start, end)
    return insights.equipment_analytics(db, start, end)


@api.get("/analytics/control-chart", response_model=ControlChart, tags=["analytics"])
def control_chart(
    db: DB, equipment_id: uuid.UUID, metric: Metric, start: Start = None, end: End = None
):
    start, end = resolve_window(
        start, end, default=timedelta(hours=6), max_window=timedelta(days=7)
    )
    return insights.control_chart(db, equipment_id, metric, start, end)


# ---------------------------------------------------------------- data quality


@api.get("/data-quality/summary", response_model=DataQualitySummary, tags=["data-quality"])
def dq_summary(db: DB, start: Start = None, end: End = None):
    start, end = resolve_window(start, end)
    return insights.data_quality_summary(db, start, end)


@api.get(
    "/data-quality/failures", response_model=Page[DataQualityFailureOut], tags=["data-quality"]
)
def dq_failures(
    db: DB,
    category: DQCategory | None = None,
    equipment_code: Annotated[str | None, Query(max_length=64)] = None,
    start: Start = None,
    end: End = None,
    page: PageNo = 1,
    page_size: PageSize = 25,
):
    start, end = resolve_window(start, end)
    items, total = insights.list_failures(
        db,
        category=category,
        equipment_code=equipment_code,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------- demo controls


@api.post("/demo/simulate", response_model=SimulateResult, tags=["demo"])
def simulate(body: SimulateRequest):
    """Inject a synthetic scenario through the real processing pipeline (in-process).

    Disabled unless FACTORYOPS_DEMO_CONTROLS_ENABLED=true. Events are generated with
    timestamps ending now, one tick per second.
    """
    if not get_settings().demo_controls_enabled:
        raise Forbidden("demo controls are disabled on this deployment")
    from factoryops.simulator.direct import replay
    from factoryops.simulator.generator import TelemetryGenerator

    seed = body.seed if body.seed is not None else int(datetime.now(UTC).timestamp())
    start = datetime.now(UTC) - timedelta(seconds=body.ticks)
    gen = TelemetryGenerator(seed=seed, scenario=body.scenario, start=start, interval_s=1.0)
    messages = list(gen.run(body.ticks))
    outcomes = replay(get_sessionmaker(), messages)
    return SimulateResult(
        scenario=body.scenario, events_sent=len(messages), outcomes=dict(outcomes)
    )


@api.post("/demo/baselines", tags=["demo"])
def recompute_baselines(db: DB, hours: Annotated[int, Query(ge=1, le=168)] = 6):
    """Recompute SPC baselines from the last N hours of running-state telemetry."""
    if not get_settings().demo_controls_enabled:
        raise Forbidden("demo controls are disabled on this deployment")
    end = datetime.now(UTC)
    count = insights.compute_baselines(db, end - timedelta(hours=hours), end)
    return {"baselines_updated": count}


@api.get("/settings/public", tags=["settings"])
def public_settings():
    """Non-secret runtime settings the UI may display."""
    s = get_settings()
    return {
        "environment": s.env,
        "demo_controls_enabled": s.demo_controls_enabled,
        "late_event_seconds": s.late_event_seconds,
        "max_clock_skew_seconds": s.max_clock_skew_seconds,
        "max_event_age_seconds": s.max_event_age_seconds,
        "alert_cooldown_seconds": s.alert_cooldown_seconds,
        "processing_max_retries": s.processing_max_retries,
    }


# ---------------------------------------------------------------- historical benchmark (AI4I 2020)


@api.get("/historical/datasets", response_model=list[DatasetImportOut], tags=["historical"])
def historical_datasets(db: DB):
    """Imported historical datasets with source attribution (empty list if none)."""
    return hist_svc.list_imports(db)


@api.get("/historical/summary", response_model=HistoricalSummary, tags=["historical"])
def historical_summary(db: DB):
    return hist_svc.summary(db)


@api.get("/historical/failure-analysis", response_model=FailureAnalysis, tags=["historical"])
def historical_failure_analysis(
    db: DB,
    feature: hist_svc.Feature = hist_svc.Feature.TORQUE,
    bins: Annotated[int, Query(ge=4, le=40)] = 12,
):
    """Failure rate per equal-width bin of one process variable (computed in SQL)."""
    return hist_svc.failure_analysis(db, feature, bins)


@api.get("/historical/failure-rules", response_model=list[RuleCheck], tags=["historical"])
def historical_failure_rules(db: DB):
    """UCI-documented failure-mode rules compared with the labels in the imported file."""
    return hist_svc.rule_checks(db)


@api.get("/historical/records", response_model=Page[Ai4iRecordOut], tags=["historical"])
def historical_records(
    db: DB,
    product_type: Annotated[str | None, Query(pattern="^[LMH]$")] = None,
    failure: bool | None = None,
    mode: Annotated[str | None, Query(pattern="^(TWF|HDF|PWF|OSF|RNF)$")] = None,
    page: PageNo = 1,
    page_size: PageSize = 25,
):
    items, total = hist_svc.list_records(
        db, product_type=product_type, failure=failure, mode=mode, page=page, page_size=page_size
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@api.get(
    "/historical/model-evaluations", response_model=list[ModelEvaluationOut], tags=["historical"]
)
def historical_model_evaluations(db: DB):
    """Persisted offline evaluations (run `cli ai4i-evaluate`); empty list if not evaluated."""
    return hist_svc.evaluations(db)
