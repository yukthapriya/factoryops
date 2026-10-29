"""Domain vocabulary shared by models, schemas, pipeline and simulator."""

from enum import StrEnum


class EquipmentStatus(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    WARNING = "warning"
    CRITICAL = "critical"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


class MachineState(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    STOPPED = "stopped"
    FAULT = "fault"


class Metric(StrEnum):
    TEMPERATURE = "temperature"
    VIBRATION = "vibration"
    PRESSURE = "pressure"
    CYCLE_TIME = "cycle_time"


METRIC_UNITS: dict[Metric, str] = {
    Metric.TEMPERATURE: "°C",
    Metric.VIBRATION: "mm/s",
    Metric.PRESSURE: "bar",
    Metric.CYCLE_TIME: "s",
}

# Physically plausible sensor ranges. Values outside are treated as sensor/data faults and
# rejected by validation. These are deliberately separate from operational alert thresholds,
# which are configured per equipment and must lie inside these ranges.
PLAUSIBLE_RANGES: dict[Metric, tuple[float, float]] = {
    Metric.TEMPERATURE: (-40.0, 250.0),
    Metric.VIBRATION: (0.0, 100.0),
    Metric.PRESSURE: (0.0, 20.0),
    Metric.CYCLE_TIME: (0.0, 600.0),
}

# Column name on telemetry_events for each metric.
METRIC_COLUMNS: dict[Metric, str] = {
    Metric.TEMPERATURE: "temperature_c",
    Metric.VIBRATION: "vibration_mm_s",
    Metric.PRESSURE: "pressure_bar",
    Metric.CYCLE_TIME: "cycle_time_s",
}


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"


class DefectCategory(StrEnum):
    DIMENSIONAL = "dimensional"
    SURFACE = "surface"
    ASSEMBLY = "assembly"
    MATERIAL = "material"
    WELD = "weld"
    OTHER = "other"


class DefectSeverity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class DefectStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class AlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class AlertRule(StrEnum):
    THRESHOLD_HIGH = "threshold_high"
    THRESHOLD_LOW = "threshold_low"
    SPC_3SIGMA = "spc_3sigma"
    MACHINE_FAULT = "machine_fault"


class DQCategory(StrEnum):
    MALFORMED = "malformed"  # not JSON / not an object
    MISSING_FIELD = "missing_field"
    INVALID_TYPE = "invalid_type"
    OUT_OF_RANGE = "out_of_range"
    INVALID_TIMESTAMP = "invalid_timestamp"
    UNKNOWN_EQUIPMENT = "unknown_equipment"
    DUPLICATE = "duplicate"
    PROCESSING_ERROR = "processing_error"


class IngestOutcome(StrEnum):
    ACCEPTED = "accepted"
    ACCEPTED_LATE = "accepted_late"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


def sql_in(enum_cls: type[StrEnum]) -> str:
    return ", ".join(f"'{m.value}'" for m in enum_cls)
