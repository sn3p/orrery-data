"""Database and release readers preserve supplied artifacts on every outcome."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from orrery_data.database import open_database
from orrery_data.storage import file_info, read_json, write_json


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"


class DatabaseReadOnly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = self.directory / "store"
        self.output = self.directory / "releases"
        self.prepared = self.cli("prepare-release", "--store", self.store, "--output", self.output,
                                 "--producer-commit", "1" * 40, "--selected-limit", 2,
                                 "--mpcorb", FIXTURES / "MPCORB.DAT",
                                 "--numbered", FIXTURES / "NumberedMPs.txt")
        self.bundle = Path(self.prepared["path"])

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, "-B", "-m", "orrery_data", *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertFalse(result.stderr if code == 0 else result.stdout)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def state(self, directory):
        result = {}
        for path in (directory, *directory.rglob("*")):
            info = path.lstat()
            payload = (str(path.readlink()) if path.is_symlink() else
                       hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            result[str(path.relative_to(directory))] = (info.st_mode, info.st_size,
                                                        info.st_mtime_ns, info.st_ctime_ns, payload)
        return result

    def reseal(self, bundle):
        manifest = read_json(bundle / "release.json")
        manifest["artifacts"]["orrery.sqlite3"] = file_info(bundle / "orrery.sqlite3")
        write_json(bundle / "release.json", manifest)
        (bundle / "SHA256SUMS").write_text("".join(
            f"{file_info(bundle / name)['sha256']}  {name}\n"
            for name in sorted([*manifest["artifacts"], "release.json"])))

    def readers(self, bundle):
        database = bundle / "orrery.sqlite3"
        return [("db-info", "--database", database),
                ("db-info", "--database", database, "--verify"),
                ("query", "--database", database, "--number", 1),
                ("verify-release", "--bundle", bundle, "--manifest-sha256",
                 file_info(bundle / "release.json")["sha256"])]

    def test_valid_rollback_bundle_is_unchanged_by_repeated_readers(self):
        self.assertEqual((self.bundle / "orrery.sqlite3").read_bytes()[18:20], b"\x01\x01")
        before = self.state(self.bundle)
        for repeat in range(2):
            for args in self.readers(self.bundle):
                with self.subTest(repeat=repeat, command=args[0]):
                    self.cli(*args)
                    self.assertEqual(self.state(self.bundle), before)

    def test_resealed_wal_bundles_reject_without_sidecars_on_success_or_failure_payloads(self):
        for changed_row in (False, True):
            bundle = self.directory / f"wal-{changed_row}"
            shutil.copytree(self.bundle, bundle)
            working = self.directory / f"wal-source-{changed_row}.sqlite3"
            shutil.copyfile(bundle / "orrery.sqlite3", working)
            with closing(sqlite3.connect(working)) as db:
                with closing(db.cursor()) as cursor:
                    self.assertEqual(cursor.execute("PRAGMA journal_mode=WAL").fetchall(), [("wal",)])
                    if changed_row:
                        cursor.execute("UPDATE orbits SET a=a+1 WHERE source_order=1")
                        db.commit()
                    self.assertEqual(cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall(), [(0, 0, 0)])
            # Transfer the fully checkpointed main file after closing its writer.
            # Some SQLite builds retain empty sidecars in the writer directory.
            shutil.copyfile(working, bundle / "orrery.sqlite3")
            self.assertEqual((bundle / "orrery.sqlite3").read_bytes()[18:20], b"\x02\x02")
            self.assertFalse(list(bundle.glob("orrery.sqlite3-*")))
            self.reseal(bundle)
            before = self.state(bundle)
            for args in self.readers(bundle):
                with self.subTest(changed_row=changed_row, command=args[0]):
                    self.assertIn("rollback journal format", self.cli(*args, code=1)["error"])
                    self.assertEqual(self.state(bundle), before)

    def test_wal_candidate_reuse_preserves_pointer_and_candidate(self):
        with closing(sqlite3.connect(self.bundle / "orrery.sqlite3")) as db:
            db.execute("PRAGMA journal_mode=WAL")
        self.reseal(self.bundle)
        write_json(self.output / "latest.json", {"release_version": self.prepared["release_version"],
                                                 "manifest": file_info(self.bundle / "release.json")})
        before = self.state(self.output)
        error = self.cli("prepare-release", "--store", self.store, "--output", self.output,
                         "--producer-commit", "1" * 40, "--selected-limit", 2,
                         "--snapshot", self.prepared["snapshot_version"], code=1)["error"]
        self.assertIn("rollback journal format", error)
        self.assertEqual(self.state(self.output), before)

    def test_invalid_rollback_metadata_rejects_without_mutating_bundle(self):
        with closing(sqlite3.connect(self.bundle / "orrery.sqlite3")) as db:
            db.execute("UPDATE metadata SET value='\"invalid\"' WHERE key='database_version'")
            db.commit()
        self.reseal(self.bundle)
        before = self.state(self.bundle)
        for args in self.readers(self.bundle):
            with self.subTest(command=args[0]):
                self.cli(*args, code=1)
                self.assertEqual(self.state(self.bundle), before)

    def test_live_wal_is_rejected_without_ignoring_committed_changes(self):
        with closing(sqlite3.connect(self.bundle / "orrery.sqlite3")) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("UPDATE orbits SET a=a+1 WHERE source_order=1")
            writer.commit()
            self.assertGreater(Path(str(self.bundle / "orrery.sqlite3") + "-wal").stat().st_size, 0)
            before = self.state(self.bundle)
            for args in self.readers(self.bundle):
                with self.subTest(command=args[0]):
                    self.cli(*args, code=1)
                    self.assertEqual(self.state(self.bundle), before)

    def test_read_transaction_keeps_old_database_when_path_is_replaced(self):
        database = self.directory / "catalog ?#%.sqlite3"
        replacement = self.directory / "replacement.sqlite3"
        shutil.copyfile(self.bundle / "orrery.sqlite3", database)
        shutil.copyfile(database, replacement)
        with closing(sqlite3.connect(replacement)) as writer:
            writer.execute("UPDATE orbits SET a=a+1 WHERE source_order=1")
            writer.commit()
        with open_database(database) as (reader, _):
            original = reader.execute("SELECT a FROM orbits WHERE source_order=1").fetchone()[0]
            replacement.replace(database)
            self.assertEqual(reader.execute("SELECT a FROM orbits WHERE source_order=1").fetchone()[0], original)
        result = self.cli("query", "--database", database, "--number", 1)
        self.assertEqual(result["records"][0]["a"], original + 1)
