"""Deterministic synthetic telemetry generator.

Given the same seed, start time and scenario, the generator yields byte-identical messages
(including event_ids), so replaying a seeded run exercises idempotent processing.
"""

import json
import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from factoryops.simulator.fleet import FLEET, SimEquipment

SCENARIOS = (
    "normal",
    "temperature_drift",
    "vibration_spike",
    "shutdown",
    "out_of_range",
    "missing_fields",
    "duplicates",
    "out_of_order",
    "defects",
    "mixed",
)

_NAMESPACE = uuid.UUID("6f1c2f7e-3b7a-4a55-9a43-1f5b8d5c0e11")
_DEFECT_CATEGORIES = {
    "Stamping Press": ["dimensional", "surface"],
    "Welding Robot": ["weld", "dimensional"],
    "Paint Booth": ["surface"],
    "CNC Mill": ["dimensional", "material"],
    "Assembly Robot": ["assembly", "other"],
}
BATCH_MINUTES = 30
DRIFT_PERIOD = 60


@dataclass(frozen=True)
class SimMessage:
    key: str  # equipment code (Kafka partition key)
    value: bytes
    event_time: datetime
    note: str  # human-readable reason for injected anomalies ("" when normal)


class TelemetryGenerator:
    def __init__(
        self,
        seed: int,
        scenario: str = "normal",
        start: datetime | None = None,
        interval_s: float = 5.0,
        fleet: list[SimEquipment] | None = None,
        target_codes: list[str] | None = None,
        base_defect_rate: float = 0.015,
    ):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}; choose from {SCENARIOS}")
        if start is None or start.tzinfo is None:
            raise ValueError("start must be a timezone-aware datetime")
        self.seed = seed
        self.scenario = scenario
        self.start = start
        self.interval = timedelta(seconds=interval_s)
        self.fleet = fleet or FLEET
        self.rng = random.Random(seed)
        # Scenario targets: deterministic choice from the fleet unless given explicitly.
        codes = [e.code for e in self.fleet]
        self.targets = set(target_codes or self.rng.sample(codes, k=min(2, len(codes))))
        self.base_defect_rate = base_defect_rate
        self._held: list[SimMessage] = []
        self._history: list[SimMessage] = []

    def _event_id(self, tick: int, code: str, variant: str = "") -> str:
        return str(
            uuid.uuid5(_NAMESPACE, f"{self.seed}|{self.start.isoformat()}|{tick}|{code}|{variant}")
        )

    @staticmethod
    def batch_for(code: str, t: datetime) -> str:
        slot = t.replace(
            minute=(t.minute // BATCH_MINUTES) * BATCH_MINUTES, second=0, microsecond=0
        )
        return f"B{slot:%Y%m%d%H%M}"

    def _active(self, name: str) -> bool:
        return self.scenario == name or self.scenario == "mixed"

    def _payload(self, eq: SimEquipment, tick: int, t: datetime) -> tuple[dict, str]:
        rng = self.rng
        targeted = eq.code in self.targets
        note = ""
        temp = rng.gauss(eq.temp_mean, 1.2)
        vib = abs(rng.gauss(eq.vib_mean, eq.vib_mean * 0.08))
        pressure = rng.gauss(eq.pressure_mean, eq.pressure_mean * 0.03)
        cycle = abs(rng.gauss(eq.cycle_mean, eq.cycle_mean * 0.04))
        state = "running" if rng.random() > 0.03 else "idle"

        if targeted and self._active("temperature_drift"):
            # Gradual heating that crosses warning/critical limits, then resets (cool-down /
            # maintenance) every DRIFT_PERIOD ticks so --forever runs stay physically plausible.
            temp += 0.9 * (tick % DRIFT_PERIOD)
            note = "temperature drift"
        if targeted and self._active("vibration_spike") and tick >= 5 and tick % 7 in (0, 1, 2):
            vib *= rng.uniform(2.6, 4.0)
            note = "vibration spike"
        if targeted and self._active("shutdown") and tick >= 10:
            if tick == 10:
                state = "fault"
                note = "sudden fault"
            else:
                state = "stopped"
                cycle = 0.0
                note = "shutdown"
                temp = max(eq.temp_mean - 20, temp - 0.5 * (tick - 10))

        defect_rate = self.base_defect_rate
        if targeted and self._active("defects"):
            defect_rate = 0.25
        qc = None
        if state == "running":
            if rng.random() < defect_rate:
                categories = _DEFECT_CATEGORIES.get(eq.equipment_type, ["other"])
                category = rng.choice(categories)
                severity = rng.choices(["minor", "major", "critical"], weights=[6, 3, 1])[0]
                qc = {
                    "result": "fail",
                    "category": category,
                    "severity": severity,
                    "description": f"Synthetic {category} defect flagged by inline inspection",
                }
                note = note or "defect"
            else:
                qc = {"result": "pass"}

        payload = {
            "schema_version": 1,
            "event_id": self._event_id(tick, eq.code),
            "equipment_id": eq.code,
            "event_time": t.isoformat().replace("+00:00", "Z"),
            "machine_state": state,
            "batch_id": self.batch_for(eq.code, t),
            "metrics": {
                "temperature_c": round(temp, 2),
                "vibration_mm_s": round(vib, 3),
                "pressure_bar": round(pressure, 3),
                "cycle_time_s": round(cycle, 2),
            },
            "quality_check": qc,
        }
        return payload, note

    def _corrupt(self, payload: dict, eq: SimEquipment) -> tuple[dict, str]:
        rng = self.rng
        if self._active("out_of_range") and rng.random() < (
            0.08 if self.scenario != "mixed" else 0.01
        ):
            field = rng.choice(["temperature_c", "pressure_bar", "vibration_mm_s"])
            payload["metrics"][field] = {
                "temperature_c": 999.0,
                "pressure_bar": -4.0,
                "vibration_mm_s": 250.0,
            }[field]
            return payload, f"out-of-range {field}"
        if self._active("missing_fields") and rng.random() < (
            0.08 if self.scenario != "mixed" else 0.01
        ):
            choice = rng.choice(["event_time", "batch_id", "metrics.pressure_bar", "machine_state"])
            if choice.startswith("metrics."):
                payload["metrics"].pop(choice.split(".")[1])
            else:
                payload.pop(choice)
            return payload, f"missing {choice}"
        return payload, ""

    def tick(self, tick: int, now: datetime | None = None) -> list[SimMessage]:
        """Messages for one tick. `now` overrides the event time (live mode)."""
        t = now or self.start + tick * self.interval
        out: list[SimMessage] = []
        for eq in self.fleet:
            payload, note = self._payload(eq, tick, t)
            payload, corrupt_note = self._corrupt(payload, eq)
            msg = SimMessage(eq.code, json.dumps(payload).encode(), t, corrupt_note or note)
            if (
                self._active("out_of_order")
                and eq.code in self.targets
                and self.rng.random() < (0.3 if self.scenario != "mixed" else 0.1)
            ):
                self._held.append(msg)  # delayed delivery: emitted after newer events
                continue
            out.append(msg)
        # Release held (now out-of-order) messages after newer ones from this tick.
        if self._held and (tick % 3 == 2):
            out.extend(
                SimMessage(m.key, m.value, m.event_time, "delayed / out-of-order")
                for m in self._held
            )
            self._held.clear()
        if self._active("duplicates") and self._history:
            for prev in self.rng.sample(
                self._history, k=min(len(self._history), 2 if self.scenario != "mixed" else 1)
            ):
                if self.scenario != "mixed" or self.rng.random() < 0.3:
                    out.append(
                        SimMessage(prev.key, prev.value, prev.event_time, "duplicate redelivery")
                    )
        self._history = out[-len(self.fleet) :]
        return out

    def run(self, ticks: int) -> Iterator[SimMessage]:
        for i in range(ticks):
            yield from self.tick(i)
        if self._held:
            yield from (
                SimMessage(m.key, m.value, m.event_time, "delayed / out-of-order")
                for m in self._held
            )
            self._held.clear()
