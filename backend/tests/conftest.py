"""Test fixtures. Tests use an isolated database whose name must end in `_test`; the schema is
rebuilt from Alembic migrations once per session and all tables are truncated between tests."""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

TEST_DB_URL = os.environ.get(
    "FACTORYOPS_TEST_DATABASE_URL", "postgresql+psycopg://localhost:5432/factoryops_test"
)
os.environ["FACTORYOPS_DATABASE_URL"] = TEST_DB_URL
os.environ["FACTORYOPS_LOG_JSON"] = "false"
os.environ["FACTORYOPS_LOG_LEVEL"] = "WARNING"
os.environ["FACTORYOPS_DEMO_CONTROLS_ENABLED"] = "true"

from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from alembic import command  # noqa: E402
from factoryops.config import get_settings  # noqa: E402
from factoryops.db import get_engine, get_sessionmaker  # noqa: E402
from factoryops.models import Base  # noqa: E402
from factoryops.schemas import EquipmentCreate  # noqa: E402
from factoryops.services.equipment import create_equipment  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def _database():
    url = make_url(TEST_DB_URL)
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(f"refusing to run tests against non-test database {url.database!r}")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    engine = create_engine(TEST_DB_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS historical CASCADE"))
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    command.upgrade(cfg, "head")
    yield
    get_engine().dispose()


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    tables = ", ".join(
        f'"{t.schema}"."{t.name}"' if t.schema else f'"{t.name}"'
        for t in Base.metadata.sorted_tables
    )
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def db():
    session = get_sessionmaker()()
    yield session
    session.close()


@pytest.fixture
def session_factory():
    return get_sessionmaker()


@pytest.fixture
def client():
    from factoryops.main import create_app

    with TestClient(create_app(start_listener=False)) as c:
        yield c


THRESHOLDS = [
    {"metric": "temperature", "warning_high": 80.0, "critical_high": 90.0},
    {"metric": "vibration", "warning_high": 5.0, "critical_high": 8.0},
    {"metric": "pressure", "warning_low": 4.0, "critical_low": 3.0, "warning_high": 9.0},
]


@pytest.fixture
def equipment(db):
    return create_equipment(
        db,
        EquipmentCreate(
            code="TEST-01",
            name="Test Press",
            station="Stamping",
            equipment_type="Stamping Press",
            thresholds=THRESHOLDS,
        ),
    )


NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def make_event(
    *,
    code: str = "TEST-01",
    event_time: datetime | str | None = None,
    event_id: str | None = None,
    state: str = "running",
    batch: str = "B1",
    temperature: float = 60.0,
    vibration: float = 2.0,
    pressure: float = 6.0,
    cycle: float = 30.0,
    quality_check: dict | None = None,
    drop: tuple[str, ...] = (),
    **overrides,
) -> dict:
    event = {
        "schema_version": 1,
        "event_id": event_id or str(uuid.uuid4()),
        "equipment_id": code,
        "event_time": event_time
        if isinstance(event_time, str)
        else (event_time or NOW - timedelta(seconds=1)).isoformat(),
        "machine_state": state,
        "batch_id": batch,
        "metrics": {
            "temperature_c": temperature,
            "vibration_mm_s": vibration,
            "pressure_bar": pressure,
            "cycle_time_s": cycle,
        },
        "quality_check": quality_check,
    }
    event.update(overrides)
    for field in drop:
        if field.startswith("metrics."):
            event["metrics"].pop(field.split(".", 1)[1])
        else:
            event.pop(field)
    return event


def as_bytes(event: dict) -> bytes:
    return json.dumps(event).encode()
