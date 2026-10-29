"""Pure validation of raw telemetry messages (wire schema v1).

Wire format::

    {
      "schema_version": 1,
      "event_id": "<uuid>",
      "equipment_id": "PRESS-01",            # equipment code
      "event_time": "2026-01-01T12:00:00Z",  # ISO-8601, timezone required
      "machine_state": "running|idle|stopped|fault",
      "batch_id": "B-0001",
      "metrics": {"temperature_c": 70.1, "vibration_mm_s": 2.3,
                  "pressure_bar": 6.1, "cycle_time_s": 41.0},
      "quality_check": null | {"result": "pass|fail", "category": "...",
                               "severity": "...", "description": "..."}
    }

Validation here is deterministic and has no I/O. Checks needing the database (unknown
equipment, duplicates, out-of-order) are done by the processor.
"""

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from factoryops.domain import (
    PLAUSIBLE_RANGES,
    DefectCategory,
    DefectSeverity,
    DQCategory,
    MachineState,
    Metric,
)

SCHEMA_VERSION = 1
METRIC_FIELDS: dict[str, Metric] = {
    "temperature_c": Metric.TEMPERATURE,
    "vibration_mm_s": Metric.VIBRATION,
    "pressure_bar": Metric.PRESSURE,
    "cycle_time_s": Metric.CYCLE_TIME,
}
TOP_LEVEL_FIELDS = (
    "schema_version",
    "event_id",
    "equipment_id",
    "event_time",
    "machine_state",
    "batch_id",
    "metrics",
)
_SAFE_KEYS = set(TOP_LEVEL_FIELDS) | {"quality_check"}
_MAX_STR = 64


@dataclass(frozen=True)
class QualityCheck:
    category: DefectCategory
    severity: DefectSeverity
    description: str


@dataclass(frozen=True)
class ValidEvent:
    event_id: uuid.UUID
    equipment_code: str
    event_time: datetime
    machine_state: MachineState
    batch_id: str
    temperature_c: float
    vibration_mm_s: float
    pressure_bar: float
    cycle_time_s: float
    defect: QualityCheck | None
    is_late: bool

    def metric_value(self, metric: Metric) -> float:
        return {
            Metric.TEMPERATURE: self.temperature_c,
            Metric.VIBRATION: self.vibration_mm_s,
            Metric.PRESSURE: self.pressure_bar,
            Metric.CYCLE_TIME: self.cycle_time_s,
        }[metric]


@dataclass(frozen=True)
class Rejection:
    category: DQCategory
    reason: str
    field: str | None = None
    event_id: str | None = None
    equipment_code: str | None = None
    payload_excerpt: dict | None = None


@dataclass(frozen=True)
class ValidationPolicy:
    late_after: timedelta
    max_age: timedelta
    max_future_skew: timedelta


def _truncate(value: object) -> object:
    if isinstance(value, str):
        return value[:_MAX_STR]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return f"<{type(value).__name__}>"


def safe_excerpt(payload: object) -> dict:
    """Keep only known schema fields with truncated scalar values. Unknown keys are listed by
    name only so arbitrary (possibly sensitive) content is never persisted."""
    if not isinstance(payload, dict):
        return {"_type": type(payload).__name__}
    out: dict = {}
    for key, value in payload.items():
        if key not in _SAFE_KEYS:
            continue
        if isinstance(value, dict):
            out[key] = {str(k)[:_MAX_STR]: _truncate(v) for k, v in list(value.items())[:10]}
        else:
            out[key] = _truncate(value)
    extra = [str(k)[:_MAX_STR] for k in payload if k not in _SAFE_KEYS]
    if extra:
        out["_unknown_keys"] = extra[:10]
    return out


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def decode(raw: bytes | str | dict) -> dict | Rejection:
    if isinstance(raw, dict):
        return raw
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Rejection(
            DQCategory.MALFORMED,
            f"Message is not valid UTF-8 JSON: {type(exc).__name__}",
            payload_excerpt={"raw_length": len(raw)},
        )
    if not isinstance(payload, dict):
        return Rejection(
            DQCategory.MALFORMED,
            "Message must be a JSON object",
            payload_excerpt=safe_excerpt(payload),
        )
    return payload


def validate_event(
    raw: bytes | str | dict, now: datetime, policy: ValidationPolicy
) -> ValidEvent | Rejection:
    payload = decode(raw)
    if isinstance(payload, Rejection):
        return payload

    raw_event_id = payload.get("event_id")
    raw_code = payload.get("equipment_id")
    ctx = {
        "event_id": str(raw_event_id)[:_MAX_STR] if raw_event_id is not None else None,
        "equipment_code": str(raw_code)[:_MAX_STR] if raw_code is not None else None,
        "payload_excerpt": safe_excerpt(payload),
    }

    def reject(category: DQCategory, reason: str, field: str | None = None) -> Rejection:
        return Rejection(category, reason, field, **ctx)

    for name in TOP_LEVEL_FIELDS:
        if payload.get(name) is None:
            return reject(DQCategory.MISSING_FIELD, f"Required field '{name}' is missing", name)

    if payload["schema_version"] != SCHEMA_VERSION:
        return reject(
            DQCategory.INVALID_TYPE,
            f"Unsupported schema_version {payload['schema_version']!r}; expected {SCHEMA_VERSION}",
            "schema_version",
        )
    try:
        event_id = uuid.UUID(str(raw_event_id))
    except ValueError:
        return reject(DQCategory.INVALID_TYPE, "event_id is not a valid UUID", "event_id")

    if not isinstance(raw_code, str) or not raw_code.strip():
        return reject(
            DQCategory.INVALID_TYPE, "equipment_id must be a non-empty string", "equipment_id"
        )
    batch_id = payload["batch_id"]
    if not isinstance(batch_id, str) or not 1 <= len(batch_id) <= 48:
        return reject(
            DQCategory.INVALID_TYPE, "batch_id must be a 1-48 character string", "batch_id"
        )

    try:
        state = MachineState(payload["machine_state"])
    except ValueError:
        return reject(
            DQCategory.INVALID_TYPE,
            f"machine_state must be one of {[s.value for s in MachineState]}",
            "machine_state",
        )

    metrics = payload["metrics"]
    if not isinstance(metrics, dict):
        return reject(DQCategory.INVALID_TYPE, "metrics must be an object", "metrics")
    values: dict[str, float] = {}
    for field, metric in METRIC_FIELDS.items():
        value = metrics.get(field)
        path = f"metrics.{field}"
        if value is None:
            return reject(DQCategory.MISSING_FIELD, f"Required metric '{field}' is missing", path)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            return reject(DQCategory.INVALID_TYPE, f"{field} must be a finite number", path)
        lo, hi = PLAUSIBLE_RANGES[metric]
        if not lo <= value <= hi:
            return reject(
                DQCategory.OUT_OF_RANGE,
                f"{field}={value} is outside the physically plausible range [{lo}, {hi}]",
                path,
            )
        values[field] = float(value)

    event_time = _parse_time(payload["event_time"])
    if event_time is None:
        return reject(
            DQCategory.INVALID_TIMESTAMP,
            "event_time must be an ISO-8601 timestamp with a timezone offset",
            "event_time",
        )
    if event_time - now > policy.max_future_skew:
        return reject(
            DQCategory.INVALID_TIMESTAMP,
            f"event_time is {(event_time - now).total_seconds():.0f}s in the future "
            f"(max skew {policy.max_future_skew.total_seconds():.0f}s)",
            "event_time",
        )
    if now - event_time > policy.max_age:
        return reject(
            DQCategory.INVALID_TIMESTAMP,
            f"event_time is older than the {policy.max_age.total_seconds() / 3600:.0f}h "
            "acceptance window",
            "event_time",
        )

    defect: QualityCheck | None = None
    qc = payload.get("quality_check")
    if qc is not None:
        if not isinstance(qc, dict) or qc.get("result") not in ("pass", "fail"):
            return reject(
                DQCategory.INVALID_TYPE,
                "quality_check.result must be 'pass' or 'fail'",
                "quality_check.result",
            )
        if qc["result"] == "fail":
            try:
                category = DefectCategory(qc.get("category"))
                severity = DefectSeverity(qc.get("severity"))
            except ValueError:
                return reject(
                    DQCategory.INVALID_TYPE,
                    "failed quality_check needs a valid category and severity",
                    "quality_check",
                )
            description = str(qc.get("description") or f"{category.value} defect detected")[:500]
            defect = QualityCheck(category, severity, description)

    return ValidEvent(
        event_id=event_id,
        equipment_code=raw_code.strip(),
        event_time=event_time,
        machine_state=state,
        batch_id=batch_id,
        temperature_c=values["temperature_c"],
        vibration_mm_s=values["vibration_mm_s"],
        pressure_bar=values["pressure_bar"],
        cycle_time_s=values["cycle_time_s"],
        defect=defect,
        is_late=now - event_time > policy.late_after,
    )
