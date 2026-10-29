"""UCI AI4I 2020 Predictive Maintenance Dataset: provenance, validation and idempotent import.

The dataset is SYNTHETIC (per its UCI description) and models a milling process. It is used
here as a documented historical benchmark; it is not live telemetry and not data from any
specific manufacturer.
"""

import csv
import hashlib
import io
import math
import re
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from factoryops.models import Ai4iRecord, DatasetImport

DATASET = "ai4i2020"
SOURCE = {
    "title": "AI4I 2020 Predictive Maintenance Dataset",
    "source_url": "https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset",
    "download_url": "https://archive.ics.uci.edu/static/public/601/ai4i+2020+predictive+maintenance+dataset.zip",
    "doi": "10.24432/C5HS5C",
    "license": "Creative Commons Attribution 4.0 International (CC BY 4.0)",
    "citation": "Matzka, S. (2020). AI4I 2020 Predictive Maintenance Dataset [Dataset]. "
    "UCI Machine Learning Repository. https://doi.org/10.24432/C5HS5C",
    "nature": "Synthetic dataset that reflects real predictive maintenance data (UCI description).",
}
CSV_NAME = "ai4i2020.csv"
# SHA-256 of ai4i2020.csv as extracted from the UCI zip (observed 2026-10-28). A different hash
# means the upstream file changed; the downloader refuses it unless explicitly allowed.
EXPECTED_SHA256 = "dc6630cd9b1f0f853922fad78a1b6436570d3f1ec863f1dd5c4340ac56bc8a8e"

COLUMNS = (
    "UDI",
    "Product ID",
    "Type",
    "Air temperature [K]",
    "Process temperature [K]",
    "Rotational speed [rpm]",
    "Torque [Nm]",
    "Tool wear [min]",
    "Machine failure",
    "TWF",
    "HDF",
    "PWF",
    "OSF",
    "RNF",
)
MODES = ("TWF", "HDF", "PWF", "OSF", "RNF")
MAX_ERRORS = 50

# Broad physical sanity bounds (not fitted to the data): values outside are rejected.
BOUNDS = {
    "Air temperature [K]": (200.0, 400.0),
    "Process temperature [K]": (200.0, 400.0),
    "Rotational speed [rpm]": (1, 20000),
    "Torque [Nm]": (0.0, 1000.0),
    "Tool wear [min]": (0, 10000),
}
_PRODUCT_ID = re.compile(r"^[LMH]\d{5}$")


class ImportValidationError(Exception):
    def __init__(self, errors: list[str], rows_checked: int):
        super().__init__(f"{len(errors)} validation error(s); nothing was imported")
        self.errors = errors
        self.rows_checked = rows_checked


class ImportConflict(Exception):
    """A different file version is already imported and --replace was not given."""


@dataclass
class ParsedDataset:
    rows: list[dict]
    sha256: str
    observations: dict = field(default_factory=dict)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _flag(value: str, col: str, line: int, errors: list[str]) -> bool | None:
    if value in ("0", "1"):
        return value == "1"
    errors.append(f"line {line}: {col} must be 0 or 1, got {value!r}")
    return None


def _number(value: str, col: str, line: int, errors: list[str], integer: bool):
    try:
        num = int(value) if integer else float(value)
    except ValueError:
        kind = "an integer" if integer else "a number"
        errors.append(f"line {line}: {col} must be {kind}, got {value!r}")
        return None
    if not integer and not math.isfinite(num):
        errors.append(f"line {line}: {col} must be finite, got {value!r}")
        return None
    lo, hi = BOUNDS[col]
    if not lo <= num <= hi:
        errors.append(f"line {line}: {col}={num} outside sanity bounds [{lo}, {hi}]")
        return None
    return num


def parse_ai4i(data: bytes) -> ParsedDataset:
    """Validate the whole file. Any error rejects the file (all-or-nothing)."""
    errors: list[str] = []
    try:
        text = data.decode("utf-8-sig")  # the published file starts with a UTF-8 BOM
    except UnicodeDecodeError as exc:
        raise ImportValidationError([f"file is not UTF-8: {exc}"], 0) from exc
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None:
        raise ImportValidationError(["file is empty"], 0)
    header = [h.strip() for h in header]
    if tuple(header) != COLUMNS:
        missing = [c for c in COLUMNS if c not in header]
        extra = [c for c in header if c not in COLUMNS]
        detail = f"missing {missing}" if missing else ""
        detail += f"{'; ' if detail else ''}unexpected {extra}" if extra else ""
        raise ImportValidationError(
            [f"header does not match the AI4I 2020 schema ({detail or 'column order differs'})"], 0
        )

    rows: list[dict] = []
    seen_udi: set[int] = set()
    seen_pid: set[str] = set()
    line = 1
    for line, raw in enumerate(reader, start=2):
        if len(errors) >= MAX_ERRORS:
            break
        if not raw or all(not c.strip() for c in raw):
            continue
        if len(raw) != len(COLUMNS):
            errors.append(f"line {line}: expected {len(COLUMNS)} columns, got {len(raw)}")
            continue
        r = dict(zip(COLUMNS, (c.strip() for c in raw), strict=True))
        before = len(errors)
        try:
            udi = int(r["UDI"])
            if udi <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"line {line}: UDI must be a positive integer, got {r['UDI']!r}")
            udi = None
        if udi is not None:
            if udi in seen_udi:
                errors.append(f"line {line}: duplicate UDI {udi} within file")
            seen_udi.add(udi)
        ptype = r["Type"]
        if ptype not in ("L", "M", "H"):
            errors.append(f"line {line}: Type must be L, M or H, got {ptype!r}")
        pid = r["Product ID"]
        if not _PRODUCT_ID.match(pid):
            errors.append(f"line {line}: Product ID {pid!r} must look like L12345")
        elif pid[0] != ptype:
            errors.append(f"line {line}: Product ID {pid} does not match Type {ptype}")
        elif pid in seen_pid:
            errors.append(f"line {line}: duplicate Product ID {pid} within file")
        seen_pid.add(pid)
        air = _number(r["Air temperature [K]"], "Air temperature [K]", line, errors, False)
        proc = _number(r["Process temperature [K]"], "Process temperature [K]", line, errors, False)
        rpm = _number(r["Rotational speed [rpm]"], "Rotational speed [rpm]", line, errors, True)
        torque = _number(r["Torque [Nm]"], "Torque [Nm]", line, errors, False)
        wear = _number(r["Tool wear [min]"], "Tool wear [min]", line, errors, True)
        flags = {c: _flag(r[c], c, line, errors) for c in ("Machine failure", *MODES)}
        if len(errors) == before:
            rows.append(
                {
                    "udi": udi,
                    "product_id": pid,
                    "product_type": ptype,
                    "air_temp_k": air,
                    "process_temp_k": proc,
                    "rotational_speed_rpm": rpm,
                    "torque_nm": torque,
                    "tool_wear_min": wear,
                    "machine_failure": flags["Machine failure"],
                    **{m.lower(): flags[m] for m in MODES},
                }
            )
    if errors:
        raise ImportValidationError(errors, line - 1)
    if not rows:
        raise ImportValidationError(["file has a header but no data rows"], 0)
    return ParsedDataset(rows=rows, sha256=sha256_bytes(data), observations=observe(rows))


def observe(rows: list[dict]) -> dict:
    """Label-consistency facts about the file. These are reported, never 'corrected'."""
    modes = [m.lower() for m in MODES]
    return {
        "rows": len(rows),
        "machine_failures": sum(r["machine_failure"] for r in rows),
        "mode_counts": {m.upper(): sum(r[m] for r in rows) for m in modes},
        "failure_without_mode": sum(
            1 for r in rows if r["machine_failure"] and not any(r[m] for m in modes)
        ),
        "mode_without_failure": {
            m.upper(): sum(1 for r in rows if r[m] and not r["machine_failure"]) for m in modes
        },
        "multi_mode_rows": sum(1 for r in rows if sum(r[m] for m in modes) > 1),
        "type_counts": {t: sum(1 for r in rows if r["product_type"] == t) for t in "LMH"},
    }


def import_ai4i(session: Session, data: bytes, file_name: str, replace: bool = False) -> dict:
    """Idempotent, all-or-nothing import.

    * Same file (same SHA-256) already imported -> no-op ("unchanged").
    * Different file already imported -> ImportConflict unless replace=True, in which case the
      old import (and its records/evaluations, via cascade) is replaced in the same transaction.
    """
    parsed = parse_ai4i(data)  # raises ImportValidationError before touching the database
    existing = session.scalar(
        select(DatasetImport).where(DatasetImport.dataset == DATASET).with_for_update()
    )
    if existing is not None:
        if existing.file_sha256 == parsed.sha256:
            session.rollback()
            return {
                "status": "unchanged",
                "import_id": str(existing.id),
                "sha256": parsed.sha256,
                "rows_in_file": len(parsed.rows),
                "rows_inserted": 0,
                "rows_in_database": existing.row_count,
            }
        if not replace:
            session.rollback()
            raise ImportConflict(
                f"{DATASET} is already imported from a different file "
                f"(sha256 {existing.file_sha256[:12]}…); re-run with --replace to swap versions"
            )
        session.execute(delete(DatasetImport).where(DatasetImport.id == existing.id))
        session.flush()

    record = DatasetImport(
        dataset=DATASET,
        title=SOURCE["title"],
        source_url=SOURCE["source_url"],
        doi=SOURCE["doi"],
        license=SOURCE["license"],
        citation=SOURCE["citation"],
        file_name=file_name,
        file_sha256=parsed.sha256,
        row_count=len(parsed.rows),
        observations={
            **parsed.observations,
            "matches_published_checksum": parsed.sha256 == EXPECTED_SHA256,
        },
    )
    session.add(record)
    session.flush()
    session.execute(insert(Ai4iRecord), [{"import_id": record.id, **r} for r in parsed.rows])
    session.commit()
    return {
        "status": "replaced" if existing is not None else "imported",
        "import_id": str(record.id),
        "sha256": parsed.sha256,
        "matches_published_checksum": parsed.sha256 == EXPECTED_SHA256,
        "rows_in_file": len(parsed.rows),
        "rows_inserted": len(parsed.rows),
        "observations": parsed.observations,
    }


def download(dest_dir: Path, allow_checksum_mismatch: bool = False, timeout: int = 60) -> Path:
    """Fetch the UCI zip, extract the CSV and verify its SHA-256."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(SOURCE["download_url"], timeout=timeout) as resp:
        archive = resp.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        data = zf.read(CSV_NAME)
    digest = sha256_bytes(data)
    if digest != EXPECTED_SHA256 and not allow_checksum_mismatch:
        raise ValueError(
            f"downloaded {CSV_NAME} has sha256 {digest}, expected {EXPECTED_SHA256}; "
            "the upstream file changed. Inspect it, then pass --allow-checksum-mismatch."
        )
    path = dest_dir / CSV_NAME
    path.write_bytes(data)
    return path
