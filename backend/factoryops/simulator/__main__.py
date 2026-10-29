"""Telemetry producer.

    python -m factoryops.simulator --scenario mixed --ticks 120 --interval 1 --seed 42
    python -m factoryops.simulator --transport direct --scenario duplicates --ticks 20

Transports:
  kafka  - publish to FACTORYOPS_KAFKA_TOPIC (key = equipment code, acks=all, idempotent producer)
  direct - run the same processing pipeline in-process against PostgreSQL (no Kafka required)
"""

import argparse
import json
import logging
import time
from collections import Counter
from datetime import UTC, datetime

from factoryops.config import get_settings
from factoryops.logging_setup import configure_logging
from factoryops.simulator.generator import SCENARIOS, SimMessage, TelemetryGenerator

log = logging.getLogger("factoryops.simulator")


class KafkaSink:
    def __init__(self) -> None:
        from confluent_kafka import Producer

        s = get_settings()
        self.topic = s.kafka_topic
        self.stats: Counter[str] = Counter()
        self.producer = Producer(
            {
                "bootstrap.servers": s.kafka_bootstrap_servers,
                "enable.idempotence": True,
                "acks": "all",
                "linger.ms": 20,
                "message.timeout.ms": 30000,
                "client.id": "factoryops-simulator",
            }
        )

    def _on_delivery(self, err, msg) -> None:
        if err is not None:
            self.stats["delivery_failed"] += 1
            log.error("delivery failed", extra={"error": str(err), "key": msg.key()})
        else:
            self.stats["delivered"] += 1

    def send(self, m: SimMessage) -> None:
        while True:
            try:
                self.producer.produce(
                    self.topic, key=m.key, value=m.value, on_delivery=self._on_delivery
                )
                break
            except BufferError:
                self.producer.poll(0.5)  # local queue full: wait for deliveries
        self.producer.poll(0)

    def close(self) -> dict:
        remaining = self.producer.flush(35)
        if remaining:
            self.stats["undelivered_at_exit"] += remaining
        return dict(self.stats)


class DirectSink:
    def __init__(self) -> None:
        from factoryops.db import get_sessionmaker

        self.factory = get_sessionmaker()
        self.stats: Counter[str] = Counter()

    def send(self, m: SimMessage) -> None:
        from factoryops.pipeline.processor import process_with_retry

        result = process_with_retry(self.factory, m.value, source="simulator:direct")
        self.stats[result.outcome.value] += 1

    def close(self) -> dict:
        return dict(self.stats)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="FactoryOps synthetic telemetry producer")
    p.add_argument("--scenario", choices=SCENARIOS, default="mixed")
    p.add_argument(
        "--ticks", type=int, default=60, help="number of ticks (each = 1 event per unit)"
    )
    p.add_argument("--interval", type=float, default=2.0, help="seconds between ticks")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--transport", choices=["kafka", "direct"], default="kafka")
    p.add_argument("--forever", action="store_true", help="loop until interrupted")
    p.add_argument("--targets", nargs="*", help="equipment codes affected by the scenario")
    args = p.parse_args(argv)

    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    sink = KafkaSink() if args.transport == "kafka" else DirectSink()
    gen = TelemetryGenerator(
        seed=args.seed,
        scenario=args.scenario,
        start=datetime.now(UTC),
        interval_s=args.interval,
        target_codes=args.targets,
    )
    log.info(
        "simulator started",
        extra={
            "scenario": args.scenario,
            "transport": args.transport,
            "targets": sorted(gen.targets),
        },
    )
    sent = 0
    tick = 0
    try:
        while args.forever or tick < args.ticks:
            started = time.monotonic()
            for m in gen.tick(tick, now=datetime.now(UTC)):
                sink.send(m)
                sent += 1
                if m.note:
                    log.info("injected", extra={"key": m.key, "note": m.note})
            tick += 1
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        stats = sink.close()
        print(json.dumps({"events_sent": sent, "ticks": tick, "outcomes": stats}))


if __name__ == "__main__":
    main()
