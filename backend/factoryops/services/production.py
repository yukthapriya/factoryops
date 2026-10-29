import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from factoryops.analytics import defect_rate_pct, yield_pct
from factoryops.errors import NotFound
from factoryops.models import Defect, Equipment, ProductionRun, TelemetryEvent
from factoryops.schemas import DefectBrief, EquipmentRef, RunDetail, RunOut, RunTelemetryStats
from factoryops.services.common import paginate, parse_sort

SORTS = {
    "started_at": ProductionRun.started_at,
    "units_produced": ProductionRun.units_produced,
    "defect_count": ProductionRun.defect_count,
    "run_code": ProductionRun.run_code,
}


def run_out(run: ProductionRun) -> RunOut:
    return RunOut(
        id=run.id,
        run_code=run.run_code,
        batch_id=run.batch_id,
        equipment=EquipmentRef.model_validate(run.equipment),
        started_at=run.started_at,
        ended_at=run.ended_at,
        units_produced=run.units_produced,
        defect_count=run.defect_count,
        status=run.status,
        yield_pct=yield_pct(run.units_produced, run.defect_count),
        defect_rate_pct=defect_rate_pct(run.units_produced, run.defect_count),
    )


def list_runs(
    session: Session,
    *,
    q: str | None,
    status: str | None,
    equipment_id: uuid.UUID | None,
    station: str | None,
    start: datetime | None,
    end: datetime | None,
    sort: str | None,
    page: int,
    page_size: int,
):
    stmt = select(ProductionRun).join(ProductionRun.equipment)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                ProductionRun.run_code.ilike(like),
                ProductionRun.batch_id.ilike(like),
                Equipment.code.ilike(like),
            )
        )
    if status:
        stmt = stmt.where(ProductionRun.status == status)
    if equipment_id:
        stmt = stmt.where(ProductionRun.equipment_id == equipment_id)
    if station:
        stmt = stmt.where(Equipment.station == station)
    # A run overlaps [start, end) if it started before end and had not ended before start.
    if end:
        stmt = stmt.where(ProductionRun.started_at < end)
    if start:
        stmt = stmt.where(or_(ProductionRun.ended_at.is_(None), ProductionRun.ended_at >= start))
    stmt = stmt.order_by(parse_sort(sort, SORTS, ProductionRun.started_at.desc()), ProductionRun.id)
    runs, total = paginate(session, stmt, page, page_size)
    return [run_out(r) for r in runs], total


def get_run(session: Session, run_id: uuid.UUID) -> RunDetail:
    run = session.get(ProductionRun, run_id)
    if run is None:
        raise NotFound(f"production run {run_id} not found")
    stats = session.execute(
        select(
            func.count(),
            func.avg(TelemetryEvent.temperature_c),
            func.max(TelemetryEvent.temperature_c),
            func.avg(TelemetryEvent.vibration_mm_s),
            func.max(TelemetryEvent.vibration_mm_s),
            func.avg(TelemetryEvent.pressure_bar),
            func.avg(TelemetryEvent.cycle_time_s),
        ).where(TelemetryEvent.production_run_id == run.id)
    ).one()
    defects = session.scalars(
        select(Defect).where(Defect.production_run_id == run.id).order_by(Defect.detected_at.desc())
    ).all()
    base = run_out(run)
    return RunDetail(
        **base.model_dump(),
        telemetry=RunTelemetryStats(
            event_count=stats[0],
            avg_temperature_c=stats[1],
            max_temperature_c=stats[2],
            avg_vibration_mm_s=stats[3],
            max_vibration_mm_s=stats[4],
            avg_pressure_bar=stats[5],
            avg_cycle_time_s=stats[6],
        ),
        defects=[DefectBrief.model_validate(d) for d in defects],
    )
