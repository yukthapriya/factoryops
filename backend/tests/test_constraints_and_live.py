"""Database constraints, consumer/DLQ handling, live WebSocket and simulator determinism."""

import asyncio
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from factoryops.consumer.__main__ import handle_message
from factoryops.live import CLIENT_QUEUE_SIZE, LiveHub
from factoryops.models import Alert, DataQualityFailure, ProductionRun, TelemetryEvent
from factoryops.pipeline.processor import TransientProcessingError
from factoryops.simulator.generator import TelemetryGenerator
from tests.conftest import as_bytes, make_event

T0 = datetime(2026, 3, 1, tzinfo=UTC)


def _run(equipment, **kw):
    base = dict(
        run_code=f"R-{uuid.uuid4().hex[:6]}",
        batch_id="B",
        equipment_id=equipment.id,
        started_at=T0,
        status="completed",
        units_produced=10,
        defect_count=0,
    )
    return ProductionRun(**{**base, **kw})


@pytest.mark.parametrize(
    "kw",
    [
        {"units_produced": -1},
        {"defect_count": -1},
        {"defect_count": 11},
        {"ended_at": T0 - timedelta(seconds=1)},
        {"status": "paused"},
    ],
)
def test_run_check_constraints(db, equipment, kw):
    db.add(_run(equipment, **kw))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_only_one_running_run_per_equipment(db, equipment):
    db.add(_run(equipment, status="running"))
    db.commit()
    db.add(_run(equipment, status="running"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_only_one_active_alert_per_key(db, equipment):
    def alert(status):
        return Alert(
            equipment_id=equipment.id,
            metric="temperature",
            rule="threshold_high",
            rule_detail="x",
            severity="warning",
            status=status,
            triggered_at=T0,
            last_seen_at=T0,
        )

    db.add_all([alert("resolved"), alert("resolved"), alert("open")])
    db.commit()
    db.add(alert("acknowledged"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---------------------------------------------------------------- consumer


class FakeMsg:
    def __init__(self, value: bytes):
        self._value = value

    def value(self):
        return self._value

    def key(self):
        return b"TEST-01"

    def topic(self):
        return "factoryops.telemetry"

    def partition(self):
        return 0

    def offset(self):
        return 42


class FakeProducer:
    def __init__(self):
        self.produced = []

    def produce(self, topic, key=None, value=None, headers=None):
        self.produced.append((topic, value, headers))

    def flush(self, timeout=None):
        return 0


def test_consumer_processes_and_counts(session_factory, db, equipment):
    counters: Counter = Counter()
    event = make_event(event_time=datetime.now(UTC) - timedelta(seconds=1))
    handle_message(FakeMsg(as_bytes(event)), session_factory, FakeProducer(), "dlq", counters)
    handle_message(FakeMsg(as_bytes(event)), session_factory, FakeProducer(), "dlq", counters)
    handle_message(FakeMsg(b"garbage"), session_factory, FakeProducer(), "dlq", counters)
    assert counters == {"accepted": 1, "duplicate": 1, "rejected": 1, "rejected:malformed": 1}
    assert db.scalar(select(DataQualityFailure.source).limit(1)) == "factoryops.telemetry[0]@42"


def test_consumer_dead_letters_unexpected_errors(session_factory, db, monkeypatch):
    from factoryops.consumer import __main__ as consumer_mod

    def boom(*a, **k):
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr(consumer_mod, "process_with_retry", boom)
    producer = FakeProducer()
    counters: Counter = Counter()
    payload = as_bytes(make_event())
    handle_message(FakeMsg(payload), session_factory, producer, "dlq-topic", counters)
    assert counters["dead_lettered"] == 1
    topic, value, headers = producer.produced[0]
    assert topic == "dlq-topic" and value == payload and "unexpected bug" in headers["x-error"]
    failure = db.scalar(select(DataQualityFailure))
    assert failure.category == "processing_error"
    assert db.scalar(select(TelemetryEvent)) is None


def test_consumer_propagates_transient_errors(session_factory, monkeypatch):
    from factoryops.consumer import __main__ as consumer_mod

    def down(*a, **k):
        raise TransientProcessingError("db down")

    monkeypatch.setattr(consumer_mod, "process_with_retry", down)
    producer = FakeProducer()
    with pytest.raises(TransientProcessingError):
        handle_message(FakeMsg(b"{}"), session_factory, producer, "dlq", Counter())
    assert producer.produced == []  # not dead-lettered: must be redelivered


# ---------------------------------------------------------------- live updates


def test_websocket_hello_and_broadcast(client):
    from factoryops.live import hub

    with client.websocket_connect("/ws/live") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and "listener_connected" in hello["data"]
        assert set(hello) == {"type", "ts", "data"}
        hub.broadcast(json.dumps({"type": "alert", "ts": "t", "data": {"alert_id": "a"}}))
        msg = ws.receive_json()
        assert msg["type"] == "alert" and msg["data"]["alert_id"] == "a"


def test_hub_drops_oldest_for_slow_clients():
    hub = LiveHub()

    class DummyWS:
        pass

    client = hub.register(DummyWS())
    for i in range(CLIENT_QUEUE_SIZE + 5):
        hub.broadcast(str(i))
    assert client.queue.qsize() == CLIENT_QUEUE_SIZE
    assert client.dropped == 5
    assert asyncio.run(client.queue.get()) == "5"


def test_notifications_reach_listener(db, equipment):
    """End-to-end: a committed pipeline write emits NOTIFY payloads on the configured channel."""
    import psycopg

    from factoryops.config import get_settings
    from factoryops.pipeline.processor import process_message
    from tests.conftest import TEST_DB_URL

    conninfo = TEST_DB_URL.replace("postgresql+psycopg", "postgresql")
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(f'LISTEN "{get_settings().notify_channel}"')
        process_message(
            db,
            as_bytes(make_event(temperature=85, event_time=datetime.now(UTC))),
            now=datetime.now(UTC),
        )
        types = [json.loads(n.payload)["type"] for n in conn.notifies(timeout=2, stop_after=3)]
    assert set(types) >= {"alert", "telemetry", "equipment_status"}


# ---------------------------------------------------------------- simulator


def test_simulator_is_deterministic():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    a = [m.value for m in TelemetryGenerator(7, "mixed", start=start).run(20)]
    b = [m.value for m in TelemetryGenerator(7, "mixed", start=start).run(20)]
    c = [m.value for m in TelemetryGenerator(8, "mixed", start=start).run(20)]
    assert a == b
    assert a != c


@pytest.mark.parametrize(
    "scenario, predicate",
    [
        ("temperature_drift", lambda ps: max(p["metrics"]["temperature_c"] for p in ps) > 100),
        ("shutdown", lambda ps: any(p["machine_state"] == "fault" for p in ps)),
        ("duplicates", lambda ps: len({p["event_id"] for p in ps}) < len(ps)),
        (
            "missing_fields",
            lambda ps: any(
                "event_time" not in p
                or "batch_id" not in p
                or "machine_state" not in p
                or "pressure_bar" not in p["metrics"]
                for p in ps
            ),
        ),
        (
            "defects",
            lambda ps: any((p.get("quality_check") or {}).get("result") == "fail" for p in ps),
        ),
    ],
)
def test_scenarios_inject_expected_anomalies(scenario, predicate):
    gen = TelemetryGenerator(3, scenario, start=datetime(2026, 1, 1, tzinfo=UTC))
    payloads = [json.loads(m.value) for m in gen.run(60)]
    assert predicate(payloads)


def test_out_of_order_scenario_emits_older_after_newer():
    gen = TelemetryGenerator(3, "out_of_order", start=datetime(2026, 1, 1, tzinfo=UTC))
    msgs = list(gen.run(30))
    per_key: dict[str, datetime] = {}
    regressions = 0
    for m in msgs:
        if m.key in per_key and m.event_time < per_key[m.key]:
            regressions += 1
        per_key[m.key] = max(per_key.get(m.key, m.event_time), m.event_time)
    assert regressions > 0


# ---------------------------------------------------------------- consumer diagnosis regression


def test_register_fleet_is_idempotent_and_unblocks_ingestion(session_factory, db):
    """Regression: the Docker stack never registered equipment, so 100% of simulated events were
    rejected as unknown_equipment (consumer logged processed=500, rejected=500)."""
    from factoryops.cli.__main__ import main as cli_main
    from factoryops.models import Equipment
    from factoryops.simulator.fleet import FLEET

    counters: Counter = Counter()
    event = make_event(code="WELD-01", event_time=datetime.now(UTC) - timedelta(seconds=1))
    handle_message(FakeMsg(as_bytes(event)), session_factory, FakeProducer(), "dlq", counters)
    assert counters["rejected:unknown_equipment"] == 1

    assert cli_main(["register-fleet"]) == 0
    assert cli_main(["register-fleet"]) == 0  # second run creates nothing
    assert db.scalar(select(func.count()).select_from(Equipment)) == len(FLEET)

    retry = make_event(code="WELD-01", event_time=datetime.now(UTC) - timedelta(seconds=1))
    handle_message(FakeMsg(as_bytes(retry)), session_factory, FakeProducer(), "dlq", counters)
    assert counters["accepted"] == 1


def test_progress_report_warns_and_names_the_cause():
    import logging

    from factoryops.consumer.__main__ import progress_report

    window = Counter(
        {"rejected": 500, "rejected:unknown_equipment": 490, "rejected:out_of_range": 10}
    )
    level, msg, extra = progress_report(window, 500)
    assert level == logging.WARNING
    assert "unknown_equipment=490" in msg and "register-fleet" in msg
    assert extra["rejection_rate"] == 1.0
    assert extra["rejected_by_category"] == {"unknown_equipment": 490, "out_of_range": 10}

    healthy = Counter({"accepted": 495, "rejected": 5, "rejected:missing_field": 5})
    level, msg, extra = progress_report(healthy, 1000)
    assert level == logging.INFO and extra["accepted"] == 495 and extra["rejection_rate"] == 0.01


def test_long_running_drift_stays_physically_plausible():
    """Regression: drift grew without bound in --forever mode (PRESS-02 reached 208 °C and was
    heading past the 250 °C plausibility limit, which would turn into out_of_range rejections)."""
    gen = TelemetryGenerator(5, "temperature_drift", start=datetime(2026, 1, 1, tzinfo=UTC))
    temps = [json.loads(m.value)["metrics"]["temperature_c"] for m in gen.run(600)]
    assert max(temps) < 150  # nominal max 71 °C + 0.9 × 59 + noise
    assert max(temps) > 96  # still crosses the critical limit (nominal + 25 °C)
