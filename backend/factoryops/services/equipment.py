import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from factoryops import notify
from factoryops.errors import Conflict, NotFound
from factoryops.models import Equipment, EquipmentThreshold
from factoryops.schemas import EquipmentCreate, EquipmentUpdate, Threshold
from factoryops.services.common import paginate, parse_sort

SORTS = {
    "code": Equipment.code,
    "name": Equipment.name,
    "station": Equipment.station,
    "status": Equipment.status,
    "updated_at": Equipment.updated_at,
    "last_event_at": Equipment.last_event_at,
}


def list_equipment(
    session: Session,
    *,
    q: str | None,
    status: str | None,
    station: str | None,
    include_inactive: bool,
    sort: str | None,
    page: int,
    page_size: int,
):
    stmt = select(Equipment)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Equipment.code.ilike(like),
                Equipment.name.ilike(like),
                Equipment.equipment_type.ilike(like),
            )
        )
    if status:
        stmt = stmt.where(Equipment.status == status)
    if station:
        stmt = stmt.where(Equipment.station == station)
    if not include_inactive:
        stmt = stmt.where(Equipment.is_active.is_(True))
    stmt = stmt.order_by(parse_sort(sort, SORTS, Equipment.code.asc()), Equipment.id)
    return paginate(session, stmt, page, page_size)


def list_stations(session: Session) -> list[str]:
    return list(session.scalars(select(Equipment.station).distinct().order_by(Equipment.station)))


def get_equipment(session: Session, equipment_id: uuid.UUID) -> Equipment:
    eq = session.get(Equipment, equipment_id)
    if eq is None:
        raise NotFound(f"equipment {equipment_id} not found")
    return eq


def _threshold_rows(thresholds: list[Threshold]) -> list[EquipmentThreshold]:
    return [
        EquipmentThreshold(
            metric=t.metric.value,
            warning_low=t.warning_low,
            warning_high=t.warning_high,
            critical_low=t.critical_low,
            critical_high=t.critical_high,
        )
        for t in thresholds
    ]


def create_equipment(session: Session, data: EquipmentCreate) -> Equipment:
    if session.scalar(select(Equipment.id).where(Equipment.code == data.code)):
        raise Conflict(f"equipment code '{data.code}' already exists")
    eq = Equipment(
        code=data.code,
        name=data.name,
        station=data.station,
        equipment_type=data.equipment_type,
        status=data.status.value,
        is_active=True,
        thresholds=_threshold_rows(data.thresholds),
    )
    session.add(eq)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict(f"equipment code '{data.code}' already exists") from exc
    notify.publish(session, "equipment", {"action": "created", "equipment_id": str(eq.id)})
    session.commit()
    session.refresh(eq)
    return eq


def update_equipment(session: Session, equipment_id: uuid.UUID, data: EquipmentUpdate) -> Equipment:
    eq = session.scalar(select(Equipment).where(Equipment.id == equipment_id).with_for_update())
    if eq is None:
        raise NotFound(f"equipment {equipment_id} not found")
    fields = data.model_dump(exclude_unset=True, exclude={"thresholds"})
    for key, value in fields.items():
        if value is not None:
            setattr(eq, key, value.value if hasattr(value, "value") else value)
    if data.thresholds is not None:
        eq.thresholds.clear()
        session.flush()  # delete old rows before inserting replacements (unique metric)
        eq.thresholds.extend(_threshold_rows(data.thresholds))
    notify.publish(session, "equipment", {"action": "updated", "equipment_id": str(eq.id)})
    session.commit()
    session.refresh(eq)
    return eq


def deactivate_equipment(session: Session, equipment_id: uuid.UUID) -> Equipment:
    """Soft delete: history (telemetry, runs, alerts, defects) must remain referentially intact."""
    eq = session.scalar(select(Equipment).where(Equipment.id == equipment_id).with_for_update())
    if eq is None:
        raise NotFound(f"equipment {equipment_id} not found")
    eq.is_active = False
    eq.status = "offline"
    notify.publish(session, "equipment", {"action": "deactivated", "equipment_id": str(eq.id)})
    session.commit()
    session.refresh(eq)
    return eq
