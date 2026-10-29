"""Synthetic equipment fleet. All values are invented for demonstration purposes.

Units: temperature °C, vibration mm/s RMS, pressure bar, cycle time seconds.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SimEquipment:
    code: str
    name: str
    station: str
    equipment_type: str
    temp_mean: float
    vib_mean: float
    pressure_mean: float
    cycle_mean: float


# (type, nominal temp, vibration, pressure, cycle time)
_TYPES = {
    "Stamping Press": (62.0, 3.2, 8.5, 12.0),
    "Welding Robot": (71.0, 1.8, 5.5, 38.0),
    "Paint Booth": (38.0, 0.9, 2.2, 95.0),
    "CNC Mill": (55.0, 2.6, 6.0, 64.0),
    "Assembly Robot": (44.0, 1.4, 6.5, 52.0),
}

_LAYOUT = [
    ("PRESS-01", "Press Line A1", "Stamping", "Stamping Press"),
    ("PRESS-02", "Press Line A2", "Stamping", "Stamping Press"),
    ("PRESS-03", "Press Line B1", "Stamping", "Stamping Press"),
    ("WELD-01", "Body Weld Cell 1", "Body Shop", "Welding Robot"),
    ("WELD-02", "Body Weld Cell 2", "Body Shop", "Welding Robot"),
    ("WELD-03", "Body Weld Cell 3", "Body Shop", "Welding Robot"),
    ("WELD-04", "Underbody Weld Cell", "Body Shop", "Welding Robot"),
    ("PAINT-01", "Primer Booth", "Paint Shop", "Paint Booth"),
    ("PAINT-02", "Top Coat Booth", "Paint Shop", "Paint Booth"),
    ("CNC-01", "Housing Mill 1", "Machining", "CNC Mill"),
    ("CNC-02", "Housing Mill 2", "Machining", "CNC Mill"),
    ("CNC-03", "Bracket Mill", "Machining", "CNC Mill"),
    ("ASSY-01", "Final Assembly Robot 1", "Final Assembly", "Assembly Robot"),
    ("ASSY-02", "Final Assembly Robot 2", "Final Assembly", "Assembly Robot"),
    ("ASSY-03", "Seat Install Robot", "Final Assembly", "Assembly Robot"),
]

FLEET: list[SimEquipment] = [
    SimEquipment(code, name, station, etype, *_TYPES[etype])
    for code, name, station, etype in _LAYOUT
]


def default_thresholds(eq: SimEquipment) -> list[dict]:
    """Operational alert limits derived from nominal values (well inside plausible ranges)."""
    return [
        {
            "metric": "temperature",
            "warning_high": round(eq.temp_mean + 15, 1),
            "critical_high": round(eq.temp_mean + 25, 1),
        },
        {
            "metric": "vibration",
            "warning_high": round(eq.vib_mean * 2.2, 2),
            "critical_high": round(eq.vib_mean * 3.5, 2),
        },
        {
            "metric": "pressure",
            "warning_low": round(eq.pressure_mean * 0.7, 2),
            "critical_low": round(eq.pressure_mean * 0.5, 2),
            "warning_high": round(eq.pressure_mean * 1.3, 2),
            "critical_high": round(eq.pressure_mean * 1.5, 2),
        },
        {
            "metric": "cycle_time",
            "warning_high": round(eq.cycle_mean * 1.4, 1),
            "critical_high": round(eq.cycle_mean * 1.8, 1),
        },
    ]
