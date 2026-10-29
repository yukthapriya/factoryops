"""SQLAlchemy ORM models. The schema itself is owned by Alembic migrations."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from factoryops.domain import (
    AlertRule,
    AlertSeverity,
    AlertStatus,
    DefectCategory,
    DefectSeverity,
    DefectStatus,
    DQCategory,
    EquipmentStatus,
    IngestOutcome,
    MachineState,
    Metric,
    RunStatus,
    sql_in,
)

TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(TS, nullable=False, server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(TS, nullable=False, server_default=func.now(), onupdate=func.now())


class Equipment(Base):
    __tablename__ = "equipment"
    __table_args__ = (
        CheckConstraint(f"status IN ({sql_in(EquipmentStatus)})", name="ck_equipment_status"),
        Index("ix_equipment_station", "station"),
        Index("ix_equipment_status", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    station: Mapped[str] = mapped_column(String(64), nullable=False)
    equipment_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_event_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    thresholds: Mapped[list["EquipmentThreshold"]] = relationship(
        back_populates="equipment",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EquipmentThreshold.metric",
    )


class EquipmentThreshold(Base):
    """Operational alert limits for one metric of one piece of equipment."""

    __tablename__ = "equipment_thresholds"
    __table_args__ = (
        UniqueConstraint("equipment_id", "metric", name="uq_threshold_equipment_metric"),
        CheckConstraint(f"metric IN ({sql_in(Metric)})", name="ck_threshold_metric"),
        CheckConstraint(
            "warning_high IS NULL OR critical_high IS NULL OR warning_high <= critical_high",
            name="ck_threshold_high_order",
        ),
        CheckConstraint(
            "warning_low IS NULL OR critical_low IS NULL OR warning_low >= critical_low",
            name="ck_threshold_low_order",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    warning_low: Mapped[float | None] = mapped_column(Float)
    warning_high: Mapped[float | None] = mapped_column(Float)
    critical_low: Mapped[float | None] = mapped_column(Float)
    critical_high: Mapped[float | None] = mapped_column(Float)

    equipment: Mapped[Equipment] = relationship(back_populates="thresholds")


class ProductionRun(Base):
    __tablename__ = "production_runs"
    __table_args__ = (
        CheckConstraint("units_produced >= 0", name="ck_run_units_nonneg"),
        CheckConstraint("defect_count >= 0", name="ck_run_defects_nonneg"),
        CheckConstraint("defect_count <= units_produced", name="ck_run_defects_le_units"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_run_time_order"),
        CheckConstraint(f"status IN ({sql_in(RunStatus)})", name="ck_run_status"),
        Index("ix_runs_equipment_started", "equipment_id", "started_at"),
        Index("ix_runs_status_started", "status", "started_at"),
        Index("ix_runs_started", "started_at"),
        # at most one running run per equipment
        Index(
            "uq_runs_one_running_per_equipment",
            "equipment_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    batch_id: Mapped[str] = mapped_column(String(48), nullable=False)
    equipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    units_produced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    defect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    equipment: Mapped[Equipment] = relationship(lazy="joined", innerjoin=True)


class TelemetryEvent(Base):
    """Accepted telemetry. event_id is the producer-assigned id and the idempotency key."""

    __tablename__ = "telemetry_events"
    __table_args__ = (
        CheckConstraint(f"machine_state IN ({sql_in(MachineState)})", name="ck_tel_state"),
        Index("ix_tel_equipment_time", "equipment_id", "event_time"),
        Index("ix_tel_time", "event_time"),
        Index("ix_tel_run", "production_run_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    equipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment.id"), nullable=False)
    production_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_runs.id"))
    event_time: Mapped[datetime] = mapped_column(TS, nullable=False)
    received_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    vibration_mm_s: Mapped[float] = mapped_column(Float, nullable=False)
    pressure_bar: Mapped[float] = mapped_column(Float, nullable=False)
    cycle_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    machine_state: Mapped[str] = mapped_column(String(16), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(48), nullable=False)
    is_late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Defect(Base):
    __tablename__ = "defects"
    __table_args__ = (
        CheckConstraint(f"category IN ({sql_in(DefectCategory)})", name="ck_defect_category"),
        CheckConstraint(f"severity IN ({sql_in(DefectSeverity)})", name="ck_defect_severity"),
        CheckConstraint(f"status IN ({sql_in(DefectStatus)})", name="ck_defect_status"),
        Index("ix_defects_detected", "detected_at"),
        Index("ix_defects_status", "status"),
        Index("ix_defects_equipment_detected", "equipment_id", "detected_at"),
        Index("ix_defects_run", "production_run_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    production_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("production_runs.id"), nullable=False
    )
    equipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment.id"), nullable=False)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("telemetry_events.event_id")
    )
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    equipment: Mapped[Equipment] = relationship(lazy="joined", innerjoin=True)
    production_run: Mapped[ProductionRun] = relationship(lazy="joined", innerjoin=True)
    notes: Mapped[list["DefectNote"]] = relationship(
        back_populates="defect", order_by="DefectNote.created_at", lazy="selectin"
    )


class DefectNote(Base):
    __tablename__ = "defect_investigation_notes"
    __table_args__ = (Index("ix_notes_defect_created", "defect_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    defect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("defects.id", ondelete="CASCADE"), nullable=False
    )
    author: Mapped[str] = mapped_column(String(80), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status_from: Mapped[str | None] = mapped_column(String(16))
    status_to: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = _created()

    defect: Mapped[Defect] = relationship(back_populates="notes")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(f"metric IN ({sql_in(Metric)}, 'machine_state')", name="ck_alert_metric"),
        CheckConstraint(f"rule IN ({sql_in(AlertRule)})", name="ck_alert_rule"),
        CheckConstraint(f"severity IN ({sql_in(AlertSeverity)})", name="ck_alert_severity"),
        CheckConstraint(f"status IN ({sql_in(AlertStatus)})", name="ck_alert_status"),
        CheckConstraint("occurrence_count >= 1", name="ck_alert_occurrences"),
        Index("ix_alerts_status_triggered", "status", "triggered_at"),
        Index("ix_alerts_equipment_triggered", "equipment_id", "triggered_at"),
        Index("ix_alerts_triggered", "triggered_at"),
        # Dedup policy enforced in the database: one unresolved alert per (equipment, metric, rule)
        Index(
            "uq_alerts_one_active_per_key",
            "equipment_id",
            "metric",
            "rule",
            unique=True,
            postgresql_where=text("status <> 'resolved'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    equipment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("equipment.id"), nullable=False)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("telemetry_events.event_id"))
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    rule: Mapped[str] = mapped_column(String(24), nullable=False)
    rule_detail: Mapped[str] = mapped_column(Text, nullable=False)
    observed_value: Mapped[float | None] = mapped_column(Float)
    threshold_value: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    triggered_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(TS)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    equipment: Mapped[Equipment] = relationship(lazy="joined", innerjoin=True)
    history: Mapped[list["AlertStatusHistory"]] = relationship(
        order_by="AlertStatusHistory.changed_at", lazy="selectin"
    )


class AlertStatusHistory(Base):
    __tablename__ = "alert_status_history"
    __table_args__ = (Index("ix_alert_history_alert", "alert_id", "changed_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class DataQualityFailure(Base):
    __tablename__ = "data_quality_failures"
    __table_args__ = (
        CheckConstraint(f"category IN ({sql_in(DQCategory)})", name="ck_dq_category"),
        Index("ix_dq_received", "received_at"),
        Index("ix_dq_category_received", "category", "received_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_id: Mapped[str | None] = mapped_column(String(64))  # raw; may not be a valid UUID
    equipment_code: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    field: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload_excerpt: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[str | None] = mapped_column(String(128))


class IngestionStat(Base):
    """Per-minute ingestion counters (deliberate denormalization for cheap dashboard reads)."""

    __tablename__ = "ingestion_stats"
    __table_args__ = (
        CheckConstraint(f"outcome IN ({sql_in(IngestOutcome)})", name="ck_ingest_outcome"),
        CheckConstraint("count >= 0", name="ck_ingest_count"),
    )

    bucket_start: Mapped[datetime] = mapped_column(TS, primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class SpcBaseline(Base):
    """Shewhart individuals-chart baseline per equipment & metric."""

    __tablename__ = "spc_baselines"
    __table_args__ = (
        UniqueConstraint("equipment_id", "metric", name="uq_spc_equipment_metric"),
        CheckConstraint("sigma > 0", name="ck_spc_sigma_pos"),
        CheckConstraint("sample_size >= 2", name="ck_spc_sample"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    mean: Mapped[float] = mapped_column(Float, nullable=False)
    sigma: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime] = mapped_column(TS, nullable=False)
    window_end: Mapped[datetime] = mapped_column(TS, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


# ---------------------------------------------------------------- historical benchmark data
# Kept in a separate Postgres schema so historical benchmark rows can never be confused with
# (or joined into) live Kafka telemetry.

HIST = "historical"


class DatasetImport(Base):
    """One successfully validated import of a historical dataset file, with provenance."""

    __tablename__ = "dataset_imports"
    __table_args__ = (
        UniqueConstraint("dataset", name="uq_dataset_imports_dataset"),  # one current version
        CheckConstraint("row_count >= 0", name="ck_dataset_imports_rows"),
        {"schema": HIST},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    doi: Mapped[str | None] = mapped_column(String(64))
    license: Mapped[str] = mapped_column(Text, nullable=False)
    citation: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observations: Mapped[dict] = mapped_column(JSONB, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class Ai4iRecord(Base):
    """One row of the UCI AI4I 2020 dataset (synthetic milling process; units per source)."""

    __tablename__ = "ai4i_records"
    __table_args__ = (
        UniqueConstraint("import_id", "product_id", name="uq_ai4i_product"),
        CheckConstraint("product_type IN ('L', 'M', 'H')", name="ck_ai4i_type"),
        CheckConstraint("air_temp_k > 0 AND process_temp_k > 0", name="ck_ai4i_temps"),
        CheckConstraint("rotational_speed_rpm > 0", name="ck_ai4i_rpm"),
        CheckConstraint("torque_nm >= 0 AND tool_wear_min >= 0", name="ck_ai4i_nonneg"),
        Index("ix_ai4i_failure", "import_id", "machine_failure"),
        {"schema": HIST},
    )

    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{HIST}.dataset_imports.id", ondelete="CASCADE"), primary_key=True
    )
    udi: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[str] = mapped_column(String(16), nullable=False)
    product_type: Mapped[str] = mapped_column(String(1), nullable=False)
    air_temp_k: Mapped[float] = mapped_column(Float, nullable=False)
    process_temp_k: Mapped[float] = mapped_column(Float, nullable=False)
    rotational_speed_rpm: Mapped[int] = mapped_column(Integer, nullable=False)
    torque_nm: Mapped[float] = mapped_column(Float, nullable=False)
    tool_wear_min: Mapped[int] = mapped_column(Integer, nullable=False)
    machine_failure: Mapped[bool] = mapped_column(Boolean, nullable=False)
    twf: Mapped[bool] = mapped_column(Boolean, nullable=False)
    hdf: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pwf: Mapped[bool] = mapped_column(Boolean, nullable=False)
    osf: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rnf: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Derived quantities named in the dataset documentation (computed by Postgres).
    temp_diff_k: Mapped[float] = mapped_column(
        Float, Computed("process_temp_k - air_temp_k", persisted=True)
    )
    power_w: Mapped[float] = mapped_column(
        Float, Computed("torque_nm * rotational_speed_rpm * 2 * pi() / 60", persisted=True)
    )
    strain_min_nm: Mapped[float] = mapped_column(
        Float, Computed("tool_wear_min * torque_nm", persisted=True)
    )


class ModelEvaluation(Base):
    """Offline evaluation of a failure classifier on one dataset import (reproducible by seed)."""

    __tablename__ = "model_evaluations"
    __table_args__ = (
        UniqueConstraint("import_id", "model_name", name="uq_model_eval_import_model"),
        {"schema": HIST},
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    import_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{HIST}.dataset_imports.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(48), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pr_curve: Mapped[list] = mapped_column(JSONB, nullable=False)
    feature_importance: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
