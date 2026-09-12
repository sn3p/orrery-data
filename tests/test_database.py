"""SQLite regression coverage at real CLI, persistence and failure boundaries."""

from contextlib import closing
import copy
import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

import test_cli


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"


class DatabaseCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = self.directory / "store"
        self.database = self.directory / "output/catalog ?#%.sqlite3"
        self.orbits = (FIXTURES / "MPCORB.DAT").read_text()
        self.dates = (FIXTURES / "NumberedMPs.txt").read_text()
        self.snapshot = self.refresh()

    def cli(self, command, *args, code=0, script=None):
        argv = [command]
        if command in ("build-db", "refresh", "export"):
            argv += ["--store", str(self.store)]
        if command in ("build-db", "query", "db-info"):
            argv += ["--database", str(self.database)]
        argv += list(map(str, args))
        invocation = [sys.executable, "-m", "orrery_data", *argv]
        if script:
            invocation = [sys.executable, "-c", script, *argv]
        result = subprocess.run(invocation, cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        if code == 2:
            self.assertFalse(result.stdout)
            return result.stderr
        self.assertFalse(result.stderr if code == 0 else result.stdout)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def refresh(self, orbits=None, dates=None, *args):
        orbital = self.directory / "MPCORB.DAT"
        numbered = self.directory / "NumberedMPs.txt"
        orbital.write_text(orbits if orbits is not None else self.orbits)
        numbered.write_text(dates if dates is not None else self.dates)
        return self.cli("refresh", "--mpcorb", orbital, "--numbered", numbered, *args)

    def master(self):
        with gzip.open(Path(self.snapshot["path"]) / "master.jsonl.gz", "rt") as stream:
            return [json.loads(line) for line in stream]

    def checksum(self):
        return hashlib.sha256(self.database.read_bytes()).hexdigest()

    def tree(self, root):
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}

    def rewrite_master(self, records):
        directory = Path(self.snapshot["path"])
        master = directory / "master.jsonl.gz"
        master.write_bytes(gzip.compress("".join(json.dumps(r) + "\n" for r in records).encode(), mtime=0))
        manifest = json.loads((directory / "snapshot.json").read_text())
        manifest["files"][master.name] = {"sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
                                            "bytes": master.stat().st_size}
        (directory / "snapshot.json").write_text(json.dumps(manifest))

    def test_complete_fields_identities_nulls_provenance_and_readonly_queries(self):
        store_before = self.tree(self.store)
        built = self.cli("build-db")
        self.assertEqual(built["artifact"]["sha256"], self.checksum())
        self.assertEqual(self.cli("query")["records"], self.master())
        info = self.cli("db-info", "--verify")
        snapshot = Path(self.snapshot["path"])
        self.assertEqual(info["snapshot"], json.loads((snapshot / "snapshot.json").read_text()))
        self.assertEqual(info["mpcorb_header"], (snapshot / "MPCORB-header.txt").read_text())
        self.assertEqual(info["notice"], (ROOT / "orrery_data/NOTICE.txt").read_text())
        self.assertEqual(info["integrity"], "ok")
        self.assertEqual(info["counts"], {"master_records": 9, "known_discovery": 6, "missing_discovery": 3})
        before = self.tree(self.database.parent)
        for record in self.master():
            self.assertEqual(self.cli("query", "--id", record["id"])["records"], [record])
            self.assertEqual(self.cli("query", "--packed-designation", record["packed_designation"])["records"], [record])
            if record["number"] is not None:
                self.assertEqual(self.cli("query", "--number", record["number"])["records"], [record])
        self.assertEqual(self.cli("query", "--id", "' OR 1=1 --")["matched_records"], 0)
        self.assertEqual(self.cli("query", "--packed-designation", "j60s01b")["records"], [])
        self.assertEqual(self.cli("query", "--number", "9999999")["records"], [])
        self.assertEqual(self.tree(self.database.parent), before)
        self.assertEqual(self.tree(self.store), store_before)

    def test_distinct_W_w_rounded_endpoint_and_nullable_numbered_date(self):
        orbits = test_cli.change_field(self.orbits, "00001", 37, 46, "360.00000")
        dates = "".join(self.dates.splitlines(keepends=True)[1:])
        self.snapshot = self.refresh(orbits, dates, "--allow-count-decrease")
        self.cli("build-db")
        row = self.cli("query", "--number", 1)["records"][0]
        self.assertEqual(row, self.master()[0])
        self.assertEqual(row["w"], 360.0)
        self.assertNotEqual(row["W"], row["w"])
        self.assertIsNone(row["disc"])

    def test_filters_inclusive_bounds_order_ties_and_pagination(self):
        self.snapshot = self.refresh(dates=self.dates.replace("1804 09 01", "1799 01 01").replace("1802 03 28", "1801 01 01"))
        self.cli("build-db")
        master = self.master()
        expected = sorted(master, key=lambda r: (r["disc"] is None, r["disc"] or 0))
        self.assertEqual(self.cli("query", "--order", "discovery")["records"], expected)
        self.assertEqual(self.cli("query", "--discovery", "known")["matched_records"], 6)
        self.assertEqual(self.cli("query", "--discovery", "missing")["records"], master[-3:])
        self.assertEqual(self.cli("query", "--discovered-from", "1801-01-01", "--discovered-to", "1801-01-01")["records"], master[:2])
        self.assertEqual(self.cli("query", "--discovered-to", "1799-01-01")["records"], [master[2]])
        self.assertEqual(self.cli("query", "--discovered-from", "2000-01-01")["matched_records"], 2)
        page = self.cli("query", "--order", "discovery", "--offset", 1, "--limit", 2)
        self.assertEqual(page["records"], expected[1:3])
        self.assertEqual(page["matched_records"], 9)
        self.assertEqual(self.cli("query", "--offset", 100)["records"], [])

    def test_repeat_updates_removals_insertions_and_pinned_rollback(self):
        first = self.cli("build-db")
        again = self.cli("build-db")
        self.assertEqual(first["database_version"], again["database_version"])
        original = self.master()
        changed = test_cli.change_field(self.orbits, "00001", 92, 103, "2.8000000")
        changed = test_cli.change_field(changed, "00001", 166, 194, "(1) Renamed")
        changed = "".join(line for line in changed.splitlines(keepends=True) if not line.startswith("J60S01B"))
        self.refresh(changed, None, "--allow-count-decrease")
        self.cli("build-db")
        self.assertEqual(self.cli("query", "--number", 1)["records"][0]["a"], 2.8)
        self.assertEqual(self.cli("query", "--number", 1)["records"][0]["readable_designation"], "(1) Renamed")
        self.assertEqual(self.cli("query", "--id", "mpc:designation:J60S01B")["records"], [])
        # A newly present number with an old discovery must be included without a date watermark.
        smaller = "".join(line for line in changed.splitlines(keepends=True) if not line.startswith("00003"))
        self.refresh(smaller, None, "--allow-count-decrease")
        self.cli("build-db")
        self.refresh(changed)
        self.cli("build-db")
        self.assertEqual(self.cli("query", "--number", 3)["records"], [original[2]])
        rollback = self.cli("build-db", "--snapshot", self.snapshot["snapshot_version"])
        self.assertEqual(rollback["database_version"], first["database_version"])
        self.assertEqual(self.cli("query")["records"], original)

    def test_newly_unsupported_orbit_is_removed(self):
        self.cli("build-db")
        changed = test_cli.change_field(self.orbits, "J60S01B", 70, 79, "1.0100000")
        self.refresh(changed, None, "--allow-count-decrease")
        self.cli("build-db")
        self.assertEqual(self.cli("query")["matched_records"], 8)
        self.assertEqual(self.cli("query", "--packed-designation", "J60S01B")["records"], [])

    def test_open_reader_sees_complete_old_database_after_replacement(self):
        self.cli("build-db")
        with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
            reader.execute("BEGIN")
            self.assertEqual(reader.execute("SELECT a FROM orbits WHERE source_order = 1").fetchone()[0], 2.7655526)
            self.refresh(test_cli.change_field(self.orbits, "00001", 92, 103, "2.8000000"))
            self.cli("build-db")
            self.assertEqual(reader.execute("SELECT a FROM orbits WHERE source_order = 1").fetchone()[0], 2.7655526)
            self.assertEqual(reader.execute("SELECT count(*) FROM objects").fetchone()[0], 9)
            self.assertEqual(self.cli("query", "--number", 1)["records"][0]["a"], 2.8)

    def test_corrupt_inputs_and_invalid_snapshot_retain_previous_database(self):
        self.cli("build-db")
        before = self.checksum()
        self.cli("build-db", "--snapshot", "../../wrong", code=1)
        master = Path(self.snapshot["path"]) / "master.jsonl.gz"
        original = master.read_bytes()
        master.write_bytes(original[:-8])
        self.assertIn("mismatch", self.cli("build-db", code=1)["error"])
        self.assertEqual(before, self.checksum())
        # A valid file checksum must not hide a broken gzip stream.
        manifest_path = master.parent / "snapshot.json"
        saved_manifest = manifest_path.read_bytes()
        manifest = json.loads(saved_manifest)
        manifest["files"][master.name] = {"sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
                                            "bytes": master.stat().st_size}
        manifest_path.write_text(json.dumps(manifest))
        self.cli("build-db", code=1)
        self.assertEqual(before, self.checksum())
        master.write_bytes(original)
        manifest_path.write_bytes(saved_manifest)
        self.cli("build-db")
        self.assertEqual(self.cli("db-info", "--verify")["integrity"], "ok")

    def test_invalid_rows_duplicates_counts_and_gzip_retain_database(self):
        self.cli("build-db")
        before = self.checksum()
        original = self.master()
        cases = [original[:-1], [*original[:-1], original[0]]]
        for key, value in (("a", float("nan")), ("disc", True), ("number", True), ("id", "wrong"), ("epoch", None), ("w", 361), ("orbit_computer", 42)):
            cases.append([{**original[0], key: value}, *original[1:]])
        cases.append([{**original[0], "extra": 1}, *original[1:]])
        for rows in cases:
            with self.subTest(rows=rows[0]):
                self.rewrite_master(rows)
                self.cli("build-db", code=1)
                self.assertEqual(before, self.checksum())
                self.assertFalse(list(self.database.parent.glob(".sqlite-build-*")))
        self.rewrite_master(original)
        self.cli("build-db")

    def test_failures_during_insert_integrity_and_replace_are_recoverable(self):
        self.cli("build-db")
        before = self.checksum()
        for target in ("insert_master", "verify_integrity", "os.replace"):
            script = f'''from unittest.mock import patch
from orrery_data.cli import main
with patch("orrery_data.database.{target}", side_effect=OSError("injected {target} failure")):
    raise SystemExit(main())
'''
            self.assertIn("injected", self.cli("build-db", code=1, script=script)["error"])
            self.assertEqual(before, self.checksum())
            self.assertFalse(list(self.database.parent.glob(".sqlite-build-*")))
            self.assertEqual(self.cli("query")["matched_records"], 9)
        # Commit failure after real inserts, through the CLI's actual transaction boundary.
        script = '''import sqlite3
from unittest.mock import patch
from orrery_data.cli import main
original = sqlite3.connect
class FailingCommit(sqlite3.Connection):
    def __exit__(self, *args):
        raise sqlite3.OperationalError("injected commit failure")
with patch("orrery_data.database.sqlite3.connect", side_effect=lambda *a, **k: original(*a, **k, factory=FailingCommit)):
    raise SystemExit(main())
'''
        self.assertIn("commit failure", self.cli("build-db", code=1, script=script)["error"])
        self.assertEqual(before, self.checksum())
        self.cli("build-db")

    def test_process_killed_before_commit_retains_old_database_and_retry_succeeds(self):
        self.cli("build-db")
        before = self.checksum()
        marker = self.directory / "inserted"
        script = f'''import sys
from pathlib import Path
from unittest.mock import patch
from orrery_data import database
from orrery_data.cli import main
original = database.insert_master
def pause(*args):
    result = original(*args)
    Path({str(marker)!r}).touch()
    sys.stdin.read(1)
    return result
with patch("orrery_data.database.insert_master", side_effect=pause):
    raise SystemExit(main())
'''
        process = subprocess.Popen([sys.executable, "-c", script, "build-db", "--store", str(self.store),
                                    "--database", str(self.database)], cwd=ROOT, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            self.assertEqual(self.cli("query")["matched_records"], 9)
            process.kill()
            process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        self.assertEqual(before, self.checksum())
        self.assertTrue(list(self.database.parent.glob(".sqlite-build-*")))
        self.cli("build-db")
        self.assertEqual(self.cli("db-info", "--verify")["integrity"], "ok")

    def test_writer_lock_sidecars_and_unsafe_outputs(self):
        self.cli("build-db")
        before = self.checksum()
        with (self.database.parent / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIn("Another writer", self.cli("build-db", code=1)["error"])
            self.assertEqual(self.cli("query")["matched_records"], 9)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.database) + suffix)
            sidecar.touch()
            self.assertIn("sidecars", self.cli("build-db", code=1)["error"])
            sidecar.unlink()
        self.cli("build-db", "--database", self.store / "current.json", code=1)
        self.cli("build-db", "--database", Path(self.snapshot["path"]) / "oops.db", code=1)
        link = self.directory / "link.db"
        link.symlink_to(self.database)
        self.cli("build-db", "--database", link, code=1)
        self.assertEqual(before, self.checksum())

    def test_missing_corrupt_and_unsupported_databases_are_not_written(self):
        for command in ("query", "db-info"):
            self.cli(command, code=1)
            self.assertFalse(self.database.parent.exists())
        self.database.parent.mkdir()
        self.database.write_bytes(b"not a SQLite database")
        before = self.checksum()
        self.cli("query", code=1)
        self.cli("db-info", code=1)
        self.assertEqual(before, self.checksum())
        self.cli("build-db")  # A complete rebuild recovers a damaged output.
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA user_version = 999")
        before = self.checksum()
        self.assertIn("Unsupported", self.cli("query", code=1)["error"])
        self.assertEqual(before, self.checksum())
        self.cli("build-db")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("UPDATE metadata SET value = '\"wrong\"' WHERE key = 'database_version'")
            connection.commit()
        self.assertIn("identity", self.cli("db-info", code=1)["error"])

    def test_invalid_query_arguments(self):
        for args in (("--limit", 0), ("--limit", 10001), ("--offset", -1), ("--number", 0),
                     ("--id", "x", "--number", 1), ("--discovered-from", "2026-02-30"),
                     ("--discovered-from", "20260101"), ("--order", "wrong")):
            with self.subTest(args=args):
                self.cli("query", *args, code=2)
        self.cli("query", "--discovery", "missing", "--discovered-from", "2000-01-01", code=1)
        self.cli("query", "--discovered-from", "2001-01-01", "--discovered-to", "2000-01-01", code=1)
        self.assertFalse(self.database.exists())

    def test_altered_provenance_is_rejected_by_every_read_command(self):
        from orrery_data.storage import digest

        self.cli("build-db")
        pristine = self.database.read_bytes()
        with closing(sqlite3.connect(self.database)) as connection:
            original = {k: json.loads(v) for k, v in connection.execute("SELECT key, value FROM metadata")}
        for case in ("notice", "mpcorb_header", "notice_type", "header_type", "snapshot_files",
                     "identity_files", "header_bytes"):
            with self.subTest(case=case):
                self.database.write_bytes(pristine)
                metadata = copy.deepcopy(original)
                if case in ("notice", "mpcorb_header"):
                    # Same length: checksum comparison must detect the alteration.
                    metadata[case] = "X" + metadata[case][1:]
                elif case in ("notice_type", "header_type"):
                    metadata["notice" if case == "notice_type" else "mpcorb_header"] = None
                elif case == "snapshot_files":
                    metadata["snapshot"]["files"]["master.jsonl.gz"]["sha256"] = "0" * 64
                else:
                    metadata["identity"]["files"]["MPCORB-header.txt"]["bytes"] += 1
                    if case == "header_bytes":
                        metadata["snapshot"]["files"] = copy.deepcopy(metadata["identity"]["files"])
                    metadata["database_version"] = "sqlite-v1-" + digest(metadata["identity"])
                with closing(sqlite3.connect(self.database)) as connection:
                    connection.executemany("UPDATE metadata SET value = ? WHERE key = ?",
                                           [(json.dumps(v), k) for k, v in metadata.items()])
                    connection.commit()
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
                before = self.checksum()
                for command in (("db-info", "--verify"), ("db-info",), ("query", "--number", 1)):
                    self.assertIn("provenance", self.cli(*command, code=1)["error"])
                    self.assertEqual(before, self.checksum())
        self.database.write_bytes(pristine)
        self.assertEqual(self.cli("db-info", "--verify")["integrity"], "ok")
        self.assertEqual(self.cli("query")["matched_records"], 9)

    def test_json_exports_unchanged_by_database_build(self):
        output = self.directory / "exports"
        for args in ((), ("--limit", 2)):
            exported = self.cli("export", "--output", output, *args)
            path = Path(exported["path"])
            expected = [row for row in self.master() if row["disc"] is not None]
            if args:
                expected = expected[:2]
            expected.sort(key=lambda row: row["disc"])
            self.assertEqual(json.loads((path / "catalog.json").read_text()),
                             [{k: row[k] for k in test_cli.FIELDS} for row in expected])
        before = self.tree(output)
        self.cli("build-db")
        self.cli("export", "--output", output)
        self.cli("export", "--output", output, "--limit", 2)
        self.assertEqual(self.tree(output), before)


if __name__ == "__main__":
    unittest.main()
