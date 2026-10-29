"""Kafka telemetry consumer.

Delivery semantics: at-least-once from Kafka + idempotent writes (event_id primary key)
=> each event is stored at most once.

* enable.auto.commit=false; an offset is committed only after the message's DB transaction
  committed (or the message was recorded as a data-quality failure / sent to the DLQ).
* Invalid messages are never retried: they are recorded once and committed.
* Transient DB errors are retried with bounded exponential backoff. If retries are exhausted
  the consumer exits non-zero WITHOUT committing, so the message is redelivered after restart
  (Docker restart policy). This avoids losing data during a database outage.
* Unexpected (non-transient) processing errors send the raw message to the DLQ topic with
  error headers, record a `processing_error` data-quality row, and commit.
"""

import logging
import signal
import sys
from collections import Counter

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer

from factoryops.config import get_settings
from factoryops.db import get_sessionmaker
from factoryops.domain import IngestOutcome
from factoryops.logging_setup import configure_logging
from factoryops.pipeline.processor import (
    TransientProcessingError,
    process_with_retry,
    record_processing_error,
)

log = logging.getLogger("factoryops.consumer")


PROGRESS_EVERY = 500
HIGH_REJECTION_RATE = 0.5


def progress_report(window: Counter, processed: int) -> tuple[int, str, dict]:
    """Summarize the last PROGRESS_EVERY messages. Escalates to WARNING when most of the window
    was rejected so a systemic cause (e.g. unregistered equipment) is visible, not just counted."""
    handled = sum(window[o] for o in ("accepted", "accepted_late", "rejected", "duplicate"))
    rejected = window["rejected"]
    rate = rejected / handled if handled else 0.0
    reasons = {
        k.split(":", 1)[1]: v
        for k, v in sorted(window.items(), key=lambda kv: -kv[1])
        if k.startswith("rejected:")
    }
    extra = {
        "processed": processed,
        "window": handled,
        "accepted": window["accepted"] + window["accepted_late"],
        "rejected": rejected,
        "duplicate": window["duplicate"],
        "rejection_rate": round(rate, 3),
        "rejected_by_category": reasons,
    }
    if rate > HIGH_REJECTION_RATE:
        top = ", ".join(f"{k}={v}" for k, v in list(reasons.items())[:3])
        msg = f"high rejection rate {rate:.0%} in last {handled} messages ({top})"
        if "unknown_equipment" in reasons:
            msg += (
                "; is the equipment fleet registered? run `python -m factoryops.cli register-fleet`"
            )
        return logging.WARNING, msg, extra
    return logging.INFO, "consumer progress", extra


def handle_message(msg, session_factory, dlq_producer, dlq_topic: str, counters: Counter) -> None:
    """Process one Kafka message. Raises TransientProcessingError if it must be redelivered."""
    source = f"{msg.topic()}[{msg.partition()}]@{msg.offset()}"
    try:
        result = process_with_retry(session_factory, msg.value(), source=source)
        counters[result.outcome.value] += 1
        if result.outcome == IngestOutcome.REJECTED and result.category is not None:
            counters[f"rejected:{result.category.value}"] += 1
        if result.alerts_opened:
            counters["alerts_opened"] += len(result.alerts_opened)
        if result.alerts_suppressed:
            counters["alerts_suppressed"] += result.alerts_suppressed
    except TransientProcessingError:
        raise
    except Exception as exc:  # non-transient: dead-letter it
        counters["dead_lettered"] += 1
        log.exception("processing failed; sending to DLQ", extra={"source": source})
        dlq_producer.produce(
            dlq_topic,
            key=msg.key(),
            value=msg.value(),
            headers={
                "x-error": str(exc)[:500],
                "x-source": source,
            },
        )
        dlq_producer.flush(10)
        session = session_factory()
        try:
            record_processing_error(session, msg.value() or b"", str(exc), source)
        finally:
            session.close()


def main() -> int:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    session_factory = get_sessionmaker()
    consumer = Consumer(
        {
            "bootstrap.servers": s.kafka_bootstrap_servers,
            "group.id": s.kafka_group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "session.timeout.ms": 30000,
        }
    )
    dlq = Producer({"bootstrap.servers": s.kafka_bootstrap_servers, "acks": "all"})
    running = True

    def _stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    consumer.subscribe([s.kafka_topic])
    counters: Counter[str] = Counter()
    log.info("consumer started", extra={"topic": s.kafka_topic, "group": s.kafka_group_id})
    processed = 0
    last_report: Counter[str] = Counter()
    exit_code = 0
    try:
        while running:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                # Broker/network errors are retried internally by librdkafka; log and continue.
                log.warning("kafka error", extra={"error": str(msg.error())})
                continue
            try:
                handle_message(msg, session_factory, dlq, s.kafka_dlq_topic, counters)
            except TransientProcessingError:
                log.error("database unavailable after retries; exiting without commit")
                exit_code = 2
                break
            consumer.commit(message=msg, asynchronous=False)
            processed += 1
            if processed % PROGRESS_EVERY == 0:
                level, message, extra = progress_report(counters - last_report, processed)
                log.log(level, message, extra=extra)
                last_report = counters.copy()
    except KafkaException:
        log.exception("fatal kafka error")
        exit_code = 3
    finally:
        consumer.close()
        log.info("consumer stopped", extra={"processed": processed, "totals": dict(counters)})
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
