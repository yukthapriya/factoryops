import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from factoryops import notify
from factoryops.domain import DefectStatus
from factoryops.errors import InvalidTransition, NotFound
from factoryops.models import Alert, Defect, DefectNote, Equipment, ProductionRun, TelemetryEvent
from factoryops.schemas import (
    AlertBrief,
    DefectDetail,
    DefectEvidence,
    DefectNoteCreate,
    DefectNoteOut,
    DefectOut,
    DefectUpdate,
    TelemetryPoint,
    Threshold,
)
from factoryops.services.common import paginate, parse_sort

EVIDENCE_WINDOW = timedelta(minutes=15)

ALLOWED_TRANSITIONS: dict[DefectStatus, set[DefectStatus]] = {
    DefectStatus.OPEN: {DefectStatus.INVESTIGATING, DefectStatus.RESOLVED, DefectStatus.DISMISSED},
    DefectStatus.INVESTIGATING: {DefectStatus.OPEN, DefectStatus.RESOLVED, DefectStatus.DISMISSED},
    DefectStatus.RESOLVED: {DefectStatus.INVESTIGATING},  # reopen
    DefectStatus.DISMISSED: {DefectStatus.INVESTIGATING},  # reopen
}

SORTS = {
    "detected_at": Defect.detected_at,
    "severity": Defect.severity,
    "status": Defect.status,
    "category": Defect.category,
}


def list_defects(
    session: Session,
    *,
    q: str | None,
    status: str | None,
    category: str | None,
    severity: str | None,
    station: str | None,
    equipment_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    start: datetime | None,
    end: datetime | None,
    sort: str | None,
    page: int,
    page_size: int,
):
    stmt = select(Defect).join(Defect.equipment).join(Defect.production_run)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Defect.description.ilike(like),
                Equipment.code.ilike(like),
                ProductionRun.run_code.ilike(like),
                ProductionRun.batch_id.ilike(like),
            )
        )
    for col, value in (
        (Defect.status, status),
        (Defect.category, category),
        (Defect.severity, severity),
        (Equipment.station, station),
        (Defect.equipment_id, equipment_id),
        (Defect.production_run_id, run_id),
    ):
        if value:
            stmt = stmt.where(col == value)
    if start:
        stmt = stmt.where(Defect.detected_at >= start)
    if end:
        stmt = stmt.where(Defect.detected_at < end)
    stmt = stmt.order_by(parse_sort(sort, SORTS, Defect.detected_at.desc()), Defect.id)
    return paginate(session, stmt, page, page_size)


def get_defect(session: Session, defect_id: uuid.UUID) -> DefectDetail:
    defect = session.get(Defect, defect_id)
    if defect is None:
        raise NotFound(f"defect {defect_id} not found")
    w_start = defect.detected_at - EVIDENCE_WINDOW
    w_end = defect.detected_at + EVIDENCE_WINDOW
    telemetry = session.scalars(
        select(TelemetryEvent)
        .where(
            TelemetryEvent.equipment_id == defect.equipment_id,
            TelemetryEvent.event_time >= w_start,
            TelemetryEvent.event_time <= w_end,
        )
        .order_by(TelemetryEvent.event_time)
        .limit(500)
    ).all()
    source = session.get(TelemetryEvent, defect.source_event_id) if defect.source_event_id else None
    alerts = session.scalars(
        select(Alert)
        .where(
            Alert.equipment_id == defect.equipment_id,
            Alert.triggered_at <= w_end,
            Alert.last_seen_at >= w_start,
        )
        .order_by(Alert.triggered_at)
    ).all()
    base = DefectOut.model_validate(defect)
    return DefectDetail(
        **base.model_dump(),
        notes=[DefectNoteOut.model_validate(n) for n in defect.notes],
        evidence=DefectEvidence(
            window_start=w_start,
            window_end=w_end,
            source_event=TelemetryPoint.model_validate(source) if source else None,
            telemetry=[TelemetryPoint.model_validate(t) for t in telemetry],
            alerts=[AlertBrief.model_validate(a) for a in alerts],
            thresholds=[Threshold.model_validate(t) for t in defect.equipment.thresholds],
        ),
    )


def update_defect(session: Session, defect_id: uuid.UUID, data: DefectUpdate) -> DefectDetail:
    defect = session.scalar(select(Defect).where(Defect.id == defect_id).with_for_update())
    if defect is None:
        raise NotFound(f"defect {defect_id} not found")
    current = DefectStatus(defect.status)
    changes: list[str] = []
    status_from = status_to = None
    if data.status is not None and data.status != current:
        if data.status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransition(f"cannot change defect status from {current} to {data.status}")
        status_from, status_to = current.value, data.status.value
        defect.status = data.status.value
        changes.append(f"status {current.value} → {data.status.value}")
    if data.severity is not None and data.severity != defect.severity:
        changes.append(f"severity {defect.severity} → {data.severity.value}")
        defect.severity = data.severity.value
    if changes or data.note:
        body = "; ".join(changes)
        if data.note:
            body = f"{body}\n{data.note}" if body else data.note
        session.add(
            DefectNote(
                defect_id=defect.id,
                author=data.author,
                body=body,
                status_from=status_from,
                status_to=status_to,
            )
        )
    notify.publish(session, "defect", {"action": "updated", "defect_id": str(defect.id)})
    session.commit()
    session.expire_all()
    return get_defect(session, defect_id)


def add_note(session: Session, defect_id: uuid.UUID, data: DefectNoteCreate) -> DefectNoteOut:
    if session.get(Defect, defect_id) is None:
        raise NotFound(f"defect {defect_id} not found")
    note = DefectNote(defect_id=defect_id, author=data.author, body=data.body)
    session.add(note)
    notify.publish(session, "defect", {"action": "note_added", "defect_id": str(defect_id)})
    session.commit()
    session.refresh(note)
    return DefectNoteOut.model_validate(note)
