"""Live-update messages published through Postgres NOTIFY.

NOTIFY is transactional: messages are delivered to listeners only if the surrounding
transaction commits, so the UI never sees an update that was rolled back.
"""

import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from factoryops.config import get_settings

MAX_PAYLOAD_BYTES = 7900  # Postgres limit is 8000 bytes


def envelope(msg_type: str, data: dict) -> dict:
    return {"type": msg_type, "ts": datetime.now(UTC).isoformat(), "data": data}


def publish(session: Session, msg_type: str, data: dict) -> None:
    payload = json.dumps(envelope(msg_type, data), default=str)
    if len(payload.encode()) > MAX_PAYLOAD_BYTES:
        payload = json.dumps(envelope(msg_type, {"truncated": True}))
    session.execute(select(func.pg_notify(get_settings().notify_channel, payload)))
