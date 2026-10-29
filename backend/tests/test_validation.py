from datetime import timedelta

import pytest

from factoryops.domain import DQCategory, MachineState
from factoryops.pipeline.validation import (
    Rejection,
    ValidationPolicy,
    ValidEvent,
    safe_excerpt,
    validate_event,
)
from tests.conftest import NOW, as_bytes, make_event

POLICY = ValidationPolicy(
    late_after=timedelta(seconds=120),
    max_age=timedelta(days=7),
    max_future_skew=timedelta(seconds=300),
)


def check(event, now=NOW):
    return validate_event(as_bytes(event) if isinstance(event, dict) else event, now, POLICY)


def test_valid_event_is_parsed():
    result = check(make_event(temperature=71.5))
    assert isinstance(result, ValidEvent)
    assert result.temperature_c == 71.5
    assert result.machine_state == MachineState.RUNNING
    assert result.is_late is False
    assert result.defect is None


@pytest.mark.parametrize(
    "raw, reason_fragment",
    [(b"{not json", "not valid UTF-8 JSON"), (b"[1,2]", "JSON object"), (b"\xff\xfe", "UTF-8")],
)
def test_malformed_messages(raw, reason_fragment):
    result = check(raw)
    assert isinstance(result, Rejection)
    assert result.category == DQCategory.MALFORMED
    assert reason_fragment in result.reason


@pytest.mark.parametrize(
    "field", ["event_id", "event_time", "batch_id", "machine_state", "metrics.pressure_bar"]
)
def test_missing_fields(field):
    result = check(make_event(drop=(field,)))
    assert isinstance(result, Rejection)
    assert result.category == DQCategory.MISSING_FIELD
    assert result.field == field


def test_invalid_uuid_and_enum():
    assert check(make_event(event_id="abc")).category == DQCategory.INVALID_TYPE
    assert check(make_event(state="exploding")).category == DQCategory.INVALID_TYPE
    assert check(make_event(schema_version=2)).category == DQCategory.INVALID_TYPE


@pytest.mark.parametrize("value", ["hot", True, float("nan"), float("inf")])
def test_non_numeric_metric_rejected(value):
    event = make_event()
    event["metrics"]["temperature_c"] = value
    result = validate_event(event, NOW, POLICY)
    assert isinstance(result, Rejection)
    assert result.category == DQCategory.INVALID_TYPE


@pytest.mark.parametrize(
    "field, value, ok",
    [
        ("temperature_c", 250.0, True),  # upper bound inclusive
        ("temperature_c", 250.01, False),
        ("temperature_c", -40.0, True),
        ("pressure_bar", -0.1, False),
        ("vibration_mm_s", 100.0, True),
        ("vibration_mm_s", 250.0, False),
    ],
)
def test_plausible_range_boundaries(field, value, ok):
    event = make_event()
    event["metrics"][field] = value
    result = validate_event(event, NOW, POLICY)
    if ok:
        assert isinstance(result, ValidEvent)
    else:
        assert result.category == DQCategory.OUT_OF_RANGE
        assert "plausible range" in result.reason


def test_timestamp_rules():
    naive = make_event(event_time="2026-03-01T11:59:00")
    assert check(naive).category == DQCategory.INVALID_TIMESTAMP
    garbage = make_event(event_time="yesterday")
    assert check(garbage).category == DQCategory.INVALID_TIMESTAMP
    future = make_event(event_time=NOW + timedelta(minutes=10))
    assert check(future).category == DQCategory.INVALID_TIMESTAMP
    slightly_future = make_event(event_time=NOW + timedelta(minutes=4))
    assert isinstance(check(slightly_future), ValidEvent)
    ancient = make_event(event_time=NOW - timedelta(days=8))
    assert check(ancient).category == DQCategory.INVALID_TIMESTAMP


def test_late_flag():
    assert check(make_event(event_time=NOW - timedelta(seconds=119))).is_late is False
    assert check(make_event(event_time=NOW - timedelta(seconds=121))).is_late is True


def test_quality_check_fail_creates_defect_spec():
    result = check(
        make_event(quality_check={"result": "fail", "category": "weld", "severity": "major"})
    )
    assert result.defect is not None
    assert result.defect.category == "weld"
    bad = check(make_event(quality_check={"result": "fail", "category": "nope"}))
    assert bad.category == DQCategory.INVALID_TYPE


def test_safe_excerpt_never_stores_unknown_values():
    event = make_event(api_token="SECRET-VALUE", batch_id="x" * 200)
    excerpt = safe_excerpt(event)
    assert "api_token" not in excerpt
    assert excerpt["_unknown_keys"] == ["api_token"]
    assert "SECRET-VALUE" not in str(excerpt)
    assert len(excerpt["batch_id"]) == 64
