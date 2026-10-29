"""Pipeline tests against a real PostgreSQL test database."""

from datetime import UTC, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from factoryops.domain import DQCategory, IngestOutcome
from factoryops.models import (
    Alert,
    DataQualityFailure,
    Defect,
    Equipment,
    IngestionStat,
    ProductionRun,
    TelemetryEvent,
)
from factoryops.pipeline.processor import (
    TransientProcessingError,
    process_message,
    process_with_retry,
)
from factoryops.schemas import AlertAction
from factoryops.services.alerts import AlertStatus, transition
from tests.conftest import NOW, as_bytes, make_event


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def stats(db):
    return dict(
        db.execute(
            select(IngestionStat.outcome, func.sum(IngestionStat.count)).group_by(
                IngestionStat.outcome
            )
        ).all()
    )


def test_valid_event_persisted_with_run_and_status(db, equipment):
    result = process_message(db, as_bytes(make_event()), now=NOW)
    assert result.outcome == IngestOutcome.ACCEPTED
    assert count(db, TelemetryEvent) == 1
    run = db.scalar(select(ProductionRun))
    assert (run.run_code, run.units_produced, run.status) == ("TEST-01-B1", 1, "running")
    db.expire_all()
    assert db.get(Equipment, equipment.id).status == "running"
    assert stats(db) == {"accepted": 1}


def test_duplicate_event_is_idempotent(db, equipment):
    event = make_event()
    first = process_message(db, as_bytes(event), now=NOW)
    second = process_message(db, as_bytes(event), now=NOW + timedelta(seconds=1))
    third = process_message(db, as_bytes(event), now=NOW + timedelta(seconds=2))
    assert first.outcome == IngestOutcome.ACCEPTED
    assert second.outcome == third.outcome == IngestOutcome.DUPLICATE
    assert count(db, TelemetryEvent) == 1
    assert db.scalar(select(ProductionRun.units_produced)) == 1  # not double counted
    assert count(db, DataQualityFailure, DataQualityFailure.category == "duplicate") == 2
    assert stats(db) == {"accepted": 1, "duplicate": 2}


def test_invalid_event_recorded_not_dropped(db, equipment):
    event = make_event(drop=("metrics.pressure_bar",))
    result = process_message(db, as_bytes(event), now=NOW, source="topic[0]@5")
    assert result.outcome == IngestOutcome.REJECTED
    assert count(db, TelemetryEvent) == 0
    failure = db.scalar(select(DataQualityFailure))
    assert failure.category == DQCategory.MISSING_FIELD
    assert failure.field == "metrics.pressure_bar"
    assert failure.event_id == event["event_id"]
    assert failure.source == "topic[0]@5"
    assert failure.payload_excerpt["equipment_id"] == "TEST-01"


def test_unknown_or_inactive_equipment_rejected(db, equipment):
    r = process_message(db, as_bytes(make_event(code="NOPE-9")), now=NOW)
    assert r.category == DQCategory.UNKNOWN_EQUIPMENT
    equipment.is_active = False
    db.commit()
    r = process_message(db, as_bytes(make_event()), now=NOW)
    assert r.category == DQCategory.UNKNOWN_EQUIPMENT
    assert count(db, TelemetryEvent) == 0


def test_out_of_order_event_stored_late_without_changing_status(db, equipment):
    process_message(db, as_bytes(make_event(event_time=NOW - timedelta(seconds=5))), now=NOW)
    older = make_event(event_time=NOW - timedelta(seconds=30), state="fault")
    result = process_message(db, as_bytes(older), now=NOW)
    assert result.outcome == IngestOutcome.ACCEPTED_LATE
    row = db.get(TelemetryEvent, result.event_id)
    assert row.is_late is True
    db.expire_all()
    eq = db.get(Equipment, equipment.id)
    assert eq.status == "running"  # stale fault does not override current state
    assert eq.last_event_at == NOW - timedelta(seconds=5)
    # but it is still evaluated for alerts
    assert count(db, Alert, Alert.rule == "machine_fault") == 1


def test_delayed_event_flagged_late(db, equipment):
    result = process_message(
        db, as_bytes(make_event(event_time=NOW - timedelta(minutes=10))), now=NOW
    )
    assert result.outcome == IngestOutcome.ACCEPTED_LATE


def test_threshold_alert_dedup_and_escalation(db, equipment):
    t = NOW - timedelta(seconds=60)
    r1 = process_message(db, as_bytes(make_event(event_time=t, temperature=85)), now=NOW)
    assert len(r1.alerts_opened) == 1
    r2 = process_message(
        db,
        as_bytes(make_event(event_time=t + timedelta(seconds=5), temperature=86)),
        now=NOW + timedelta(seconds=5),
    )
    assert r2.alerts_opened == [] and r2.alerts_updated == r1.alerts_opened
    r3 = process_message(
        db,
        as_bytes(make_event(event_time=t + timedelta(seconds=10), temperature=95)),
        now=NOW + timedelta(seconds=10),
    )
    assert r3.alerts_updated == r1.alerts_opened
    process_message(  # a later critical repeat must not overwrite the escalating evidence
        db,
        as_bytes(make_event(event_time=t + timedelta(seconds=15), temperature=92)),
        now=NOW + timedelta(seconds=15),
    )
    alert = db.scalar(select(Alert))
    db.refresh(alert)
    assert alert.occurrence_count == 4
    assert alert.severity == "critical"
    assert alert.threshold_value == 90
    assert alert.observed_value == 95  # value of the escalating event, consistent with detail
    assert "critical limit" in alert.rule_detail
    assert [h.to_status for h in alert.history] == ["open"]
    db.expire_all()
    assert db.get(Equipment, equipment.id).status == "critical"


def test_cooldown_suppresses_reopen_then_allows_after_window(db, equipment, client):
    process_message(db, as_bytes(make_event(temperature=85)), now=NOW)
    alert = db.scalar(select(Alert))
    transition(db, alert.id, AlertStatus.RESOLVED, AlertAction(actor="tester"))
    resolved_at = db.get(Alert, alert.id).resolved_at

    soon = resolved_at + timedelta(seconds=60)
    r = process_message(
        db, as_bytes(make_event(event_time=soon - timedelta(seconds=1), temperature=85)), now=soon
    )
    assert r.alerts_suppressed == 1 and r.alerts_opened == []

    later = resolved_at + timedelta(seconds=301)
    r = process_message(
        db, as_bytes(make_event(event_time=later - timedelta(seconds=1), temperature=85)), now=later
    )
    assert len(r.alerts_opened) == 1
    assert count(db, Alert) == 2


def test_defect_creates_record_and_counts(db, equipment):
    qc = {"result": "fail", "category": "surface", "severity": "minor", "description": "scratch"}
    process_message(db, as_bytes(make_event()), now=NOW)
    result = process_message(db, as_bytes(make_event(quality_check=qc, event_time=NOW)), now=NOW)
    assert result.defect_id is not None
    run = db.scalar(select(ProductionRun))
    db.refresh(run)
    assert (run.units_produced, run.defect_count) == (2, 1)
    defect = db.get(Defect, result.defect_id)
    assert defect.source_event_id is not None
    assert defect.production_run_id == run.id


def test_new_batch_rolls_over_run(db, equipment):
    process_message(
        db, as_bytes(make_event(batch="B1", event_time=NOW - timedelta(seconds=20))), now=NOW
    )
    process_message(
        db, as_bytes(make_event(batch="B2", event_time=NOW - timedelta(seconds=10))), now=NOW
    )
    runs = {r.batch_id: r for r in db.scalars(select(ProductionRun))}
    assert runs["B1"].status == "completed"
    assert runs["B1"].ended_at == NOW - timedelta(seconds=10)
    assert runs["B2"].status == "running"


def test_shutdown_sets_offline(db, equipment):
    process_message(db, as_bytes(make_event(state="stopped", cycle=0)), now=NOW)
    db.expire_all()
    assert db.get(Equipment, equipment.id).status == "offline"
    assert db.scalar(select(ProductionRun.units_produced)) == 0


class BrokenSession:
    """Stands in for a session whose connection was refused."""

    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    scalar = execute

    def rollback(self):
        pass

    def close(self):
        pass


class FlakySession:
    """Session factory whose first N sessions fail with a DB connection error."""

    def __init__(self, real_factory, failures):
        self.real_factory = real_factory
        self.failures = failures
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return BrokenSession() if self.calls <= self.failures else self.real_factory()


def test_transient_errors_retried_with_bounded_backoff(session_factory, equipment):
    flaky = FlakySession(session_factory, failures=2)
    sleeps: list[float] = []
    result = process_with_retry(
        flaky,
        as_bytes(make_event(event_time=NOW.astimezone(UTC))),
        max_retries=3,
        sleep=sleeps.append,
        now_fn=lambda: NOW,
    )
    assert result.outcome == IngestOutcome.ACCEPTED
    assert sleeps == [0.5, 1.0]


def test_retries_exhausted_raises_transient(session_factory, equipment):
    flaky = FlakySession(session_factory, failures=10)
    sleeps: list[float] = []
    with pytest.raises(TransientProcessingError):
        process_with_retry(
            flaky, as_bytes(make_event()), max_retries=2, sleep=sleeps.append, now_fn=lambda: NOW
        )
    assert len(sleeps) == 2 and flaky.calls == 3


def test_non_transient_errors_not_retried(session_factory):
    calls = []

    def factory():
        calls.append(1)

        class Bad:
            def scalar(self, *a, **k):
                raise ValueError("bug")

            execute = scalar

            def rollback(self):
                pass

            def close(self):
                pass

        return Bad()

    with pytest.raises(ValueError):
        process_with_retry(factory, as_bytes(make_event(code="X-1")), now_fn=lambda: NOW)
    assert len(calls) == 1
