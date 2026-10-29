"""Read-side aggregations. All aggregation happens in SQL; nothing loads full history."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from factoryops.analytics import (
    IndividualsBaseline,
    classify_points,
    defect_rate_pct,
    estimate_individuals_baseline,
    yield_pct,
)
from factoryops.config import get_settings
from factoryops.domain import (
    METRIC_COLUMNS,
    METRIC_UNITS,
    AlertStatus,
    DQCategory,
    EquipmentStatus,
    IngestOutcome,
    MachineState,
    Metric,
)
from factoryops.errors import NotFound
from factoryops.models import (
    Alert,
    DataQualityFailure,
    Defect,
    Equipment,
    IngestionStat,
    SpcBaseline,
    TelemetryEvent,
)
from factoryops.schemas import (
    ControlChart,
    ControlChartPoint,
    DashboardSummary,
    DataQualitySummary,
    DefectAnalytics,
    EquipmentAnalytics,
    EquipmentAnalyticsRow,
    GroupCount,
    StationDefectRate,
    StatusCount,
    TelemetryBucket,
    TelemetrySummary,
    TimeCount,
)
from factoryops.services.common import bucket_expr, bucket_seconds_for

UNITS_NOTE = (
    "Units = accepted telemetry events with machine_state='running' in the window "
    "(the simulator emits one event per completed cycle)."
)
DEFECT_RATE_NOTE = "Defect rate = defects detected in window / units in window × 100."

_running = TelemetryEvent.machine_state == MachineState.RUNNING.value


# ---------------------------------------------------------------- telemetry


def list_telemetry(
    session: Session,
    equipment_id: uuid.UUID | None,
    start: datetime,
    end: datetime,
    limit: int,
    late_only: bool = False,
):
    stmt = select(TelemetryEvent).where(
        TelemetryEvent.event_time >= start, TelemetryEvent.event_time < end
    )
    if equipment_id:
        stmt = stmt.where(TelemetryEvent.equipment_id == equipment_id)
    if late_only:
        stmt = stmt.where(TelemetryEvent.is_late.is_(True))
    return session.scalars(stmt.order_by(TelemetryEvent.event_time.desc()).limit(limit)).all()


def telemetry_summary(
    session: Session, equipment_id: uuid.UUID | None, start: datetime, end: datetime
) -> TelemetrySummary:
    seconds = bucket_seconds_for(start, end)
    b = bucket_expr(TelemetryEvent.event_time, seconds, start).label("bucket")
    stmt = (
        select(
            b,
            func.count(),
            func.avg(TelemetryEvent.temperature_c),
            func.max(TelemetryEvent.temperature_c),
            func.avg(TelemetryEvent.vibration_mm_s),
            func.max(TelemetryEvent.vibration_mm_s),
            func.avg(TelemetryEvent.pressure_bar),
            func.avg(TelemetryEvent.cycle_time_s),
        )
        .where(TelemetryEvent.event_time >= start, TelemetryEvent.event_time < end)
        .group_by(b)
        .order_by(b)
    )
    if equipment_id:
        stmt = stmt.where(TelemetryEvent.equipment_id == equipment_id)
    buckets = [
        TelemetryBucket(
            bucket=r[0],
            event_count=r[1],
            avg_temperature_c=r[2],
            max_temperature_c=r[3],
            avg_vibration_mm_s=r[4],
            max_vibration_mm_s=r[5],
            avg_pressure_bar=r[6],
            avg_cycle_time_s=r[7],
        )
        for r in session.execute(stmt)
    ]
    return TelemetrySummary(
        start=start, end=end, bucket_seconds=seconds, equipment_id=equipment_id, buckets=buckets
    )


# ---------------------------------------------------------------- dashboard


def _ingestion_totals(session: Session, start: datetime, end: datetime) -> dict[str, int]:
    rows = session.execute(
        select(IngestionStat.outcome, func.sum(IngestionStat.count))
        .where(IngestionStat.bucket_start >= start, IngestionStat.bucket_start < end)
        .group_by(IngestionStat.outcome)
    ).all()
    totals = {o.value: 0 for o in IngestOutcome}
    totals.update({k: int(v) for k, v in rows})
    return totals


def dashboard_summary(session: Session, start: datetime, end: datetime) -> DashboardSummary:
    status_rows = session.execute(
        select(Equipment.status, func.count())
        .where(Equipment.is_active.is_(True))
        .group_by(Equipment.status)
    ).all()
    dist = {s.value: 0 for s in EquipmentStatus}
    dist.update(dict(status_rows))
    units = (
        session.scalar(
            select(func.count()).where(
                TelemetryEvent.event_time >= start, TelemetryEvent.event_time < end, _running
            )
        )
        or 0
    )
    defects = (
        session.scalar(
            select(func.count()).where(Defect.detected_at >= start, Defect.detected_at < end)
        )
        or 0
    )
    totals = _ingestion_totals(session, start, end)
    open_alerts = (
        session.scalar(select(func.count()).where(Alert.status != AlertStatus.RESOLVED.value)) or 0
    )
    return DashboardSummary(
        generated_at=datetime.now(UTC),
        window_start=start,
        window_end=end,
        last_event_at=session.scalar(select(func.max(TelemetryEvent.event_time))),
        total_equipment=sum(dist.values()),
        running_equipment=dist[EquipmentStatus.RUNNING],
        warning_or_critical_equipment=dist[EquipmentStatus.WARNING]
        + dist[EquipmentStatus.CRITICAL],
        units_produced=units,
        defects=defects,
        defect_rate_pct=defect_rate_pct(units, defects),
        events_received=sum(totals.values()),
        events_rejected=totals[IngestOutcome.REJECTED],
        open_alerts=open_alerts,
        status_distribution=[StatusCount(status=k, count=v) for k, v in dist.items()],
        calculation_notes={
            "units_produced": UNITS_NOTE,
            "defect_rate_pct": DEFECT_RATE_NOTE,
            "events_received": "Sum of per-minute ingestion counters (accepted, late, rejected, "
            "duplicate) in the window.",
            "equipment": "Active equipment only; status is derived from the latest in-order event.",
        },
    )


# ---------------------------------------------------------------- analytics


def defect_analytics(session: Session, start: datetime, end: datetime) -> DefectAnalytics:
    in_window = (Defect.detected_at >= start, Defect.detected_at < end)
    by_cat = session.execute(
        select(Defect.category, func.count()).where(*in_window).group_by(Defect.category)
    ).all()
    by_sev = session.execute(
        select(Defect.severity, func.count()).where(*in_window).group_by(Defect.severity)
    ).all()
    defects_by_station = dict(
        session.execute(
            select(Equipment.station, func.count())
            .join(Defect, Defect.equipment_id == Equipment.id)
            .where(*in_window)
            .group_by(Equipment.station)
        ).all()
    )
    units_by_station = dict(
        session.execute(
            select(Equipment.station, func.count())
            .join(TelemetryEvent, TelemetryEvent.equipment_id == Equipment.id)
            .where(TelemetryEvent.event_time >= start, TelemetryEvent.event_time < end, _running)
            .group_by(Equipment.station)
        ).all()
    )
    stations = sorted(set(defects_by_station) | set(units_by_station))
    seconds = bucket_seconds_for(start, end)
    b = bucket_expr(Defect.detected_at, seconds, start).label("bucket")
    over_time = session.execute(
        select(b, func.count()).where(*in_window).group_by(b).order_by(b)
    ).all()
    return DefectAnalytics(
        window_start=start,
        window_end=end,
        by_category=[GroupCount(key=k, count=v) for k, v in sorted(by_cat, key=lambda r: -r[1])],
        by_severity=[GroupCount(key=k, count=v) for k, v in by_sev],
        by_station=[
            StationDefectRate(
                station=s,
                units_produced=units_by_station.get(s, 0),
                defects=defects_by_station.get(s, 0),
                defect_rate_pct=defect_rate_pct(
                    units_by_station.get(s, 0), defects_by_station.get(s, 0)
                )
                if units_by_station.get(s, 0) >= defects_by_station.get(s, 0)
                else None,
            )
            for s in stations
        ],
        over_time=[TimeCount(bucket=r[0], count=r[1]) for r in over_time],
        bucket_seconds=seconds,
        calculation_notes={"units": UNITS_NOTE, "defect_rate_pct": DEFECT_RATE_NOTE},
    )


def equipment_analytics(session: Session, start: datetime, end: datetime) -> EquipmentAnalytics:
    tel = (
        select(
            TelemetryEvent.equipment_id.label("eid"),
            func.count().label("events"),
            func.count().filter(_running).label("units"),
            func.avg(TelemetryEvent.temperature_c).label("avg_t"),
            func.avg(TelemetryEvent.vibration_mm_s).label("avg_v"),
        )
        .where(TelemetryEvent.event_time >= start, TelemetryEvent.event_time < end)
        .group_by(TelemetryEvent.equipment_id)
        .subquery()
    )
    dfx = (
        select(Defect.equipment_id.label("eid"), func.count().label("defects"))
        .where(Defect.detected_at >= start, Defect.detected_at < end)
        .group_by(Defect.equipment_id)
        .subquery()
    )
    alr = (
        select(Alert.equipment_id.label("eid"), func.count().label("alerts"))
        .where(Alert.triggered_at >= start, Alert.triggered_at < end)
        .group_by(Alert.equipment_id)
        .subquery()
    )
    rows = session.execute(
        select(
            Equipment,
            func.coalesce(tel.c.events, 0),
            func.coalesce(tel.c.units, 0),
            tel.c.avg_t,
            tel.c.avg_v,
            func.coalesce(dfx.c.defects, 0),
            func.coalesce(alr.c.alerts, 0),
        )
        .outerjoin(tel, tel.c.eid == Equipment.id)
        .outerjoin(dfx, dfx.c.eid == Equipment.id)
        .outerjoin(alr, alr.c.eid == Equipment.id)
        .where(Equipment.is_active.is_(True))
        .order_by(Equipment.code)
    ).all()
    out = []
    for eq, events, units, avg_t, avg_v, defects, alerts in rows:
        consistent = units >= defects
        out.append(
            EquipmentAnalyticsRow(
                equipment_id=eq.id,
                code=eq.code,
                name=eq.name,
                station=eq.station,
                status=eq.status,
                units_produced=units,
                defects=defects,
                defect_rate_pct=defect_rate_pct(units, defects) if consistent else None,
                yield_pct=yield_pct(units, defects) if consistent else None,
                alert_count=alerts,
                event_count=events,
                avg_temperature_c=avg_t,
                avg_vibration_mm_s=avg_v,
            )
        )
    return EquipmentAnalytics(window_start=start, window_end=end, rows=out)


def _metric_col(metric: Metric):
    return getattr(TelemetryEvent, METRIC_COLUMNS[metric])


def control_chart(
    session: Session, equipment_id: uuid.UUID, metric: Metric, start: datetime, end: datetime
) -> ControlChart:
    if session.get(Equipment, equipment_id) is None:
        raise NotFound(f"equipment {equipment_id} not found")
    col = _metric_col(metric)
    # Latest 500 running-state points in the window, returned in time order.
    latest = (
        select(TelemetryEvent.event_time, col.label("v"))
        .where(
            TelemetryEvent.equipment_id == equipment_id,
            TelemetryEvent.event_time >= start,
            TelemetryEvent.event_time < end,
            _running,
        )
        .order_by(TelemetryEvent.event_time.desc())
        .limit(500)
        .subquery()
    )
    rows = session.execute(
        select(latest.c.event_time, latest.c.v).order_by(latest.c.event_time)
    ).all()
    values = [r[1] for r in rows]
    stored = session.scalar(
        select(SpcBaseline).where(
            SpcBaseline.equipment_id == equipment_id, SpcBaseline.metric == metric.value
        )
    )
    method = "Shewhart individuals chart (I-MR): CL = mean, σ = MR̄ / 1.128, limits = CL ± 3σ"
    baseline: IndividualsBaseline | None
    if stored:
        baseline = IndividualsBaseline(stored.mean, stored.sigma, stored.sample_size)
        source = "stored"
        explanation = (
            f"Baseline estimated from {stored.sample_size} running-state readings between "
            f"{stored.window_start:%Y-%m-%d %H:%M} and {stored.window_end:%Y-%m-%d %H:%M} UTC, "
            "assumed to represent stable operation."
        )
    else:
        baseline = estimate_individuals_baseline(values[:25]) if len(values) >= 20 else None
        source = "window" if baseline else "insufficient_data"
        explanation = (
            "No stored baseline; limits estimated from the first 25 points in this window "
            "(phase-I estimate, assumes those points are in control)."
            if baseline
            else "Fewer than 20 running-state readings; control limits cannot be estimated."
        )
    flags = classify_points(values, baseline) if baseline else [None] * len(values)
    return ControlChart(
        equipment_id=equipment_id,
        metric=metric,
        unit=METRIC_UNITS[metric],
        method=method,
        baseline_source=source,
        center_line=baseline.mean if baseline else None,
        sigma=baseline.sigma if baseline else None,
        ucl=baseline.ucl if baseline else None,
        lcl=baseline.lcl if baseline else None,
        baseline_sample_size=baseline.sample_size if baseline else 0,
        points=[
            ControlChartPoint(event_time=t, value=v, out_of_control=f is not None, rule=f)
            for (t, v), f in zip(rows, flags, strict=True)
        ],
        explanation=explanation + " Rules: WE-1 (beyond 3σ), WE-2 (8 consecutive on one side).",
    )


def compute_baselines(
    session: Session, start: datetime, end: datetime, max_points: int = 300
) -> int:
    """(Re)compute SPC baselines for every active equipment & metric from running-state data."""
    count = 0
    equipment_ids = session.scalars(select(Equipment.id).where(Equipment.is_active.is_(True))).all()
    for eid in equipment_ids:
        for metric in Metric:
            col = _metric_col(metric)
            values = session.scalars(
                select(col)
                .where(
                    TelemetryEvent.equipment_id == eid,
                    TelemetryEvent.event_time >= start,
                    TelemetryEvent.event_time < end,
                    _running,
                )
                .order_by(TelemetryEvent.event_time)
                .limit(max_points)
            ).all()
            bl = estimate_individuals_baseline(list(values))
            if bl is None:
                continue
            stmt = pg_insert(SpcBaseline).values(
                id=uuid.uuid4(),
                equipment_id=eid,
                metric=metric.value,
                mean=bl.mean,
                sigma=bl.sigma,
                sample_size=bl.sample_size,
                window_start=start,
                window_end=end,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_spc_equipment_metric",
                set_={
                    "mean": bl.mean,
                    "sigma": bl.sigma,
                    "sample_size": bl.sample_size,
                    "window_start": start,
                    "window_end": end,
                    "computed_at": func.now(),
                },
            )
            session.execute(stmt)
            count += 1
    session.commit()
    return count


# ---------------------------------------------------------------- data quality


def data_quality_summary(session: Session, start: datetime, end: datetime) -> DataQualitySummary:
    s = get_settings()
    totals = _ingestion_totals(session, start, end)
    by_cat = dict(
        session.execute(
            select(DataQualityFailure.category, func.count())
            .where(DataQualityFailure.received_at >= start, DataQualityFailure.received_at < end)
            .group_by(DataQualityFailure.category)
        ).all()
    )
    seconds = bucket_seconds_for(start, end)
    b = bucket_expr(IngestionStat.bucket_start, seconds, start).label("bucket")
    rows = session.execute(
        select(
            b,
            *[
                func.sum(case((IngestionStat.outcome == o.value, IngestionStat.count), else_=0))
                for o in IngestOutcome
            ],
        )
        .where(IngestionStat.bucket_start >= start, IngestionStat.bucket_start < end)
        .group_by(b)
        .order_by(b)
    ).all()
    over_time = [
        {"bucket": r[0], **{o.value: int(r[i + 1]) for i, o in enumerate(IngestOutcome)}}
        for r in rows
    ]
    return DataQualitySummary(
        window_start=start,
        window_end=end,
        total_received=sum(totals.values()),
        valid=totals[IngestOutcome.ACCEPTED] + totals[IngestOutcome.ACCEPTED_LATE],
        valid_late=totals[IngestOutcome.ACCEPTED_LATE],
        invalid=totals[IngestOutcome.REJECTED],
        duplicates=totals[IngestOutcome.DUPLICATE],
        by_category=[GroupCount(key=c.value, count=by_cat.get(c.value, 0)) for c in DQCategory],
        over_time=over_time,
        bucket_seconds=seconds,
        policy={
            "idempotency": "event_id is the telemetry primary key; inserts use ON CONFLICT DO "
            "NOTHING, so re-delivered events are counted as duplicates and never stored twice.",
            "late_events": f"Events received more than {s.late_event_seconds}s after event_time, "
            "or older than the equipment's latest event (out-of-order), are stored and flagged "
            "late. They are evaluated for alerts but do not change current equipment status.",
            "rejected_timestamps": f"Events more than {s.max_clock_skew_seconds}s in the future "
            f"or older than {s.max_event_age_seconds // 3600}h are rejected.",
            "retries": f"Transient database errors are retried up to {s.processing_max_retries} "
            "times with exponential backoff; invalid messages are recorded once and never retried.",
            "payload_excerpts": "Only known schema fields are stored (strings truncated to 64 "
            "chars); unknown keys are listed by name only.",
        },
    )


def list_failures(
    session: Session,
    *,
    category: str | None,
    equipment_code: str | None,
    start: datetime,
    end: datetime,
    page: int,
    page_size: int,
):
    from factoryops.services.common import paginate

    stmt = select(DataQualityFailure).where(
        DataQualityFailure.received_at >= start, DataQualityFailure.received_at < end
    )
    if category:
        stmt = stmt.where(DataQualityFailure.category == category)
    if equipment_code:
        stmt = stmt.where(DataQualityFailure.equipment_code.ilike(f"%{equipment_code.strip()}%"))
    stmt = stmt.order_by(DataQualityFailure.received_at.desc(), DataQualityFailure.id)
    return paginate(session, stmt, page, page_size)
