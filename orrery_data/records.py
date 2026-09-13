"""Schema-v1 master records shared by readers and producers."""

import gzip
import math
import re

from .formats import DataError, FIELDS, identity
from .storage import loads_json, verify_generated_gzip

OBJECT_FIELDS = ("id", "number", "packed_designation", "readable_designation", "disc")
ORBIT_FIELDS = (*FIELDS[1:], "orbit_reference", "orbit_computer")
MASTER_FIELDS = (*OBJECT_FIELDS, *ORBIT_FIELDS)


def validate_catalog_record(row):
    if (not isinstance(row, dict) or row.keys() != set(FIELDS)
            or not all(type(value) in (int, float) and math.isfinite(value) for value in row.values())):
        raise DataError("Invalid discovery catalog fields")
    if (row["a"] <= 0 or row["n"] <= 0 or not 0 <= row["e"] < 1
            or not 0 <= row["i"] <= 180 or not all(0 <= row[k] <= 360 for k in ("W", "w", "M"))):
        raise DataError("Invalid discovery orbital elements")


def validate_record(row):
    if not isinstance(row, dict) or row.keys() != set(MASTER_FIELDS):
        raise DataError("Invalid master fields")
    for key in ("id", "packed_designation", "readable_designation", "orbit_reference", "orbit_computer"):
        if row[key] is None and key not in ("id", "packed_designation"):
            continue
        if not isinstance(row[key], str) or not row[key]:
            raise DataError(f"Invalid master {key}")
    if row["number"] is not None and type(row["number"]) is not int:
        raise DataError("Invalid master number")
    if identity(row["packed_designation"]) != (row["id"], row["number"]):
        raise DataError("Master MPC identity mismatch")
    display_number = re.match(r"\(([0-9]+)\)", row["readable_designation"] or "")
    if display_number and int(display_number[1]) != row["number"]:
        raise DataError("Packed and readable MPC numbers disagree")
    if row["disc"] is not None and row["number"] is None:
        raise DataError("Only numbered master records can have discovery dates")
    for key in FIELDS:
        if key == "disc" and row[key] is None:
            continue
        if type(row[key]) not in (int, float) or not math.isfinite(row[key]):
            raise DataError(f"Invalid master numeric field: {key}")
    if (row["a"] <= 0 or row["n"] <= 0 or not 0 <= row["e"] < 1
            or not 0 <= row["i"] <= 180 or not all(0 <= row[k] <= 360 for k in ("W", "w", "M"))):
        raise DataError("Invalid master elliptic orbit")


def iter_master(path):
    try:
        verify_generated_gzip(path)
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                try:
                    row = loads_json(line)
                    validate_record(row)
                except (ValueError, TypeError, OverflowError) as exc:
                    raise DataError(f"Master row {number}: {exc}") from exc
                yield row
    except (OSError, EOFError) as exc:
        raise DataError(f"Invalid master.jsonl.gz: {exc}") from exc
