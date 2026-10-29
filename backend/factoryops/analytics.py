"""Pure statistical / production calculations (no I/O)."""

from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean

# d2 constant for moving ranges of span 2 (Montgomery, Introduction to SQC).
D2_N2 = 1.128
MIN_BASELINE_POINTS = 20
RUN_RULE_LENGTH = 8


def yield_pct(units: int, defects: int) -> float | None:
    """First-pass yield as a percentage. None when nothing has been produced."""
    if units < 0 or defects < 0 or defects > units:
        raise ValueError("require 0 <= defects <= units")
    if units == 0:
        return None
    return round((units - defects) / units * 100, 2)


def defect_rate_pct(units: int, defects: int) -> float | None:
    if units < 0 or defects < 0:
        raise ValueError("counts must be nonnegative")
    if units == 0:
        return None
    return round(defects / units * 100, 2)


@dataclass(frozen=True)
class IndividualsBaseline:
    mean: float
    sigma: float
    sample_size: int

    @property
    def ucl(self) -> float:
        return self.mean + 3 * self.sigma

    @property
    def lcl(self) -> float:
        return self.mean - 3 * self.sigma


def estimate_individuals_baseline(values: list[float]) -> IndividualsBaseline | None:
    """Phase-I estimate for an individuals (I-MR) chart.

    sigma is estimated from the average moving range (MR̄ / d2), which is robust to slow drift
    compared with the sample standard deviation. Returns None for fewer than 20 points or when
    the data shows no variation (sigma would be zero).
    """
    if len(values) < MIN_BASELINE_POINTS:
        return None
    moving_ranges = [abs(b - a) for a, b in pairwise(values)]
    mr_bar = fmean(moving_ranges)
    sigma = mr_bar / D2_N2
    if sigma <= 0:
        return None
    return IndividualsBaseline(mean=fmean(values), sigma=sigma, sample_size=len(values))


def classify_points(values: list[float], baseline: IndividualsBaseline) -> list[str | None]:
    """Western Electric rules 1 & 2 for each point.

    rule1: point outside mean ± 3σ.
    rule2: point completes (or extends) a run of 8+ consecutive points on one side of the mean.
    Returns the rule name or None for each point. Rule 1 takes precedence.
    """
    result: list[str | None] = []
    run_side = 0
    run_len = 0
    for v in values:
        side = 1 if v > baseline.mean else (-1 if v < baseline.mean else 0)
        if side != 0 and side == run_side:
            run_len += 1
        else:
            run_side, run_len = side, (1 if side != 0 else 0)
        if v > baseline.ucl or v < baseline.lcl:
            result.append("rule1_beyond_3sigma")
        elif run_len >= RUN_RULE_LENGTH:
            result.append("rule2_run_of_8")
        else:
            result.append(None)
    return result
