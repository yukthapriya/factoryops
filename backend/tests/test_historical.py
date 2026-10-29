"""Historical benchmark: import validation, idempotency, API and model evaluation.

Uses small generated CSVs in the AI4I 2020 format (no network access needed). The generator
labels OSF with the documented overstrain rule so models have a learnable signal.
"""

import json
import random

import numpy as np
import pytest
from sqlalchemy import func, select

from factoryops.historical import ai4i
from factoryops.historical.model import (
    FEATURES,
    LeakageError,
    assert_no_leakage,
    build_dataset,
    classification_metrics,
    evaluate,
    evaluate_and_store,
)
from factoryops.models import Ai4iRecord, DatasetImport, ModelEvaluation

HEADER = ",".join(ai4i.COLUMNS)
LIMITS = {"L": 11000, "M": 12000, "H": 13000}


def make_rows(n: int = 600, seed: int = 0) -> list[list[str]]:
    rng = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        t = rng.choices("LMH", weights=[6, 3, 1])[0]
        air = round(rng.uniform(296, 304), 1)
        # Ranges keep HDF (ΔT >= 8.6 K) and PWF (3500-9000 W) rules false, so only the
        # documented overstrain rule (OSF) produces failures.
        proc = round(air + rng.uniform(8.7, 12), 1)
        rpm = rng.randint(1400, 1800)
        torque = round(rng.uniform(25, 45), 1)
        wear = rng.randint(0, 320)
        osf = int(wear * torque > LIMITS[t])
        rows.append(
            [
                str(i),
                f"{t}{10000 + i}",
                t,
                str(air),
                str(proc),
                str(rpm),
                str(torque),
                str(wear),
                str(osf),
                "0",
                "0",
                "0",
                str(osf),
                "0",
            ]
        )
    return rows


def to_csv(rows: list[list[str]], header: str = HEADER, bom: bool = True) -> bytes:
    text = "\n".join([header, *(",".join(r) for r in rows)]) + "\n"
    return (("﻿" if bom else "") + text).encode()


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------- validation (pure)


def test_valid_file_with_bom_parses_and_reports_observations():
    rows = make_rows(50)
    rows[0][8], rows[0][9:14] = "1", ["0"] * 5  # failure with no mode
    rows[1][8], rows[1][13] = "0", "1"  # RNF without failure flag
    parsed = ai4i.parse_ai4i(to_csv(rows))
    assert len(parsed.rows) == 50
    obs = parsed.observations
    assert obs["failure_without_mode"] == 1
    assert obs["mode_without_failure"]["RNF"] == 1
    assert sum(obs["type_counts"].values()) == 50
    assert parsed.rows[0]["machine_failure"] is True


def test_header_mismatch_rejected():
    with pytest.raises(ai4i.ImportValidationError) as exc:
        ai4i.parse_ai4i(to_csv(make_rows(3), header=HEADER.replace("Torque [Nm]", "Torque")))
    assert "missing ['Torque [Nm]']" in exc.value.errors[0]


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda r: r.__setitem__(2, "X"), "Type must be L, M or H"),
        (lambda r: r.__setitem__(slice(1, 3), ["H99999", "L"]), "does not match Type"),
        (lambda r: r.__setitem__(6, "abc"), "Torque [Nm] must be a number"),
        (lambda r: r.__setitem__(6, "nan"), "must be finite"),
        (lambda r: r.__setitem__(5, "999999"), "outside sanity bounds"),
        (lambda r: r.__setitem__(7, "1.5"), "Tool wear [min] must be an integer"),
        (lambda r: r.__setitem__(8, "2"), "Machine failure must be 0 or 1"),
        (lambda r: r.__setitem__(0, "-3"), "UDI must be a positive integer"),
        (lambda r: r.pop(), "expected 14 columns"),
    ],
)
def test_row_level_errors(mutate, fragment):
    rows = make_rows(5)
    mutate(rows[2])
    with pytest.raises(ai4i.ImportValidationError) as exc:
        ai4i.parse_ai4i(to_csv(rows))
    assert any(fragment in e and e.startswith("line 4") for e in exc.value.errors), exc.value.errors


def test_duplicate_ids_within_file_rejected():
    rows = make_rows(5)
    rows[3][0] = rows[1][0]
    rows[4][1:3] = rows[2][1:3]
    with pytest.raises(ai4i.ImportValidationError) as exc:
        ai4i.parse_ai4i(to_csv(rows))
    assert any("duplicate UDI" in e for e in exc.value.errors)
    assert any("duplicate Product ID" in e for e in exc.value.errors)


def test_empty_inputs_rejected():
    with pytest.raises(ai4i.ImportValidationError):
        ai4i.parse_ai4i(b"")
    with pytest.raises(ai4i.ImportValidationError):
        ai4i.parse_ai4i(to_csv([]))


# ---------------------------------------------------------------- import (database)


def test_invalid_file_imports_nothing(db):
    rows = make_rows(20)
    rows[-1][2] = "Z"
    with pytest.raises(ai4i.ImportValidationError):
        ai4i.import_ai4i(db, to_csv(rows), "bad.csv")
    assert count(db, DatasetImport) == 0 and count(db, Ai4iRecord) == 0


def test_import_records_provenance_and_derived_columns(db):
    result = ai4i.import_ai4i(db, to_csv(make_rows(40)), "fixture.csv")
    assert result["status"] == "imported" and result["rows_inserted"] == 40
    assert result["matches_published_checksum"] is False  # fixture, not the UCI file
    imp = db.scalar(select(DatasetImport))
    assert imp.source_url.startswith("https://archive.ics.uci.edu/dataset/601")
    assert imp.doi == "10.24432/C5HS5C" and "CC BY 4.0" in imp.license
    assert imp.row_count == 40 and len(imp.file_sha256) == 64
    rec = db.scalar(select(Ai4iRecord).order_by(Ai4iRecord.udi).limit(1))
    assert rec.temp_diff_k == pytest.approx(rec.process_temp_k - rec.air_temp_k)
    assert rec.power_w == pytest.approx(rec.torque_nm * rec.rotational_speed_rpm * 2 * np.pi / 60)
    assert rec.strain_min_nm == pytest.approx(rec.tool_wear_min * rec.torque_nm)


def test_reimporting_same_file_is_a_no_op(db):
    data = to_csv(make_rows(30))
    first = ai4i.import_ai4i(db, data, "a.csv")
    second = ai4i.import_ai4i(db, data, "a.csv")
    assert second["status"] == "unchanged" and second["rows_inserted"] == 0
    assert second["import_id"] == first["import_id"]
    assert count(db, Ai4iRecord) == 30 and count(db, DatasetImport) == 1


def test_different_file_requires_replace(db):
    ai4i.import_ai4i(db, to_csv(make_rows(30, seed=1)), "v1.csv")
    with pytest.raises(ai4i.ImportConflict):
        ai4i.import_ai4i(db, to_csv(make_rows(25, seed=2)), "v2.csv")
    assert count(db, Ai4iRecord) == 30
    result = ai4i.import_ai4i(db, to_csv(make_rows(25, seed=2)), "v2.csv", replace=True)
    assert result["status"] == "replaced"
    assert count(db, Ai4iRecord) == 25 and count(db, DatasetImport) == 1
    assert db.scalar(select(DatasetImport.file_name)) == "v2.csv"


def test_live_seed_reset_leaves_historical_data_alone(db):
    from factoryops.cli.__main__ import reset_database

    ai4i.import_ai4i(db, to_csv(make_rows(10)), "keep.csv")
    reset_database(db)
    assert count(db, Ai4iRecord) == 10


def test_cli_reports_validation_errors(tmp_path, capsys):
    from factoryops.cli.__main__ import main as cli_main

    bad = tmp_path / "bad.csv"
    rows = make_rows(5)
    rows[1][8] = "yes"
    bad.write_bytes(to_csv(rows))
    assert cli_main(["ai4i-import", "--file", str(bad)]) == 1
    out = json.loads(capsys.readouterr().out)
    assert (
        out["status"] == "failed" and "line 3: Machine failure must be 0 or 1" in out["errors"][0]
    )


# ---------------------------------------------------------------- API


def test_api_before_import(client):
    assert client.get("/api/historical/datasets").json() == []
    r = client.get("/api/historical/summary")
    assert r.status_code == 404 and "ai4i-setup" in r.json()["error"]["message"]


def test_api_summary_analysis_rules_records(client, db):
    rows = make_rows(300)
    ai4i.import_ai4i(db, to_csv(rows), "fixture.csv")
    failures = sum(int(r[8]) for r in rows)

    s = client.get("/api/historical/summary").json()
    assert s["rows"] == 300 and s["failures"] == failures
    assert sum(t["rows"] for t in s["by_type"]) == 300
    assert {m["key"]: m["count"] for m in s["by_mode"]}["OSF"] == failures
    assert s["dataset"]["doi"] == "10.24432/C5HS5C"

    fa = client.get("/api/historical/failure-analysis?feature=strain_min_nm&bins=10").json()
    assert len(fa["bins"]) == 10 and fa["derived"] is True
    assert sum(b["rows"] for b in fa["bins"]) == 300
    assert sum(b["failures"] for b in fa["bins"]) == failures
    assert fa["bins"][0]["failures"] == 0  # lowest strain never overstrains
    assert client.get("/api/historical/failure-analysis?feature=machine_failure").status_code == 422
    assert client.get("/api/historical/failure-analysis?bins=2").status_code == 422

    rules = {r["mode"]: r for r in client.get("/api/historical/failure-rules").json()}
    assert rules["OSF"]["true_positive"] == failures
    assert rules["OSF"]["false_positive"] == rules["OSF"]["false_negative"] == 0
    assert rules["TWF"]["deterministic"] is False

    rec = client.get("/api/historical/records?failure=true&page_size=5").json()
    assert rec["total"] == failures and all(r["machine_failure"] for r in rec["items"])
    assert client.get("/api/historical/records?product_type=Q").status_code == 422
    assert client.get("/api/historical/model-evaluations").json() == []


# ---------------------------------------------------------------- model evaluation


def _parsed_rows(n=800, seed=0):
    return ai4i.parse_ai4i(to_csv(make_rows(n, seed))).rows


def test_leakage_guard():
    for bad in ("machine_failure", "twf", "osf", "udi", "product_id"):
        with pytest.raises(LeakageError):
            assert_no_leakage((*FEATURES, bad))
    assert_no_leakage(FEATURES)
    ds = build_dataset(_parsed_rows(50))
    assert ds.X.shape == (50, len(FEATURES))


def test_classification_metrics_hand_example():
    y = np.array([1, 1, 1, 0, 0, 0, 0, 0])
    scores = np.array([0.9, 0.8, 0.2, 0.7, 0.1, 0.1, 0.1, 0.1])
    m = classification_metrics(y, scores, 0.5)
    assert m["confusion_matrix"] == {"tn": 4, "fp": 1, "fn": 1, "tp": 2}
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    # Ranked by score the positives sit at ranks 1, 2 and 4: AP = (1/1 + 2/2 + 3/4) / 3
    assert m["pr_auc"] == pytest.approx((1 + 1 + 0.75) / 3)


def test_evaluation_is_stratified_deterministic_and_leak_free():
    rows = _parsed_rows()
    first = evaluate(rows, seed=7)
    second = evaluate(rows, seed=7)
    assert [r["metrics"] for r in first] == [r["metrics"] for r in second]
    names = [r["model_name"] for r in first]
    assert names == ["logistic_regression", "gradient_boosting", "documented_rules"]
    split = first[0]["params"]["split"]
    assert split["train_rows"] + split["test_rows"] == len(rows)
    assert abs(split["train_prevalence"] - split["test_prevalence"]) < 0.01
    for r in first:
        assert set(r["params"]["features"]).isdisjoint(r["params"]["excluded_columns"])
        cm = r["metrics"]["confusion_matrix"]
        assert sum(cm.values()) == split["test_rows"]
        assert cm["tp"] + cm["fn"] == split["test_positives"]
        assert 0 <= r["metrics"]["pr_auc"] <= 1
    rules = first[2]["metrics"]
    assert rules["precision"] == 1.0 and rules["recall"] == 1.0  # fixture labels follow OSF rule
    importance = first[1]["feature_importance"]
    assert {i["feature"] for i in importance} == set(FEATURES)


def test_evaluate_and_store_persists_and_api_serves(client, db):
    with pytest.raises(LookupError):
        evaluate_and_store(db)
    ai4i.import_ai4i(db, to_csv(make_rows(800)), "fixture.csv")
    evaluate_and_store(db, seed=3)
    evaluate_and_store(db, seed=3)  # re-run replaces, does not duplicate
    assert count(db, ModelEvaluation) == 3
    evals = client.get("/api/historical/model-evaluations").json()
    assert [e["model_name"] for e in evals] == [
        "logistic_regression",
        "gradient_boosting",
        "documented_rules",
    ]
    assert evals[0]["params"]["dataset_sha256"] == db.scalar(select(DatasetImport.file_sha256))
    assert len(evals[1]["pr_curve"]) > 2
