import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from factoryops import notify
from factoryops.domain import AlertStatus
from factoryops.errors import InvalidTransition, NotFound
from factoryops.models import Alert, AlertStatusHistory, TelemetryEvent
from factoryops.schemas import AlertAction, AlertDetail, AlertHistoryOut, AlertOut, TelemetryPoint
from factoryops.services.common import paginate, parse_sort

SORTS = {
    "triggered_at": Alert.triggered_at,
    "last_seen_at": Alert.last_seen_at,
    "severity": Alert.severity,
    "occurrence_count": Alert.occurrence_count,
}

TRANSITIONS: dict[AlertStatus, set[AlertStatus]] = {
    AlertStatus.OPEN: {AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED},
    AlertStatus.ACKNOWLEDGED: {AlertStatus.RESOLVED},
    AlertStatus.RESOLVED: set(),
}


def list_alerts(
    session: Session,
    *,
    status: str | None,
    severity: str | None,
    equipment_id: uuid.UUID | None,
    metric: str | None,
    rule: str | None,
    start: datetime | None,
    end: datetime | None,
    sort: str | None,
    page: int,
    page_size: int,
):
    stmt = select(Alert)
    if status == "active":
        stmt = stmt.where(Alert.status != AlertStatus.RESOLVED)
    elif status:
        stmt = stmt.where(Alert.status == status)
    for col, value in (
        (Alert.severity, severity),
        (Alert.equipment_id, equipment_id),
        (Alert.metric, metric),
        (Alert.rule, rule),
    ):
        if value:
            stmt = stmt.where(col == value)
    if start:
        stmt = stmt.where(Alert.triggered_at >= start)
    if end:
        stmt = stmt.where(Alert.triggered_at < end)
    stmt = stmt.order_by(parse_sort(sort, SORTS, Alert.triggered_at.desc()), Alert.id)
    return paginate(session, stmt, page, page_size)


def get_alert(session: Session, alert_id: uuid.UUID) -> AlertDetail:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise NotFound(f"alert {alert_id} not found")
    event = session.get(TelemetryEvent, alert.event_id) if alert.event_id else None
    return AlertDetail(
        **AlertOut.model_validate(alert).model_dump(),
        history=[AlertHistoryOut.model_validate(h) for h in alert.history],
        event=TelemetryPoint.model_validate(event) if event else None,
    )


def transition(
    session: Session, alert_id: uuid.UUID, to: AlertStatus, action: AlertAction
) -> AlertDetail:
    """Row-locked state change + audit record in a single transaction."""
    alert = session.scalar(select(Alert).where(Alert.id == alert_id).with_for_update())
    if alert is None:
        raise NotFound(f"alert {alert_id} not found")
    current = AlertStatus(alert.status)
    if to not in TRANSITIONS[current]:
        raise InvalidTransition(f"cannot change alert status from {current} to {to}")
    now = datetime.now(UTC)
    alert.status = to.value
    if to == AlertStatus.ACKNOWLEDGED:
        alert.acknowledged_at = now
    if to == AlertStatus.RESOLVED:
        alert.resolved_at = now
        alert.acknowledged_at = alert.acknowledged_at or now
    session.add(
        AlertStatusHistory(
            alert_id=alert.id,
            from_status=current.value,
            to_status=to.value,
            actor=action.actor,
            note=action.note,
            changed_at=now,
        )
    )
    notify.publish(
        session,
        "alert",
        {"action": to.value, "alert_id": str(alert.id), "severity": alert.severity},
    )
    session.commit()
    session.expire_all()
    return get_alert(session, alert_id)
