"""Process generated messages in-process with simulated arrival times (used by seed + demo API)."""

from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from factoryops.pipeline.processor import process_with_retry
from factoryops.simulator.generator import SimMessage

ARRIVAL_DELAY = timedelta(milliseconds=500)


def replay(session_factory: Callable[[], Session], messages: Iterable[SimMessage]) -> Counter:
    """Arrival clock = latest event_time seen + 0.5s, so delayed (held-back) messages arrive
    after newer ones exactly as they would from a live producer."""
    outcomes: Counter[str] = Counter()
    clock: datetime | None = None
    for m in messages:
        clock = max(clock, m.event_time) if clock else m.event_time
        arrival = clock + ARRIVAL_DELAY
        result = process_with_retry(
            session_factory, m.value, source="simulator:direct", now_fn=lambda a=arrival: a
        )
        outcomes[result.outcome.value] += 1
        outcomes["alerts_opened"] += len(result.alerts_opened)
        if result.defect_id:
            outcomes["defects_created"] += 1
    return outcomes
