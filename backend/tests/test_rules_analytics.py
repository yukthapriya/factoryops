import random
from datetime import timedelta

import pytest

from factoryops.analytics import (
    classify_points,
    defect_rate_pct,
    estimate_individuals_baseline,
    yield_pct,
)
from factoryops.domain import AlertRule, AlertSeverity, Metric
from factoryops.pipeline.rules import (
    Baseline,
    ThresholdSpec,
    evaluate_event,
    evaluate_spc,
    evaluate_threshold,
)
from factoryops.pipeline.validation import ValidationPolicy, validate_event
from tests.conftest import NOW, make_event

TEMP = ThresholdSpec(Metric.TEMPERATURE, warning_high=80, critical_high=90)
PRESSURE = ThresholdSpec(Metric.PRESSURE, warning_low=4, critical_low=3, warning_high=9)


@pytest.mark.parametrize(
    "value, expected",
    [
        (79.9, None),
        (80.0, None),  # equal to limit -> within limits
        (80.01, (AlertSeverity.WARNING, AlertRule.THRESHOLD_HIGH, 80)),
        (90.0, (AlertSeverity.WARNING, AlertRule.THRESHOLD_HIGH, 80)),
        (90.5, (AlertSeverity.CRITICAL, AlertRule.THRESHOLD_HIGH, 90)),
    ],
)
def test_high_thresholds(value, expected):
    hit = evaluate_threshold(Metric.TEMPERATURE, value, TEMP)
    if expected is None:
        assert hit is None
    else:
        assert (hit.severity, hit.rule, hit.threshold_value) == expected
        assert "limit" in hit.detail


def test_low_thresholds():
    assert evaluate_threshold(Metric.PRESSURE, 4.0, PRESSURE) is None
    warn = evaluate_threshold(Metric.PRESSURE, 3.5, PRESSURE)
    assert (warn.severity, warn.rule) == (AlertSeverity.WARNING, AlertRule.THRESHOLD_LOW)
    crit = evaluate_threshold(Metric.PRESSURE, 2.0, PRESSURE)
    assert crit.severity == AlertSeverity.CRITICAL


def test_spc_rule():
    bl = Baseline(Metric.VIBRATION, mean=2.0, sigma=0.1, sample_size=100)
    assert evaluate_spc(Metric.VIBRATION, 2.29, bl) is None
    hit = evaluate_spc(Metric.VIBRATION, 2.31, bl)
    assert hit.rule == AlertRule.SPC_3SIGMA
    assert hit.threshold_value == pytest.approx(2.3)
    assert evaluate_spc(Metric.VIBRATION, 1.69, bl).threshold_value == pytest.approx(1.7)


def _event(**kw):
    policy = ValidationPolicy(timedelta(seconds=120), timedelta(days=7), timedelta(minutes=5))
    return validate_event(make_event(**kw), NOW, policy)


def test_evaluate_event_threshold_takes_precedence_over_spc():
    thresholds = {Metric.TEMPERATURE: TEMP}
    baselines = {Metric.TEMPERATURE: Baseline(Metric.TEMPERATURE, 60, 1, 50)}
    hits = evaluate_event(_event(temperature=95), thresholds, baselines)
    assert [h.rule for h in hits] == [AlertRule.THRESHOLD_HIGH]
    hits = evaluate_event(_event(temperature=70), thresholds, baselines)
    assert [h.rule for h in hits] == [AlertRule.SPC_3SIGMA]


def test_spc_ignored_when_not_running_and_fault_alert():
    baselines = {Metric.TEMPERATURE: Baseline(Metric.TEMPERATURE, 60, 1, 50)}
    assert evaluate_event(_event(temperature=70, state="idle"), {}, baselines) == []
    hits = evaluate_event(_event(state="fault"), {}, {})
    assert hits[0].rule == AlertRule.MACHINE_FAULT
    assert hits[0].severity == AlertSeverity.CRITICAL


def test_yield_and_defect_rate():
    assert yield_pct(200, 3) == 98.5
    assert defect_rate_pct(200, 3) == 1.5
    assert yield_pct(0, 0) is None
    assert defect_rate_pct(0, 0) is None
    assert yield_pct(10, 10) == 0.0
    with pytest.raises(ValueError):
        yield_pct(5, 6)
    with pytest.raises(ValueError):
        defect_rate_pct(-1, 0)


def test_individuals_baseline_estimates_sigma_from_moving_range():
    rng = random.Random(1)
    values = [rng.gauss(50, 2) for _ in range(500)]
    bl = estimate_individuals_baseline(values)
    assert bl.mean == pytest.approx(50, abs=0.3)
    assert bl.sigma == pytest.approx(2, rel=0.12)
    assert bl.ucl == pytest.approx(bl.mean + 3 * bl.sigma)


def test_baseline_requires_enough_varying_data():
    assert estimate_individuals_baseline([1.0] * 19) is None
    assert estimate_individuals_baseline([1.0] * 50) is None  # zero variation


def test_western_electric_rules():
    bl = estimate_individuals_baseline([10.0, 11.0] * 15)  # mean 10.5, sigma ~0.887
    flags = classify_points([10.6, 14.0, 10.4, 6.0], bl)
    assert flags == [None, "rule1_beyond_3sigma", None, "rule1_beyond_3sigma"]
    run = [10.7] * 9
    flags = classify_points(run, bl)
    assert flags[:7] == [None] * 7
    assert flags[7:] == ["rule2_run_of_8", "rule2_run_of_8"]
