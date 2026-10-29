"""Read-side queries over the historical benchmark (schema `historical`). All aggregation in SQL."""

from enum import StrEnum

from sqlalchemy import and_, case, func, not_, select
from sqlalchemy.orm import Session

from factoryops.analytics import defect_rate_pct
from factoryops.errors import NotFound
from factoryops.historical.ai4i import DATASET
from factoryops.models import Ai4iRecord, DatasetImport, ModelEvaluation
from factoryops.schemas import (
    FailureAnalysis,
    FailureBin,
    GroupCount,
    HistoricalSummary,
    RuleCheck,
    TypeBreakdown,
)
from factoryops.services.common import paginate

R = Ai4iRecord
MODES = ("twf", "hdf", "pwf", "osf", "rnf")


class Feature(StrEnum):
    AIR_TEMP = "air_temp_k"
    PROCESS_TEMP = "process_temp_k"
    SPEED = "rotational_speed_rpm"
    TORQUE = "torque_nm"
    TOOL_WEAR = "tool_wear_min"
    TEMP_DIFF = "temp_diff_k"
    POWER = "power_w"
    STRAIN = "strain_min_nm"


FEATURE_META: dict[Feature, tuple[str, str, bool]] = {
    Feature.AIR_TEMP: ("Air temperature", "K", False),
    Feature.PROCESS_TEMP: ("Process temperature", "K", False),
    Feature.SPEED: ("Rotational speed", "rpm", False),
    Feature.TORQUE: ("Torque", "Nm", False),
    Feature.TOOL_WEAR: ("Tool wear", "min", False),
    Feature.TEMP_DIFF: ("Process − air temperature", "K", True),
    Feature.POWER: ("Power (torque × angular speed)", "W", True),
    Feature.STRAIN: ("Tool wear × torque", "min·Nm", True),
}


def current_import(session: Session) -> DatasetImport:
    imp = session.scalar(select(DatasetImport).where(DatasetImport.dataset == DATASET))
    if imp is None:
        raise NotFound("AI4I 2020 has not been imported; run `python -m factoryops.cli ai4i-setup`")
    return imp


def list_imports(session: Session) -> list[DatasetImport]:
    return list(session.scalars(select(DatasetImport).order_by(DatasetImport.imported_at)))


def summary(session: Session) -> HistoricalSummary:
    imp = current_import(session)
    scope = R.import_id == imp.id
    rows, failures, *mode_counts = session.execute(
        select(
            func.count(),
            func.count().filter(R.machine_failure),
            *[func.count().filter(getattr(R, m)) for m in MODES],
        ).where(scope)
    ).one()
    by_type = session.execute(
        select(R.product_type, func.count(), func.count().filter(R.machine_failure))
        .where(scope)
        .group_by(R.product_type)
    ).all()
    order = {"L": 0, "M": 1, "H": 2}
    return HistoricalSummary(
        dataset=imp,
        rows=rows,
        failures=failures,
        failure_rate_pct=defect_rate_pct(rows, failures),
        by_type=[
            TypeBreakdown(
                product_type=t, rows=n, failures=f, failure_rate_pct=defect_rate_pct(n, f)
            )
            for t, n, f in sorted(by_type, key=lambda r: order.get(r[0], 9))
        ],
        by_mode=[
            GroupCount(key=m.upper(), count=c) for m, c in zip(MODES, mode_counts, strict=True)
        ],
        notes={
            "nature": "Synthetic benchmark (UCI). Not live telemetry and not data from any "
            "specific manufacturer.",
            "failure_rate_pct": "Rows with Machine failure = 1 ÷ all rows × 100.",
            "modes": "A row can carry several failure-mode flags; mode counts do not sum to "
            "the failure count. See label observations for inconsistencies in the source file.",
        },
    )


def failure_analysis(session: Session, feature: Feature, bins: int) -> FailureAnalysis:
    imp = current_import(session)
    col = getattr(R, feature.value)
    scope = R.import_id == imp.id
    lo, hi = session.execute(select(func.min(col), func.max(col)).where(scope)).one()
    label, unit, derived = FEATURE_META[feature]
    if lo is None:
        return FailureAnalysis(
            feature=feature.value, label=label, unit=unit, derived=derived, bins=[]
        )
    if hi == lo:
        hi = lo + 1
    width = (hi - lo) / bins
    # width_bucket puts the max value in bucket bins+1; fold it into the last bucket.
    bucket = func.least(func.width_bucket(col, lo, hi, bins), bins).label("b")
    result = session.execute(
        select(
            bucket,
            func.count(),
            func.count().filter(R.machine_failure),
            *[func.count().filter(getattr(R, m)) for m in MODES],
        )
        .where(scope)
        .group_by(bucket)
        .order_by(bucket)
    ).all()
    by_bucket = {r[0]: r for r in result}
    out = []
    for b in range(1, bins + 1):
        r = by_bucket.get(b)
        n, f = (r[1], r[2]) if r else (0, 0)
        out.append(
            FailureBin(
                lower=round(lo + (b - 1) * width, 4),
                upper=round(lo + b * width, 4),
                rows=n,
                failures=f,
                failure_rate_pct=defect_rate_pct(n, f),
                modes={m.upper(): (r[3 + i] if r else 0) for i, m in enumerate(MODES)},
            )
        )
    return FailureAnalysis(feature=feature.value, label=label, unit=unit, derived=derived, bins=out)


def _osf_limit():
    return case((R.product_type == "L", 11000.0), (R.product_type == "M", 12000.0), else_=13000.0)


RULES = [
    (
        "HDF",
        "process − air temperature < 8.6 K and speed < 1380 rpm",
        True,
        lambda: and_(R.temp_diff_k < 8.6, R.rotational_speed_rpm < 1380),
    ),
    ("PWF", "power < 3500 W or > 9000 W", True, lambda: (R.power_w < 3500) | (R.power_w > 9000)),
    (
        "OSF",
        "tool wear × torque > 11000 / 12000 / 13000 min·Nm (L / M / H)",
        True,
        lambda: R.strain_min_nm > _osf_limit(),
    ),
    (
        "TWF",
        "tool replaced or fails at a random wear time between 200 and 240 min",
        False,
        lambda: and_(R.tool_wear_min >= 200, R.tool_wear_min <= 240),
    ),
]


def rule_checks(session: Session) -> list[RuleCheck]:
    """Compare the UCI-documented generating rules with the labels actually in the file."""
    imp = current_import(session)
    out = []
    for mode, text, deterministic, cond_fn in RULES:
        cond = cond_fn()
        label = getattr(R, mode.lower())
        matches, labeled, tp, fp, fn = session.execute(
            select(
                func.count().filter(cond),
                func.count().filter(label),
                func.count().filter(and_(cond, label)),
                func.count().filter(and_(cond, not_(label))),
                func.count().filter(and_(not_(cond), label)),
            ).where(R.import_id == imp.id)
        ).one()
        out.append(
            RuleCheck(
                mode=mode,
                rule=text,
                deterministic=deterministic,
                rule_matches=matches,
                labeled=labeled,
                true_positive=tp,
                false_positive=fp,
                false_negative=fn,
            )
        )
    return out


def list_records(
    session: Session,
    *,
    product_type: str | None,
    failure: bool | None,
    mode: str | None,
    page: int,
    page_size: int,
):
    imp = current_import(session)
    stmt = select(R).where(R.import_id == imp.id)
    if product_type:
        stmt = stmt.where(R.product_type == product_type)
    if failure is not None:
        stmt = stmt.where(R.machine_failure.is_(failure))
    if mode:
        stmt = stmt.where(getattr(R, mode.lower()).is_(True))
    return paginate(session, stmt.order_by(R.udi), page, page_size)


def evaluations(session: Session) -> list[ModelEvaluation]:
    imp = current_import(session)
    order = case(
        (ModelEvaluation.model_name == "logistic_regression", 0),
        (ModelEvaluation.model_name == "gradient_boosting", 1),
        else_=2,
    )
    return list(
        session.scalars(
            select(ModelEvaluation).where(ModelEvaluation.import_id == imp.id).order_by(order)
        )
    )
