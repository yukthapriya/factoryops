"""Operational commands.

python -m factoryops.cli seed [--reset] [--hours 6] [--seed 42]
python -m factoryops.cli compute-baselines [--hours 6]
python -m factoryops.cli replay-dlq [--max 100]
python -m factoryops.cli register-fleet
python -m factoryops.cli ai4i-download [--dest data]
python -m factoryops.cli ai4i-import --file data/ai4i2020.csv [--replace]
python -m factoryops.cli ai4i-evaluate [--seed 42] [--test-size 0.25]
python -m factoryops.cli ai4i-setup        # download to a temp dir + import + evaluate
"""

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text

from factoryops.config import get_settings
from factoryops.db import get_sessionmaker
from factoryops.historical import ai4i
from factoryops.logging_setup import configure_logging
from factoryops.models import Alert, Base, Defect, Equipment, EquipmentThreshold
from factoryops.schemas import AlertAction, DefectNoteCreate, DefectUpdate
from factoryops.services import alerts as alert_svc
from factoryops.services import defects as defect_svc
from factoryops.services.insights import compute_baselines
from factoryops.simulator.direct import replay
from factoryops.simulator.fleet import FLEET, default_thresholds
from factoryops.simulator.generator import TelemetryGenerator

# Scenario plan for the historical backfill after the normal baseline phase:
# (scenario, share of remaining ticks, targets)
SEED_PLAN = [
    ("mixed", 0.35, ["PRESS-02", "CNC-01"]),
    ("temperature_drift", 0.10, ["WELD-02"]),
    ("vibration_spike", 0.15, ["PRESS-03"]),
    ("defects", 0.20, ["PAINT-02", "ASSY-01"]),
    ("normal", 0.12, None),
    ("shutdown", 0.08, ["CNC-03"]),
]


def reset_database(session) -> None:
    # Live/demo tables only: historical benchmark data (schema "historical") is never reset here.
    tables = ", ".join(
        f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables) if t.schema is None
    )
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    session.commit()


def upsert_fleet(session) -> int:
    created = 0
    for sim in FLEET:
        if session.scalar(select(Equipment.id).where(Equipment.code == sim.code)):
            continue
        session.add(
            Equipment(
                code=sim.code,
                name=sim.name,
                station=sim.station,
                equipment_type=sim.equipment_type,
                status="idle",
                is_active=True,
                thresholds=[EquipmentThreshold(**t) for t in default_thresholds(sim)],
            )
        )
        created += 1
    session.commit()
    return created


def seed(hours: int, seed_value: int, reset: bool) -> dict:
    factory = get_sessionmaker()
    summary: dict = {}
    with factory() as session:
        if reset:
            reset_database(session)
        summary["equipment_created"] = upsert_fleet(session)

    interval = 60.0
    total_ticks = hours * 60
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=hours)
    baseline_ticks = int(total_ticks * 0.4)
    outcomes: Counter = Counter()

    gen = TelemetryGenerator(seed_value, "normal", start=start, interval_s=interval)
    outcomes.update(replay(factory, gen.run(baseline_ticks)))
    baseline_end = start + timedelta(seconds=baseline_ticks * interval)
    with factory() as session:
        summary["baselines"] = compute_baselines(session, start, baseline_end)

    cursor = baseline_end
    remaining = total_ticks - baseline_ticks
    for i, (scenario, share, targets) in enumerate(SEED_PLAN):
        ticks = max(1, int(remaining * share))
        gen = TelemetryGenerator(
            seed_value + i + 1, scenario, start=cursor, interval_s=interval, target_codes=targets
        )
        outcomes.update(replay(factory, gen.run(ticks)))
        cursor += timedelta(seconds=ticks * interval)
    summary["ingestion"] = dict(outcomes)

    # A little investigation history so the workflow screens have context.
    with factory() as session:
        alerts = session.scalars(select(Alert).order_by(Alert.triggered_at).limit(6)).all()
        for idx, alert in enumerate(alerts):
            action = AlertAction(actor="seed: shift lead", note="Reviewed during shift handover")
            if idx % 3 == 0:
                alert_svc.transition(session, alert.id, alert_svc.AlertStatus.RESOLVED, action)
            elif idx % 3 == 1:
                alert_svc.transition(session, alert.id, alert_svc.AlertStatus.ACKNOWLEDGED, action)
        defects = session.scalars(select(Defect).order_by(Defect.detected_at).limit(4)).all()
        for idx, d in enumerate(defects):
            defect_svc.add_note(
                session,
                d.id,
                DefectNoteCreate(
                    author="seed: quality engineer",
                    body="Part quarantined; reviewing telemetry around detection time.",
                ),
            )
            if idx % 2 == 0:
                defect_svc.update_defect(
                    session,
                    d.id,
                    DefectUpdate(status="investigating", author="seed: quality engineer"),
                )
    return summary


def replay_dlq(max_messages: int) -> dict:
    """Move messages from the DLQ back to the main topic (after fixing the root cause)."""
    from confluent_kafka import Consumer, Producer

    s = get_settings()
    consumer = Consumer(
        {
            "bootstrap.servers": s.kafka_bootstrap_servers,
            "group.id": f"{s.kafka_group_id}-dlq-replay",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    producer = Producer({"bootstrap.servers": s.kafka_bootstrap_servers, "acks": "all"})
    consumer.subscribe([s.kafka_dlq_topic])
    moved = 0
    idle = 0
    try:
        while moved < max_messages and idle < 5:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                idle += 1
                continue
            idle = 0
            producer.produce(s.kafka_topic, key=msg.key(), value=msg.value())
            producer.flush(10)
            consumer.commit(message=msg, asynchronous=False)
            moved += 1
    finally:
        consumer.close()
    return {"replayed": moved}


def _summary(results: list[dict]) -> list[dict]:
    keys = ("precision", "recall", "f1", "pr_auc", "confusion_matrix")
    return [{"model": r["model_name"], **{k: r["metrics"][k] for k in keys}} for r in results]


def ai4i_command(args) -> dict:
    from factoryops.historical.model import evaluate_and_store

    factory = get_sessionmaker()
    if args.cmd == "ai4i-download":
        path = ai4i.download(Path(args.dest), args.allow_checksum_mismatch)
        return {"downloaded": str(path), "sha256": ai4i.sha256_bytes(path.read_bytes())}
    if args.cmd == "ai4i-import":
        path = Path(args.file)
        with factory() as session:
            return ai4i.import_ai4i(session, path.read_bytes(), path.name, replace=args.replace)
    if args.cmd == "ai4i-evaluate":
        with factory() as session:
            return {"models": _summary(evaluate_and_store(session, args.seed, args.test_size))}
    with tempfile.TemporaryDirectory() as tmp:
        path = ai4i.download(Path(tmp))
        with factory() as session:
            imported = ai4i.import_ai4i(session, path.read_bytes(), path.name)
    with factory() as session:
        models = _summary(evaluate_and_store(session, args.seed))
    return {"import": imported, "models": models}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factoryops.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed", help="create fleet and backfill synthetic history")
    p_seed.add_argument("--hours", type=int, default=6)
    p_seed.add_argument("--seed", type=int, default=42)
    p_seed.add_argument("--reset", action="store_true", help="TRUNCATE all tables first")
    sub.add_parser(
        "register-fleet", help="idempotently register the simulated equipment fleet (no history)"
    )
    p_dl = sub.add_parser("ai4i-download", help="download the UCI AI4I 2020 CSV and verify it")
    p_dl.add_argument("--dest", default="data", help="directory for ai4i2020.csv")
    p_dl.add_argument("--allow-checksum-mismatch", action="store_true")
    p_imp = sub.add_parser("ai4i-import", help="validate and import the AI4I 2020 CSV")
    p_imp.add_argument("--file", required=True)
    p_imp.add_argument("--replace", action="store_true", help="replace a different imported file")
    p_ev = sub.add_parser("ai4i-evaluate", help="train/evaluate failure classifiers")
    p_ev.add_argument("--seed", type=int, default=42)
    p_ev.add_argument("--test-size", type=float, default=0.25)
    p_all = sub.add_parser("ai4i-setup", help="download (to a temp dir), import and evaluate")
    p_all.add_argument("--seed", type=int, default=42)
    p_bl = sub.add_parser("compute-baselines", help="recompute SPC baselines")
    p_bl.add_argument("--hours", type=int, default=6)
    p_dlq = sub.add_parser("replay-dlq", help="re-publish DLQ messages to the main topic")
    p_dlq.add_argument("--max", type=int, default=100)
    args = parser.parse_args(argv)

    s = get_settings()
    configure_logging("WARNING", s.log_json)
    if args.cmd == "seed":
        result = seed(args.hours, args.seed, args.reset)
    elif args.cmd.startswith("ai4i-"):
        try:
            result = ai4i_command(args)
        except (ai4i.ImportValidationError, ai4i.ImportConflict, LookupError, ValueError) as exc:
            errors = getattr(exc, "errors", None)
            print(json.dumps({"status": "failed", "error": str(exc), "errors": errors}, indent=2))
            return 1
    elif args.cmd == "register-fleet":
        with get_sessionmaker()() as session:
            created = upsert_fleet(session)
        result = {"equipment_created": created, "fleet_size": len(FLEET)}
    elif args.cmd == "compute-baselines":
        end = datetime.now(UTC)
        with get_sessionmaker()() as session:
            result = {
                "baselines": compute_baselines(session, end - timedelta(hours=args.hours), end)
            }
    else:
        result = replay_dlq(args.max)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
