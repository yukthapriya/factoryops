"""Transactional processing of a single telemetry message.

Each message is handled in exactly one database transaction:

1. Validate (pure). Rejections are persisted to data_quality_failures - never silently dropped.
2. Lock the equipment row (serializes events per equipment across consumers).
3. INSERT ... ON CONFLICT (event_id) DO NOTHING. A conflict means the event was already
   processed: the transaction is rolled back and the duplicate is recorded instead.
4. Attach to / roll over the production run, count units and defects.
5. Update equipment status (not for late/out-of-order events).
6. Evaluate alert rules and apply the dedup/cooldown policy.
7. Increment per-minute ingestion counters and emit NOTIFY messages (delivered on commit).
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from factoryops import notify
from factoryops.config import Settings, get_settings
from factoryops.domain import (
    AlertSeverity,
    AlertStatus,
    DefectStatus,
    DQCategory,
    EquipmentStatus,
    IngestOutcome,
    MachineState,
    Metric,
    RunStatus,
)
from factoryops.models import (
    Alert,
    AlertStatusHistory,
    DataQualityFailure,
    Defect,
    Equipment,
    IngestionStat,
    ProductionRun,
    SpcBaseline,
    TelemetryEvent,
)
from factoryops.pipeline.rules import AlertCandidate, Baseline, ThresholdSpec, evaluate_event
from factoryops.pipeline.validation import (
    Rejection,
    ValidationPolicy,
    ValidEvent,
    validate_event,
)

log = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    outcome: IngestOutcome
    event_id: str | None = None
    category: DQCategory | None = None
    reason: str | None = None
    alerts_opened: list[uuid.UUID] = field(default_factory=list)
    alerts_updated: list[uuid.UUID] = field(default_factory=list)
    alerts_suppressed: int = 0
    defect_id: uuid.UUID | None = None


class TransientProcessingError(Exception):
    """Infrastructure failure (e.g. database unavailable); safe to retry the same message."""


def policy_from(settings: Settings) -> ValidationPolicy:
    return ValidationPolicy(
        late_after=timedelta(seconds=settings.late_event_seconds),
        max_age=timedelta(seconds=settings.max_event_age_seconds),
        max_future_skew=timedelta(seconds=settings.max_clock_skew_seconds),
    )


def _bump_stat(session: Session, outcome: IngestOutcome, now: datetime) -> None:
    bucket = now.replace(second=0, microsecond=0)
    stmt = pg_insert(IngestionStat).values(bucket_start=bucket, outcome=outcome.value, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["bucket_start", "outcome"],
        set_={"count": IngestionStat.count + 1},
    )
    session.execute(stmt)


def _record_failure(
    session: Session, rej: Rejection, now: datetime, source: str | None, outcome: IngestOutcome
) -> None:
    session.add(
        DataQualityFailure(
            event_id=rej.event_id,
            equipment_code=rej.equipment_code,
            received_at=now,
            category=rej.category.value,
            field=rej.field,
            reason=rej.reason,
            payload_excerpt=rej.payload_excerpt,
            source=source,
        )
    )
    _bump_stat(session, outcome, now)
    notify.publish(
        session,
        "data_quality",
        {
            "category": rej.category.value,
            "reason": rej.reason,
            "event_id": rej.event_id,
            "equipment_code": rej.equipment_code,
        },
    )


def _reject(session: Session, rej: Rejection, now: datetime, source: str | None) -> ProcessResult:
    outcome = (
        IngestOutcome.DUPLICATE if rej.category == DQCategory.DUPLICATE else IngestOutcome.REJECTED
    )
    _record_failure(session, rej, now, source, outcome)
    session.commit()
    return ProcessResult(outcome, rej.event_id, rej.category, rej.reason)


def _resolve_run(session: Session, eq: Equipment, ev: ValidEvent) -> ProductionRun:
    run_code = f"{eq.code}-{ev.batch_id}"[:48]
    run = session.scalar(
        select(ProductionRun).where(ProductionRun.run_code == run_code).with_for_update()
    )
    if run is not None:
        if ev.event_time < run.started_at:
            run.started_at = ev.event_time  # out-of-order event earlier than the first seen
        return run
    # New batch on this equipment: close the currently running run (if any).
    current = session.scalar(
        select(ProductionRun)
        .where(ProductionRun.equipment_id == eq.id, ProductionRun.status == RunStatus.RUNNING)
        .with_for_update()
    )
    if current is not None:
        current.status = RunStatus.COMPLETED.value
        current.ended_at = max(current.started_at, ev.event_time)
        session.flush()
    run = ProductionRun(
        run_code=run_code,
        batch_id=ev.batch_id,
        equipment_id=eq.id,
        started_at=ev.event_time,
        status=RunStatus.RUNNING.value,
        units_produced=0,
        defect_count=0,
    )
    session.add(run)
    session.flush()
    return run


def _derive_status(state: MachineState, candidates: list[AlertCandidate]) -> EquipmentStatus:
    if state == MachineState.FAULT or any(c.severity == AlertSeverity.CRITICAL for c in candidates):
        return EquipmentStatus.CRITICAL
    if state == MachineState.STOPPED:
        return EquipmentStatus.OFFLINE
    if candidates:
        return EquipmentStatus.WARNING
    return EquipmentStatus.RUNNING if state == MachineState.RUNNING else EquipmentStatus.IDLE


def _apply_alert(
    session: Session,
    eq: Equipment,
    ev: ValidEvent,
    cand: AlertCandidate,
    now: datetime,
    cooldown: timedelta,
    result: ProcessResult,
) -> None:
    """Dedup/cooldown policy (documented in README):

    * At most one unresolved alert per (equipment, metric, rule) - enforced by a partial unique
      index. Repeat breaches increment occurrence_count/last_seen_at and may escalate severity.
    * After an alert is resolved, the same key is suppressed for `alert_cooldown_seconds`.
    """
    active = session.scalar(
        select(Alert)
        .where(
            Alert.equipment_id == eq.id,
            Alert.metric == cand.metric,
            Alert.rule == cand.rule.value,
            Alert.status != AlertStatus.RESOLVED,
        )
        .with_for_update()
    )
    if active is not None:
        # observed_value / threshold / rule_detail / event_id always describe the event that set
        # the alert's current severity; repeats only bump the counter and last_seen_at.
        active.occurrence_count += 1
        active.last_seen_at = now
        if cand.severity == AlertSeverity.CRITICAL and active.severity != AlertSeverity.CRITICAL:
            active.severity = AlertSeverity.CRITICAL.value
            active.observed_value = cand.observed_value
            active.threshold_value = cand.threshold_value
            active.rule_detail = cand.detail
            active.event_id = ev.event_id
        result.alerts_updated.append(active.id)
        return

    recently_resolved = session.scalar(
        select(func.count())
        .select_from(Alert)
        .where(
            Alert.equipment_id == eq.id,
            Alert.metric == cand.metric,
            Alert.rule == cand.rule.value,
            Alert.resolved_at > now - cooldown,
        )
    )
    if recently_resolved:
        result.alerts_suppressed += 1
        log.info(
            "alert suppressed by cooldown",
            extra={"equipment": eq.code, "metric": cand.metric, "rule": cand.rule.value},
        )
        return

    alert = Alert(
        equipment_id=eq.id,
        event_id=ev.event_id,
        metric=cand.metric,
        rule=cand.rule.value,
        rule_detail=cand.detail,
        observed_value=cand.observed_value,
        threshold_value=cand.threshold_value,
        severity=cand.severity.value,
        status=AlertStatus.OPEN.value,
        occurrence_count=1,
        triggered_at=now,
        last_seen_at=now,
    )
    session.add(alert)
    session.flush()
    session.add(
        AlertStatusHistory(
            alert_id=alert.id,
            from_status=None,
            to_status=AlertStatus.OPEN.value,
            actor="system",
            note=cand.detail,
            changed_at=now,
        )
    )
    result.alerts_opened.append(alert.id)
    notify.publish(
        session,
        "alert",
        {
            "action": "opened",
            "alert_id": str(alert.id),
            "equipment_code": eq.code,
            "metric": cand.metric,
            "severity": cand.severity.value,
            "detail": cand.detail,
        },
    )


def process_message(
    session: Session,
    raw: bytes | str | dict,
    *,
    now: datetime | None = None,
    source: str | None = None,
    settings: Settings | None = None,
) -> ProcessResult:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)

    validated = validate_event(raw, now, policy_from(settings))
    if isinstance(validated, Rejection):
        return _reject(session, validated, now, source)
    ev = validated

    eq = session.scalar(
        select(Equipment).where(Equipment.code == ev.equipment_code).with_for_update()
    )
    if eq is None or not eq.is_active:
        return _reject(
            session,
            Rejection(
                DQCategory.UNKNOWN_EQUIPMENT,
                f"equipment '{ev.equipment_code}' is not registered or is deactivated",
                "equipment_id",
                str(ev.event_id),
                ev.equipment_code,
                {"equipment_id": ev.equipment_code, "event_time": ev.event_time.isoformat()},
            ),
            now,
            source,
        )

    out_of_order = eq.last_event_at is not None and ev.event_time < eq.last_event_at
    is_late = ev.is_late or out_of_order

    run = _resolve_run(session, eq, ev)
    inserted = session.scalar(
        pg_insert(TelemetryEvent)
        .values(
            event_id=ev.event_id,
            equipment_id=eq.id,
            production_run_id=run.id,
            event_time=ev.event_time,
            received_at=now,
            temperature_c=ev.temperature_c,
            vibration_mm_s=ev.vibration_mm_s,
            pressure_bar=ev.pressure_bar,
            cycle_time_s=ev.cycle_time_s,
            machine_state=ev.machine_state.value,
            batch_id=ev.batch_id,
            is_late=is_late,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(TelemetryEvent.event_id)
    )
    if inserted is None:
        # Already processed: undo any run bookkeeping and record the duplicate.
        session.rollback()
        return _reject(
            session,
            Rejection(
                DQCategory.DUPLICATE,
                "event_id was already processed; the duplicate was not stored again",
                "event_id",
                str(ev.event_id),
                ev.equipment_code,
                {"event_id": str(ev.event_id), "event_time": ev.event_time.isoformat()},
            ),
            now,
            source,
        )

    result = ProcessResult(
        IngestOutcome.ACCEPTED_LATE if is_late else IngestOutcome.ACCEPTED, str(ev.event_id)
    )

    # Production accounting: one completed cycle per running event (or an inspected part).
    if ev.machine_state == MachineState.RUNNING or ev.defect is not None:
        run.units_produced += 1
    if ev.defect is not None:
        run.defect_count += 1
        defect = Defect(
            production_run_id=run.id,
            equipment_id=eq.id,
            source_event_id=ev.event_id,
            category=ev.defect.category.value,
            severity=ev.defect.severity.value,
            status=DefectStatus.OPEN.value,
            description=ev.defect.description,
            detected_at=ev.event_time,
        )
        session.add(defect)
        session.flush()
        result.defect_id = defect.id
        notify.publish(
            session,
            "defect",
            {
                "action": "created",
                "defect_id": str(defect.id),
                "equipment_code": eq.code,
                "category": defect.category,
                "severity": defect.severity,
            },
        )

    thresholds = {
        Metric(t.metric): ThresholdSpec(
            Metric(t.metric), t.warning_low, t.warning_high, t.critical_low, t.critical_high
        )
        for t in eq.thresholds
    }
    baselines = {
        Metric(b.metric): Baseline(Metric(b.metric), b.mean, b.sigma, b.sample_size)
        for b in session.scalars(select(SpcBaseline).where(SpcBaseline.equipment_id == eq.id))
    }
    candidates = evaluate_event(ev, thresholds, baselines)
    cooldown = timedelta(seconds=settings.alert_cooldown_seconds)
    for cand in candidates:
        _apply_alert(session, eq, ev, cand, now, cooldown, result)

    if not is_late:
        eq.last_event_at = ev.event_time
        if eq.status != EquipmentStatus.MAINTENANCE:
            new_status = _derive_status(ev.machine_state, candidates)
            if new_status != eq.status:
                eq.status = new_status.value
                notify.publish(
                    session,
                    "equipment_status",
                    {"equipment_id": str(eq.id), "code": eq.code, "status": new_status.value},
                )

    _bump_stat(session, result.outcome, now)
    notify.publish(
        session,
        "telemetry",
        {
            "equipment_id": str(eq.id),
            "equipment_code": eq.code,
            "event_id": str(ev.event_id),
            "event_time": ev.event_time.isoformat(),
            "machine_state": ev.machine_state.value,
            "temperature_c": ev.temperature_c,
            "vibration_mm_s": ev.vibration_mm_s,
            "pressure_bar": ev.pressure_bar,
            "cycle_time_s": ev.cycle_time_s,
            "is_late": is_late,
        },
    )
    session.commit()
    return result


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, OperationalError):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


def process_with_retry(
    session_factory: Callable[[], Session],
    raw: bytes | str | dict,
    *,
    source: str | None = None,
    max_retries: int | None = None,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProcessResult:
    """Retry *transient* infrastructure errors with bounded exponential backoff.

    Invalid messages never reach this path as errors - they are recorded as data-quality
    failures on the first attempt. Non-transient exceptions are raised immediately.
    """
    attempts = max_retries if max_retries is not None else get_settings().processing_max_retries
    for attempt in range(attempts + 1):
        session = session_factory()
        try:
            return process_message(session, raw, source=source, now=now_fn())
        except Exception as exc:
            session.rollback()
            if not _is_transient(exc):
                raise
            if attempt >= attempts:
                raise TransientProcessingError(
                    f"giving up after {attempts + 1} attempts: {exc}"
                ) from exc
            delay = min(base_delay * 2**attempt, 30.0)
            log.warning(
                "transient processing error; retrying",
                extra={"attempt": attempt + 1, "delay_s": delay, "error": str(exc)[:200]},
            )
            sleep(delay)
        finally:
            session.close()
    raise AssertionError("unreachable")


def record_processing_error(
    session: Session, raw: bytes | str | dict, error: str, source: str | None
) -> None:
    """Best-effort record of a message that failed for a non-data reason (sent to DLQ)."""
    from factoryops.pipeline.validation import decode, safe_excerpt

    payload = decode(raw)
    excerpt = payload.payload_excerpt if isinstance(payload, Rejection) else safe_excerpt(payload)
    event_id = excerpt.get("event_id") if isinstance(excerpt, dict) else None
    now = datetime.now(UTC)
    _record_failure(
        session,
        Rejection(
            DQCategory.PROCESSING_ERROR,
            f"Processing failed and the message was sent to the dead-letter topic: {error[:300]}",
            None,
            str(event_id) if event_id else None,
            excerpt.get("equipment_id") if isinstance(excerpt, dict) else None,
            excerpt,
        ),
        now,
        source,
        IngestOutcome.REJECTED,
    )
    session.commit()
