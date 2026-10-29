"""Pure alert-rule evaluation. Produces candidate alerts; persistence/dedup is elsewhere."""

from dataclasses import dataclass

from factoryops.domain import METRIC_UNITS, AlertRule, AlertSeverity, MachineState, Metric
from factoryops.pipeline.validation import ValidEvent

SPC_SIGMA_LIMIT = 3.0


@dataclass(frozen=True)
class ThresholdSpec:
    metric: Metric
    warning_low: float | None = None
    warning_high: float | None = None
    critical_low: float | None = None
    critical_high: float | None = None


@dataclass(frozen=True)
class Baseline:
    metric: Metric
    mean: float
    sigma: float
    sample_size: int


@dataclass(frozen=True)
class AlertCandidate:
    metric: str
    rule: AlertRule
    severity: AlertSeverity
    observed_value: float | None
    threshold_value: float | None
    detail: str


def evaluate_threshold(metric: Metric, value: float, spec: ThresholdSpec) -> AlertCandidate | None:
    """Critical limits are checked before warning limits. A value exactly equal to a limit is
    treated as within limits (alerts fire on strict exceedance)."""
    unit = METRIC_UNITS[metric]
    checks = [
        (
            spec.critical_high,
            AlertSeverity.CRITICAL,
            AlertRule.THRESHOLD_HIGH,
            lambda v, t: v > t,
            ">",
        ),
        (
            spec.critical_low,
            AlertSeverity.CRITICAL,
            AlertRule.THRESHOLD_LOW,
            lambda v, t: v < t,
            "<",
        ),
        (
            spec.warning_high,
            AlertSeverity.WARNING,
            AlertRule.THRESHOLD_HIGH,
            lambda v, t: v > t,
            ">",
        ),
        (spec.warning_low, AlertSeverity.WARNING, AlertRule.THRESHOLD_LOW, lambda v, t: v < t, "<"),
    ]
    for limit, severity, rule, breached, op in checks:
        if limit is not None and breached(value, limit):
            return AlertCandidate(
                metric=metric.value,
                rule=rule,
                severity=severity,
                observed_value=value,
                threshold_value=limit,
                detail=f"{metric.value} {value:.2f} {unit} {op} {severity.value} limit "
                f"{limit:.2f} {unit}",
            )
    return None


def evaluate_spc(metric: Metric, value: float, baseline: Baseline) -> AlertCandidate | None:
    """Shewhart individuals chart, Western Electric rule 1: a single point beyond mean ± 3σ."""
    ucl = baseline.mean + SPC_SIGMA_LIMIT * baseline.sigma
    lcl = baseline.mean - SPC_SIGMA_LIMIT * baseline.sigma
    if lcl <= value <= ucl:
        return None
    unit = METRIC_UNITS[metric]
    limit = ucl if value > ucl else lcl
    return AlertCandidate(
        metric=metric.value,
        rule=AlertRule.SPC_3SIGMA,
        severity=AlertSeverity.WARNING,
        observed_value=value,
        threshold_value=limit,
        detail=f"{metric.value} {value:.2f} {unit} outside control limits "
        f"[{lcl:.2f}, {ucl:.2f}] (mean {baseline.mean:.2f} ± 3σ, σ={baseline.sigma:.3f}, "
        f"baseline n={baseline.sample_size})",
    )


def evaluate_event(
    event: ValidEvent,
    thresholds: dict[Metric, ThresholdSpec],
    baselines: dict[Metric, Baseline],
) -> list[AlertCandidate]:
    candidates: list[AlertCandidate] = []
    if event.machine_state == MachineState.FAULT:
        candidates.append(
            AlertCandidate(
                metric="machine_state",
                rule=AlertRule.MACHINE_FAULT,
                severity=AlertSeverity.CRITICAL,
                observed_value=None,
                threshold_value=None,
                detail="Equipment reported machine_state=fault",
            )
        )
    # Idle/stopped machines produce sensor readings that are not comparable with the running
    # baseline, so only threshold rules apply to them; SPC only applies while running.
    for metric in Metric:
        value = event.metric_value(metric)
        spec = thresholds.get(metric)
        hit = evaluate_threshold(metric, value, spec) if spec else None
        if hit is None and event.machine_state == MachineState.RUNNING and metric in baselines:
            hit = evaluate_spc(metric, value, baselines[metric])
        if hit:
            candidates.append(hit)
    return candidates
