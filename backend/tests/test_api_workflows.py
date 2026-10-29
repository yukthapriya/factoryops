"""Alert, defect, production-run, dashboard, data-quality and health endpoints."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from factoryops.models import Alert, Defect
from factoryops.pipeline.processor import process_message
from tests.conftest import as_bytes, make_event


def now():
    return datetime.now(UTC)


def ingest(db, **kw):
    t = kw.pop("event_time", now() - timedelta(seconds=2))
    return process_message(db, as_bytes(make_event(event_time=t, **kw)), now=now())


def test_alert_acknowledge_resolve_and_history(client, db, equipment):
    ingest(db, temperature=85)
    alert_id = str(db.scalar(select(Alert.id)))
    listing = client.get("/api/alerts?status=active").json()
    assert listing["total"] == 1
    assert listing["items"][0]["equipment"]["code"] == "TEST-01"

    r = client.post(f"/api/alerts/{alert_id}/acknowledge", json={"actor": "Dana", "note": "on it"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "acknowledged" and r.json()["acknowledged_at"]

    r = client.post(f"/api/alerts/{alert_id}/acknowledge", json={"actor": "Dana"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"

    r = client.post(f"/api/alerts/{alert_id}/resolve", json={"actor": "Dana", "note": "fan fixed"})
    detail = r.json()
    assert detail["status"] == "resolved" and detail["resolved_at"]
    assert [(h["from_status"], h["to_status"], h["actor"]) for h in detail["history"]] == [
        (None, "open", "system"),
        ("open", "acknowledged", "Dana"),
        ("acknowledged", "resolved", "Dana"),
    ]
    assert detail["event"]["temperature_c"] == 85
    assert client.post(f"/api/alerts/{alert_id}/resolve", json={"actor": "x"}).status_code == 409
    assert client.post(f"/api/alerts/{alert_id}/resolve", json={"actor": ""}).status_code == 422
    assert client.get("/api/alerts?status=resolved").json()["total"] == 1
    assert client.get("/api/alerts?status=weird").status_code == 422


def test_defect_investigation_workflow(client, db, equipment):
    qc = {"result": "fail", "category": "surface", "severity": "minor", "description": "scratch"}
    ingest(db, temperature=82)  # an alert in the evidence window
    ingest(db, quality_check=qc, event_time=now() - timedelta(seconds=1))
    defect_id = str(db.scalar(select(Defect.id)))

    detail = client.get(f"/api/defects/{defect_id}").json()
    assert detail["status"] == "open"
    assert detail["evidence"]["source_event"]["event_id"] == detail["source_event_id"]
    assert len(detail["evidence"]["telemetry"]) == 2
    assert detail["evidence"]["alerts"][0]["metric"] == "temperature"
    assert detail["evidence"]["thresholds"]

    r = client.patch(f"/api/defects/{defect_id}", json={"status": "resolved", "author": "Q"})
    assert r.status_code == 422  # note required to resolve

    r = client.patch(
        f"/api/defects/{defect_id}",
        json={"status": "investigating", "severity": "major", "author": "Quinn"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "investigating" and r.json()["severity"] == "major"

    r = client.post(
        f"/api/defects/{defect_id}/notes", json={"author": "Quinn", "body": "Checked fixture"}
    )
    assert r.status_code == 201 and r.json()["created_at"]

    r = client.patch(
        f"/api/defects/{defect_id}",
        json={"status": "resolved", "author": "Quinn", "note": "Fixture shimmed"},
    )
    notes = r.json()["notes"]
    assert [n["status_to"] for n in notes] == ["investigating", None, "resolved"]
    assert "Fixture shimmed" in notes[-1]["body"]

    r = client.patch(
        f"/api/defects/{defect_id}", json={"status": "dismissed", "author": "Q", "note": "n"}
    )
    assert r.status_code == 409

    listing = client.get("/api/defects?status=resolved&category=surface").json()
    assert listing["total"] == 1
    assert client.get("/api/defects?q=TEST-01").json()["total"] == 1
    assert client.get("/api/defects?q=nothing-matches").json()["total"] == 0


def test_production_runs_listing_and_detail(client, db, equipment):
    qc = {"result": "fail", "category": "weld", "severity": "major"}
    t0 = now() - timedelta(minutes=5)
    for i in range(4):
        ingest(db, batch="B1", event_time=t0 + timedelta(seconds=i))
    ingest(db, batch="B1", quality_check=qc, event_time=t0 + timedelta(seconds=10))
    ingest(db, batch="B2", event_time=t0 + timedelta(seconds=20))

    runs = client.get("/api/production-runs").json()
    assert runs["total"] == 2
    completed = client.get("/api/production-runs?status=completed").json()["items"][0]
    assert (completed["units_produced"], completed["defect_count"]) == (5, 1)
    assert completed["yield_pct"] == 80.0 and completed["defect_rate_pct"] == 20.0

    detail = client.get(f"/api/production-runs/{completed['id']}").json()
    assert detail["telemetry"]["event_count"] == 5
    assert detail["defects"][0]["category"] == "weld"

    future = (now() + timedelta(days=1)).isoformat()
    r = client.get("/api/production-runs", params={"start": future})
    assert r.status_code == 400  # start after end
    r = client.get("/api/production-runs", params={"start": "2026-01-01T00:00:00"})
    assert r.status_code == 400  # naive timestamp


def test_dashboard_and_analytics(client, db, equipment):
    qc = {"result": "fail", "category": "surface", "severity": "minor"}
    for i in range(3):
        ingest(db, event_time=now() - timedelta(seconds=30 - i))
    ingest(db, quality_check=qc, event_time=now() - timedelta(seconds=10))
    process_message(db, as_bytes(make_event(drop=("batch_id",))), now=now())

    d = client.get("/api/dashboard/summary").json()
    assert d["total_equipment"] == 1 and d["running_equipment"] == 1
    assert d["units_produced"] == 4 and d["defects"] == 1 and d["defect_rate_pct"] == 25.0
    assert d["events_received"] == 5 and d["events_rejected"] == 1
    assert "units_produced" in d["calculation_notes"]

    a = client.get("/api/analytics/defects").json()
    assert a["by_station"] == [
        {"station": "Stamping", "units_produced": 4, "defects": 1, "defect_rate_pct": 25.0}
    ]
    eq_rows = client.get("/api/analytics/equipment").json()["rows"]
    assert eq_rows[0]["yield_pct"] == 75.0

    s = client.get("/api/telemetry/summary").json()
    assert sum(b["event_count"] for b in s["buckets"]) == 4
    t = client.get("/api/telemetry", params={"equipment_id": str(equipment.id)}).json()
    assert len(t) == 4


def test_control_chart(client, db, equipment):
    for i in range(30):
        ingest(db, temperature=60 + (i % 2), event_time=now() - timedelta(seconds=100 - i))
    ingest(db, temperature=75, event_time=now() - timedelta(seconds=5))
    r = client.get(
        "/api/analytics/control-chart",
        params={"equipment_id": str(equipment.id), "metric": "temperature"},
    ).json()
    assert r["baseline_source"] == "window"
    assert r["center_line"] == pytest.approx(60.48)  # first 25 points: 13×60 + 12×61
    assert r["points"][-1]["out_of_control"] is True
    assert r["points"][-1]["rule"] == "rule1_beyond_3sigma"


def test_data_quality_endpoints(client, db, equipment):
    process_message(db, as_bytes(make_event(event_id="nope")), now=now())
    e = make_event(event_time=now() - timedelta(seconds=1))
    process_message(db, as_bytes(e), now=now())
    process_message(db, as_bytes(e), now=now())
    summary = client.get("/api/data-quality/summary").json()
    assert (summary["total_received"], summary["valid"], summary["invalid"]) == (3, 1, 1)
    assert summary["duplicates"] == 1
    cats = {c["key"]: c["count"] for c in summary["by_category"]}
    assert cats["invalid_type"] == 1 and cats["duplicate"] == 1
    assert "idempotency" in summary["policy"]

    failures = client.get("/api/data-quality/failures?category=invalid_type").json()
    assert failures["total"] == 1
    item = failures["items"][0]
    assert item["event_id"] == "nope" and "UUID" in item["reason"]
    assert client.get("/api/data-quality/failures?page_size=1&page=2").json()["total"] == 2


def test_health(client, monkeypatch):
    assert client.get("/api/health/live").json() == {"status": "ok", "checks": {}}
    ready = client.get("/api/health/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["database"] == "ok"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from factoryops.api import routes

    broken = sessionmaker(
        bind=create_engine(
            "postgresql+psycopg://localhost:1/none", connect_args={"connect_timeout": 1}
        )
    )
    monkeypatch.setattr(routes, "get_sessionmaker", lambda: broken)
    r = client.get("/api/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"
    assert client.get("/api/health/live").status_code == 200


def test_database_unavailable_returns_503(client):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from factoryops.db import get_db

    broken = sessionmaker(
        bind=create_engine(
            "postgresql+psycopg://localhost:1/none", connect_args={"connect_timeout": 1}
        )
    )

    def broken_db():
        s = broken()
        try:
            yield s
        finally:
            s.close()

    client.app.dependency_overrides[get_db] = broken_db
    try:
        r = client.get("/api/equipment")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "database_unavailable"
    finally:
        client.app.dependency_overrides.clear()


def test_demo_simulate_runs_pipeline(client, db):
    from factoryops.cli.__main__ import upsert_fleet

    upsert_fleet(db)
    r = client.post("/api/demo/simulate", json={"scenario": "duplicates", "ticks": 3, "seed": 1})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["outcomes"]["accepted"] == 45
    assert out["outcomes"]["duplicate"] >= 1
    assert client.post("/api/demo/simulate", json={"ticks": 0}).status_code == 422
