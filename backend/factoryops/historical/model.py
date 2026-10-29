"""Offline failure-prediction evaluation on the AI4I 2020 benchmark.

Target: `machine_failure`. Inputs are ONLY the process measurements and product type.
Excluded to prevent leakage: the target, the five failure-mode labels (TWF/HDF/PWF/OSF/RNF,
which define the target), and the identifiers UDI / Product ID (row order / serials).

Evaluation: one stratified train/test split with a fixed seed; metrics are reported on the
held-out test set only. Results are persisted so the API never needs to train or load models.
"""

import math
from dataclasses import dataclass

import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from factoryops.models import Ai4iRecord, DatasetImport, ModelEvaluation

TARGET = "machine_failure"
LABEL_COLUMNS = ("machine_failure", "twf", "hdf", "pwf", "osf", "rnf")
IDENTIFIER_COLUMNS = ("udi", "product_id")
# product_type is encoded L=0, M=1, H=2 and treated as categorical by both models.
FEATURES = (
    "product_type",
    "air_temp_k",
    "process_temp_k",
    "rotational_speed_rpm",
    "torque_nm",
    "tool_wear_min",
)
TYPE_CODES = {"L": 0, "M": 1, "H": 2}
OSF_LIMITS = {"L": 11000.0, "M": 12000.0, "H": 13000.0}
DEFAULT_SEED = 42
DEFAULT_TEST_SIZE = 0.25
THRESHOLD = 0.5


class LeakageError(ValueError):
    pass


def assert_no_leakage(features: tuple[str, ...]) -> None:
    leaked = set(features) & (set(LABEL_COLUMNS) | set(IDENTIFIER_COLUMNS))
    if leaked:
        raise LeakageError(f"label/identifier columns used as features: {sorted(leaked)}")


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray
    modes: dict[str, np.ndarray]
    types: list[str]


def build_dataset(rows: list[dict], features: tuple[str, ...] = FEATURES) -> Dataset:
    assert_no_leakage(features)
    X = np.array(
        [
            [TYPE_CODES[r[f]] if f == "product_type" else float(r[f]) for f in features]
            for r in rows
        ],
        dtype=float,
    )
    y = np.array([int(r[TARGET]) for r in rows])
    modes = {m: np.array([int(r[m]) for r in rows]) for m in LABEL_COLUMNS[1:]}
    return Dataset(X, y, modes, [r["product_type"] for r in rows])


def rule_scores(X: np.ndarray, types: list[str]) -> np.ndarray:
    """Documented UCI generating rules for HDF, PWF and OSF applied to inputs (not learned).

    HDF: process - air temperature < 8.6 K and speed < 1380 rpm.
    PWF: torque × speed (rad/s) < 3500 W or > 9000 W.
    OSF: tool wear × torque > 11000 / 12000 / 13000 minNm for L / M / H.
    TWF and RNF are random in the generator and have no deterministic rule.
    """
    air, proc, rpm, torque, wear = (X[:, i] for i in range(1, 6))
    hdf = (proc - air < 8.6) & (rpm < 1380)
    power = torque * rpm * 2 * math.pi / 60
    pwf = (power < 3500) | (power > 9000)
    osf = wear * torque > np.array([OSF_LIMITS[t] for t in types])
    return (hdf | pwf | osf).astype(float)


def _models(seed: int) -> dict[str, tuple[str, object]]:
    preprocess = ColumnTransformer(
        [
            ("type", OneHotEncoder(categories=[[0.0, 1.0, 2.0]]), [0]),
            ("num", StandardScaler(), list(range(1, len(FEATURES)))),
        ]
    )
    return {
        "logistic_regression": (
            "Logistic regression (one-hot type, standardized inputs, class_weight=balanced). "
            "Linear baseline.",
            Pipeline(
                [
                    ("prep", preprocess),
                    ("clf", LogisticRegression(class_weight="balanced", max_iter=2000)),
                ]
            ),
        ),
        "gradient_boosting": (
            "Histogram gradient boosting (type as categorical, default hyperparameters, "
            "no class weighting).",
            HistGradientBoostingClassifier(categorical_features=[0], random_state=seed),
        ),
    }


def classification_metrics(y_true: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    y_pred = (scores >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "threshold": threshold,
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "pr_auc": float(average_precision_score(y_true, scores)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def _recall_by_mode(modes: dict[str, np.ndarray], y_pred: np.ndarray) -> dict:
    out = {}
    for m, labels in modes.items():
        n = int(labels.sum())
        out[m.upper()] = {"failures": n, "detected": int((labels & y_pred).sum())}
    return out


def _pr_curve(y_true: np.ndarray, scores: np.ndarray, max_points: int = 60) -> list[dict]:
    precision, recall, _ = precision_recall_curve(y_true, scores)
    idx = np.unique(np.linspace(0, len(precision) - 1, min(max_points, len(precision))).astype(int))
    return [{"recall": float(recall[i]), "precision": float(precision[i])} for i in idx]


def evaluate(
    rows: list[dict], seed: int = DEFAULT_SEED, test_size: float = DEFAULT_TEST_SIZE
) -> list[dict]:
    """Train/evaluate all models on one stratified split. Pure (no DB) and deterministic."""
    ds = build_dataset(rows)
    idx = np.arange(len(ds.y))
    train_idx, test_idx = train_test_split(
        idx, test_size=test_size, stratify=ds.y, random_state=seed
    )
    X_tr, X_te, y_tr, y_te = ds.X[train_idx], ds.X[test_idx], ds.y[train_idx], ds.y[test_idx]
    test_modes = {m: v[test_idx] for m, v in ds.modes.items()}
    split = {
        "method": "stratified train_test_split",
        "seed": seed,
        "test_size": test_size,
        "train_rows": len(train_idx),
        "test_rows": len(test_idx),
        "train_positives": int(y_tr.sum()),
        "test_positives": int(y_te.sum()),
        "train_prevalence": float(y_tr.mean()),
        "test_prevalence": float(y_te.mean()),
    }
    common = {
        "target": TARGET,
        "features": list(FEATURES),
        "excluded_columns": list(LABEL_COLUMNS[1:]) + list(IDENTIFIER_COLUMNS),
        "split": split,
        "library_versions": {"scikit-learn": sklearn.__version__, "numpy": np.__version__},
    }
    results = []
    for name, (description, model) in _models(seed).items():
        model.fit(X_tr, y_tr)
        scores = model.predict_proba(X_te)[:, 1]
        metrics = classification_metrics(y_te, scores, THRESHOLD)
        metrics["recall_by_mode"] = _recall_by_mode(test_modes, (scores >= THRESHOLD).astype(int))
        imp = permutation_importance(
            model, X_te, y_te, scoring="average_precision", n_repeats=5, random_state=seed
        )
        importance = sorted(
            (
                {"feature": f, "mean": float(m), "std": float(s)}
                for f, m, s in zip(FEATURES, imp.importances_mean, imp.importances_std, strict=True)
            ),
            key=lambda d: -d["mean"],
        )
        results.append(
            {
                "model_name": name,
                "description": description,
                "params": {**common, "trained": True},
                "metrics": metrics,
                "pr_curve": _pr_curve(y_te, scores),
                "feature_importance": importance,
            }
        )

    # Reference point, not a learned model: the dataset's own documented rules.
    test_types = [ds.types[i] for i in test_idx]
    rule = rule_scores(X_te, test_types)
    rule_metrics = classification_metrics(y_te, rule, THRESHOLD)
    rule_metrics["recall_by_mode"] = _recall_by_mode(test_modes, rule.astype(int))
    results.append(
        {
            "model_name": "documented_rules",
            "description": "Not trained: applies the UCI-documented HDF/PWF/OSF generating rules "
            "to the test inputs. Shows how much of the target is deterministic in this synthetic "
            "dataset. PR-AUC of a 0/1 score is shown for completeness only.",
            "params": {**common, "trained": False},
            "metrics": rule_metrics,
            "pr_curve": _pr_curve(y_te, rule),
            "feature_importance": [],
        }
    )
    return results


def load_rows(session: Session, import_id) -> list[dict]:
    cols = [getattr(Ai4iRecord, c) for c in (*FEATURES, *LABEL_COLUMNS)]
    result = session.execute(
        select(*cols).where(Ai4iRecord.import_id == import_id).order_by(Ai4iRecord.udi)
    )
    return [dict(r._mapping) for r in result]


def evaluate_and_store(
    session: Session, seed: int = DEFAULT_SEED, test_size: float = DEFAULT_TEST_SIZE
) -> list[dict]:
    imp = session.scalar(select(DatasetImport).where(DatasetImport.dataset == "ai4i2020"))
    if imp is None:
        raise LookupError("AI4I 2020 is not imported; run `python -m factoryops.cli ai4i-import`")
    results = evaluate(load_rows(session, imp.id), seed=seed, test_size=test_size)
    session.execute(delete(ModelEvaluation).where(ModelEvaluation.import_id == imp.id))
    for r in results:
        r["params"]["dataset_sha256"] = imp.file_sha256
        session.add(ModelEvaluation(import_id=imp.id, **r))
    session.commit()
    return results
