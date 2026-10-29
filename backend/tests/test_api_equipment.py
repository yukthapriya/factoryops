import uuid

import pytest

VALID = {
    "code": "CNC-99",
    "name": "Test Mill",
    "station": "Machining",
    "equipment_type": "CNC Mill",
    "thresholds": [{"metric": "temperature", "warning_high": 70, "critical_high": 85}],
}


def create(client, **overrides):
    body = {**VALID, **overrides}
    return client.post("/api/equipment", json=body)


def test_create_get_update_deactivate(client):
    r = create(client)
    assert r.status_code == 201, r.text
    eq = r.json()
    assert eq["code"] == "CNC-99" and eq["status"] == "idle" and eq["is_active"] is True
    assert eq["thresholds"][0]["critical_high"] == 85

    r = client.get(f"/api/equipment/{eq['id']}")
    assert r.status_code == 200 and r.json()["name"] == "Test Mill"

    r = client.patch(
        f"/api/equipment/{eq['id']}",
        json={
            "name": "Renamed Mill",
            "status": "maintenance",
            "thresholds": [
                {"metric": "vibration", "warning_high": 4, "critical_high": 6},
                {"metric": "pressure", "warning_low": 3, "critical_low": 2},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Renamed Mill" and body["status"] == "maintenance"
    assert sorted(t["metric"] for t in body["thresholds"]) == ["pressure", "vibration"]

    r = client.delete(f"/api/equipment/{eq['id']}")
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get("/api/equipment").json()["total"] == 0
    assert client.get("/api/equipment?include_inactive=true").json()["total"] == 1


def test_duplicate_code_conflict(client):
    assert create(client).status_code == 201
    r = create(client)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    assert r.json()["error"]["request_id"]


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"code": "lowercase"}, "code"),
        ({"name": "x"}, "name"),
        ({"status": "exploded"}, "status"),
        (
            {"thresholds": [{"metric": "temperature", "warning_high": 90, "critical_high": 80}]},
            "warning_high must be <= critical_high",
        ),
        (
            {"thresholds": [{"metric": "temperature", "warning_high": 400}]},
            "plausible sensor range",
        ),
        ({"thresholds": [{"metric": "temperature"}]}, "at least one limit"),
        (
            {
                "thresholds": [
                    {"metric": "pressure", "warning_high": 5},
                    {"metric": "pressure", "warning_high": 6},
                ]
            },
            "at most once",
        ),
        (
            {"thresholds": [{"metric": "pressure", "warning_low": 8, "warning_high": 5}]},
            "low limits must be below",
        ),
    ],
)
def test_field_validation(client, overrides, fragment):
    r = create(client, **overrides)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert any(fragment in d["msg"] or fragment in d["loc"] for d in err["details"]), err


def test_search_filter_sort_paginate(client):
    for i, (station, status) in enumerate(
        [("Paint", "running"), ("Paint", "idle"), ("Weld", "running"), ("Weld", "warning")]
    ):
        assert (
            create(
                client, code=f"EQ-{i}", name=f"Unit {i}", station=station, status=status
            ).status_code
            == 201
        )
    r = client.get("/api/equipment?station=Paint").json()
    assert r["total"] == 2
    r = client.get("/api/equipment?status=running&sort=-code").json()
    assert [e["code"] for e in r["items"]] == ["EQ-2", "EQ-0"]
    r = client.get("/api/equipment?q=unit 3").json()
    assert [e["code"] for e in r["items"]] == ["EQ-3"]
    page2 = client.get("/api/equipment?page=2&page_size=3&sort=code").json()
    assert page2["total"] == 4 and [e["code"] for e in page2["items"]] == ["EQ-3"]
    assert client.get("/api/equipment/stations").json() == ["Paint", "Weld"]
    assert client.get("/api/equipment?sort=bogus").status_code == 400
    assert client.get("/api/equipment?page_size=1000").status_code == 422


def test_not_found_and_bad_uuid(client):
    r = client.get(f"/api/equipment/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert client.get("/api/equipment/not-a-uuid").status_code == 422
    assert client.patch(f"/api/equipment/{uuid.uuid4()}", json={"name": "abc"}).status_code == 404
