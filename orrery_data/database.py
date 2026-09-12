"""Complete local SQLite artifacts derived from validated, immutable snapshots."""

from contextlib import closing, contextmanager
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile

from . import __version__
from .formats import DataError, FIELDS, identity
from .pipeline import load_snapshot
from .storage import digest, encode, file_info, now, verify_file, writer_lock


DATABASE_SCHEMA_VERSION = 1
APPLICATION_ID = 0x4F525259  # ORRY
DEFAULT_DATABASE = Path("artifacts/orrery.sqlite3")
OBJECT_FIELDS = ("id", "number", "packed_designation", "readable_designation", "disc")
ORBIT_FIELDS = (*FIELDS[1:], "orbit_reference", "orbit_computer")
MASTER_FIELDS = (*OBJECT_FIELDS, *ORBIT_FIELDS)
# SQLite identifiers are case insensitive: JSON W and w MUST use distinct SQL names.
SQL_ORBIT_FIELDS = ("epoch", "a", "e", "i", "ascending_node", "perihelion_argument",
                    "M", "n", "orbit_reference", "orbit_computer")
SELECT_RECORD = ", ".join([*(f"o.{key}" for key in OBJECT_FIELDS),
                           *(f"r.{key}" for key in SQL_ORBIT_FIELDS)])
JOIN = "objects o JOIN orbits r ON r.source_order = o.source_order"
SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);
CREATE TABLE objects (
    source_order INTEGER PRIMARY KEY CHECK (source_order > 0),
    id TEXT NOT NULL UNIQUE,
    number INTEGER UNIQUE CHECK (number > 0),
    packed_designation TEXT NOT NULL UNIQUE COLLATE BINARY,
    readable_designation TEXT,
    disc REAL
);
CREATE TABLE orbits (
    source_order INTEGER PRIMARY KEY REFERENCES objects(source_order),
    epoch REAL NOT NULL,
    a REAL NOT NULL CHECK (a > 0),
    e REAL NOT NULL CHECK (e >= 0 AND e < 1),
    i REAL NOT NULL CHECK (i >= 0 AND i <= 180),
    ascending_node REAL NOT NULL CHECK (ascending_node >= 0 AND ascending_node <= 360),
    perihelion_argument REAL NOT NULL CHECK (perihelion_argument >= 0 AND perihelion_argument <= 360),
    M REAL NOT NULL CHECK (M >= 0 AND M <= 360),
    n REAL NOT NULL CHECK (n > 0),
    orbit_reference TEXT,
    orbit_computer TEXT
);
CREATE INDEX objects_discovery ON objects(disc, source_order);
"""


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
    for key in FIELDS:
        if key == "disc" and row[key] is None:
            continue
        if type(row[key]) not in (int, float) or not math.isfinite(row[key]):
            raise DataError(f"Invalid master numeric field: {key}")
    if (row["a"] <= 0 or row["n"] <= 0 or not 0 <= row["e"] < 1
            or not 0 <= row["i"] <= 180 or not all(0 <= row[k] <= 360 for k in ("W", "w", "M"))):
        raise DataError("Invalid master elliptic orbit")


def insert_master(connection, path):
    objects, orbits = [], []
    total, known = 0, 0

    def flush():
        connection.executemany("INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)", objects)
        connection.executemany("INSERT INTO orbits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", orbits)
        objects.clear()
        orbits.clear()

    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for total, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                validate_record(row)
            except (ValueError, TypeError) as exc:
                raise DataError(f"Master row {total}: {exc}") from exc
            known += row["disc"] is not None
            objects.append((total, *(row[k] for k in OBJECT_FIELDS)))
            orbits.append((total, *(row[k] for k in ORBIT_FIELDS)))
            if len(objects) == 2000:
                flush()
        flush()
    return {"master_records": total, "known_discovery": known, "missing_discovery": total - known}


def verify_integrity(connection):
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise DataError("Database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise DataError("Database foreign key check failed")


def reject_sidecars(database):
    if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise DataError("Database has SQLite sidecars; close external writers and recover them before rebuilding")


def build_database(store, database=DEFAULT_DATABASE, version=None):
    database = Path(database).absolute()
    if database.suffix not in (".sqlite", ".sqlite3", ".db"):
        raise DataError("Database output must end in .sqlite, .sqlite3 or .db")
    if database.is_symlink() or database.resolve().is_relative_to((store / "snapshots").resolve()):
        raise DataError("Database output must not be a symlink or be inside immutable snapshots")
    # Resolve current exactly once; a concurrent refresh cannot mix snapshot versions.
    source, snapshot = load_snapshot(store, version)
    header = (source / "MPCORB-header.txt").read_text(encoding="utf-8")
    notice = Path(__file__).with_name("NOTICE.txt").read_text(encoding="utf-8")
    build_identity = {"database_schema_version": DATABASE_SCHEMA_VERSION, "tool_version": __version__,
                      "snapshot_version": snapshot["snapshot_version"], "files": snapshot["files"],
                      "notice_sha256": hashlib.sha256(notice.encode("utf-8")).hexdigest()}
    metadata = {"database_version": "sqlite-v1-" + digest(build_identity), "identity": build_identity,
                "database_schema_version": DATABASE_SCHEMA_VERSION, "tool_version": __version__,
                "sqlite_version": sqlite3.sqlite_version, "created_at": now(),
                "snapshot": snapshot, "mpcorb_header": header, "notice": notice}
    with writer_lock(database.parent):
        reject_sidecars(database)
        with tempfile.TemporaryDirectory(prefix=".sqlite-build-", dir=database.parent) as temp:
            staged = Path(temp) / "catalog.sqlite3"
            with closing(sqlite3.connect(staged)) as connection:
                connection.execute("PRAGMA journal_mode = DELETE")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
                connection.executescript(SCHEMA)
                with connection:
                    counts = insert_master(connection, source / "master.jsonl.gz")
                    if not counts["master_records"] or any(snapshot["counts"][k] != v for k, v in counts.items()):
                        raise DataError("Master contents do not match snapshot counts")
                    connection.executemany("INSERT INTO metadata VALUES (?, ?)",
                                           [(k, encode(v)) for k, v in metadata.items()])
                verify_integrity(connection)
            # Refuse changed input artifacts, even if changed after initial verification.
            for name, info in snapshot["files"].items():
                verify_file(source / name, info)
            with staged.open("rb") as stream:
                os.fsync(stream.fileno())
            artifact = file_info(staged)
            reject_sidecars(database)
            os.replace(staged, database)
    return {"database_version": metadata["database_version"], "snapshot_version": snapshot["snapshot_version"],
            "database_schema_version": DATABASE_SCHEMA_VERSION, "path": str(database),
            "counts": counts, "artifact": artifact}


@contextmanager
def open_database(database):
    # URI escaping is essential for filenames containing ?, #, spaces or percent signs.
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("BEGIN")
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != DATABASE_SCHEMA_VERSION):
            raise DataError("Unsupported OrreryData database/schema; rebuild with a compatible tool")
        metadata = {key: json.loads(value) for key, value in connection.execute("SELECT key, value FROM metadata")}
        if (metadata["database_schema_version"] != DATABASE_SCHEMA_VERSION
                or metadata["identity"]["database_schema_version"] != DATABASE_SCHEMA_VERSION
                or metadata["identity"]["tool_version"] != metadata["tool_version"]
                or metadata["identity"]["snapshot_version"] != metadata["snapshot"]["snapshot_version"]
                or metadata["database_version"] != "sqlite-v1-" + digest(metadata["identity"])):
            raise DataError("Database metadata identity mismatch")
        if not all(isinstance(metadata[key], str) for key in ("mpcorb_header", "notice")):
            raise DataError("Invalid database provenance text")
        header = metadata["mpcorb_header"].encode("utf-8")
        header_info = {"sha256": hashlib.sha256(header).hexdigest(), "bytes": len(header)}
        if (metadata["identity"]["files"] != metadata["snapshot"]["files"]
                or header_info != metadata["identity"]["files"]["MPCORB-header.txt"]
                or hashlib.sha256(metadata["notice"].encode("utf-8")).hexdigest()
                != metadata["identity"]["notice_sha256"]):
            raise DataError("Database provenance checksum mismatch")
        yield connection, metadata


def database_info(database=DEFAULT_DATABASE, verify=False):
    with open_database(database) as (connection, metadata):
        total, known = connection.execute("SELECT count(*), count(disc) FROM objects").fetchone()
        orbit_count = connection.execute("SELECT count(*) FROM orbits").fetchone()[0]
        counts = {"master_records": total, "known_discovery": known, "missing_discovery": total - known}
        if orbit_count != total or any(metadata["snapshot"]["counts"][k] != v for k, v in counts.items()):
            raise DataError("Database contents do not match snapshot counts")
        if verify:
            verify_integrity(connection)
        return {**metadata, "counts": counts, "integrity": "ok" if verify else "not_checked"}


def query_database(database=DEFAULT_DATABASE, *, object_id=None, number=None, packed_designation=None,
                   discovery="all", discovered_from=None, discovered_to=None,
                   order="source", limit=20, offset=0):
    if not 1 <= limit <= 10000 or offset < 0:
        raise DataError("Query limit must be 1–10000 and offset must be nonnegative")
    if discovery not in ("all", "known", "missing") or order not in ("source", "discovery"):
        raise DataError("Invalid discovery filter or query order")
    if sum(value is not None for value in (object_id, number, packed_designation)) > 1:
        raise DataError("Choose one exact identity filter")
    if discovered_from is not None and discovered_to is not None and discovered_from > discovered_to:
        raise DataError("Discovery start must not be after end")
    if discovery == "missing" and (discovered_from is not None or discovered_to is not None):
        raise DataError("Missing discovery dates cannot be combined with date bounds")
    clauses, values = [], []
    for column, value in (("id", object_id), ("number", number), ("packed_designation", packed_designation)):
        if value is not None:
            clauses.append(f"o.{column} = ?")
            values.append(value)
    if discovery != "all":
        clauses.append("o.disc IS " + ("NOT NULL" if discovery == "known" else "NULL"))
    for operator, value in ((">=", discovered_from), ("<=", discovered_to)):
        if value is not None:
            clauses.append(f"o.disc {operator} ?")
            values.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    ordering = "o.source_order" if order == "source" else "o.disc IS NULL, o.disc, o.source_order"
    with open_database(database) as (connection, metadata):
        matched = connection.execute(f"SELECT count(*) FROM {JOIN}{where}", values).fetchone()[0]
        rows = connection.execute(f"SELECT {SELECT_RECORD} FROM {JOIN}{where} ORDER BY {ordering} LIMIT ? OFFSET ?",
                                  [*values, limit, offset])
        return {"database_version": metadata["database_version"],
                "snapshot_version": metadata["snapshot"]["snapshot_version"],
                "matched_records": matched, "limit": limit, "offset": offset, "order": order,
                "records": [dict(zip(MASTER_FIELDS, row)) for row in rows]}
