"""Exercise actual CLI subprocesses, files and HTTP response boundaries."""

import fcntl
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
FIELDS = {"disc", "epoch", "a", "e", "i", "W", "w", "M", "n"}


def change_field(text, key, start, end, value):
    return "".join(line[:start] + value.ljust(end-start) + line[end:]
                   if line[:7].strip() == key else line
                   for line in text.splitlines(keepends=True))


class CLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.responses = {}
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def respond(self, body):
                cls.requests.append((self.command, self.path))
                config = cls.responses[self.path]
                if config.get("malformed_status"):
                    self.wfile.write(b"NOT HTTP\r\n\r\n")
                    return
                self.send_response(config.get("status", 200))
                for key, value in config.get("headers", {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if body:
                    self.wfile.write(config["body"])
            def do_GET(self):
                self.respond(True)
            def do_HEAD(self):
                self.respond(False)
            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.requests.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = self.directory / "store"
        self.output = self.directory / "exports"
        self.orbits = (FIXTURES / "MPCORB.DAT").read_text()
        self.dates = (FIXTURES / "NumberedMPs.txt").read_text()
        for name, data in (("orbits", gzip.compress(self.orbits.encode(), mtime=0)), ("dates", self.dates.encode())):
            self.responses["/" + name] = {"body": data, "headers": {
                "ETag": '"original"', "Content-Length": str(len(data)),
                "Last-Modified": "Sat, 12 Sep 2026 12:57:00 GMT"}}

    def cli(self, command, *args, code=0, store=None):
        result = subprocess.run([sys.executable, "-m", "orrery_data", command, "--store", str(store or self.store),
                                 *map(str, args)], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout if code in (0, 2) else result.stderr)

    def http_args(self):
        return ["--mpcorb-url", self.url + "/orbits", "--numbered-url", self.url + "/dates"]

    def refresh(self, orbits=None, dates=None, *args, code=0, store=None):
        orbit_file = self.directory / "MPCORB.DAT"
        date_file = self.directory / "NumberedMPs.txt"
        orbit_file.write_text(self.orbits if orbits is None else orbits)
        date_file.write_text(self.dates if dates is None else dates)
        return self.cli("refresh", "--mpcorb", orbit_file, "--numbered", date_file, *args, code=code, store=store)

    def export(self, *args, code=0):
        return self.cli("export", "--output", self.output, *args, code=code)

    def tree(self, directory):
        return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob("*") if p.is_file()}

    def records(self, result):
        path = Path(result["path"])
        with gzip.open(path / "master.jsonl.gz", "rt") as stream:
            return [json.loads(line) for line in stream]

    def test_full_profiles_and_provenance(self):
        result = self.refresh()
        self.assertEqual(result["counts"]["master_records"], 9)
        self.assertEqual(result["counts"]["known_discovery"], 6)
        self.assertEqual(result["counts"]["missing_discovery"], 3)
        master = self.records(result)
        self.assertEqual(len({r["id"] for r in master}), 9)
        self.assertEqual([r["number"] for r in master[:6]], [1, 2, 3, 100000, 360000, 620000])
        self.assertEqual(master[-1]["id"], "mpc:designation:_EH0000")
        self.assertEqual(master[0]["a"], 2.7655526)
        self.assertEqual(master[0]["orbit_reference"], "MPO980521")
        self.assertEqual(master[0]["orbit_computer"], "Veres")
        exported = self.export()
        directory = Path(exported["path"])
        catalog = json.loads((directory / "catalog.json").read_bytes())
        self.assertEqual(len(catalog), 6)
        self.assertEqual([r["disc"] for r in catalog], sorted(r["disc"] for r in catalog))
        self.assertTrue(all(set(r) == FIELDS for r in catalog))
        self.assertTrue(all(type(v) in (float, int) for r in catalog for v in r.values()))
        self.assertEqual(gzip.decompress((directory / "catalog.json.gz").read_bytes()), (directory / "catalog.json").read_bytes())
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertIsNone(manifest["selection"]["limit"])
        self.assertEqual(manifest["counts"]["discovery_export"], 6)
        self.assertEqual(manifest["sources"]["mpcorb"]["sha256"], hashlib.sha256(self.orbits.encode()).hexdigest())
        self.assertEqual(manifest["sources"]["mpcorb"]["acquisition"], "local")
        for line in (directory / "SHA256SUMS").read_text().splitlines():
            sha, name = line.split("  ")
            self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), sha)
        self.assertIn("proper attribution", (directory / "MPCORB-header.txt").read_text())

    def test_limit_before_date_sort_and_tie_order(self):
        # Make the third MPC object earlier than the selected first two.
        dates = self.dates.replace("1804 09 01", "1799 01 01").replace("1802 03 28", "1801 01 01")
        self.refresh(dates=dates)
        result = self.export("--limit", 2)
        catalog = json.loads((Path(result["path"]) / "catalog.json").read_text())
        master = self.records(self.cli("refresh", *self.http_args()))
        self.assertEqual([r["a"] for r in catalog], [r["a"] for r in master[:2]])
        manifest = json.loads((Path(result["path"]) / "manifest.json").read_text())
        self.assertEqual(manifest["exclusions"]["selection_limit"], 4)

    def test_unchanged_refresh_and_export_are_immutable(self):
        first = self.refresh()
        self.export()
        before_store, before_output = self.tree(self.store), self.tree(self.output)
        again = self.refresh()
        self.assertEqual(first["snapshot_version"], again["snapshot_version"])
        self.assertEqual(again["status"], "unchanged")
        self.export()
        self.assertEqual(before_store, self.tree(self.store))
        self.assertEqual(before_output, self.tree(self.output))

    def test_fresh_stores_and_gzip_wrappers_produce_same_artifacts(self):
        first = self.refresh()
        exported = self.export()
        fresh = self.directory / "fresh"
        gz_orbit, gz_dates = self.directory / "orbits.gz", self.directory / "dates.gz"
        gz_orbit.write_bytes(gzip.compress(self.orbits.encode(), mtime=100))
        gz_dates.write_bytes(gzip.compress(self.dates.encode(), mtime=200))
        second = self.cli("refresh", "--mpcorb", gz_orbit, "--numbered", gz_dates, store=fresh)
        self.assertEqual(first["snapshot_version"], second["snapshot_version"])
        result = self.cli("export", "--output", self.directory / "fresh-exports", store=fresh)
        self.assertEqual(exported["data_version"], result["data_version"])
        self.assertEqual(exported["artifacts"], result["artifacts"])

    def test_updates_removals_stable_identity_and_pinned_snapshot(self):
        first = self.refresh()
        first_export = self.export()
        modified = change_field(self.orbits, "00001", 92, 103, "  2.8000000")
        second = self.refresh(modified)
        self.assertNotEqual(first["snapshot_version"], second["snapshot_version"])
        self.assertEqual(self.records(first)[0]["id"], self.records(second)[0]["id"])
        self.assertEqual(self.records(second)[0]["a"], 2.8)
        smaller = "".join(line for line in modified.splitlines(keepends=True) if not line.startswith("J60S01B"))
        before = self.tree(self.store)
        self.assertIn("decreased", self.refresh(smaller, code=1)["error"])
        self.assertEqual(before, self.tree(self.store))
        third = self.refresh(smaller, None, "--allow-count-decrease")
        self.assertEqual(third["counts"]["master_records"], 8)
        self.assertEqual(len(self.records(third)), 8)
        pinned = self.export("--snapshot", first["snapshot_version"])
        self.assertEqual(first_export["data_version"], pinned["data_version"])

    def test_optional_magnitude_fields_and_calendar_conversion(self):
        text = change_field(self.orbits, "00001", 8, 19, "")
        text = change_field(text, "00001", 20, 25, "K0011")
        result = self.refresh(text)
        self.assertEqual(self.records(result)[0]["epoch"], 2451544.5)

    def test_mpc_rounded_360_degree_endpoint_is_preserved(self):
        # Saved 2026-09-12 MPCORB row K17S44L prints w as exactly 360.00000.
        text = change_field(self.orbits, "00001", 37, 46, "360.00000")
        result = self.refresh(text)
        self.assertEqual(self.records(result)[0]["w"], 360.0)
        catalog = json.loads((Path(self.export()["path"]) / "catalog.json").read_text())
        self.assertEqual(catalog[0]["w"], 360.0)

    def test_nondated_numbered_object_stays_in_master(self):
        dates = "".join(self.dates.splitlines(keepends=True)[1:])
        result = self.refresh(dates=dates)
        self.assertIsNone(self.records(result)[0]["disc"])
        self.assertEqual(self.export()["counts"]["discovery_export"], 5)

    def test_non_elliptic_orbit_is_explicit_exclusion(self):
        text = change_field(self.orbits, "J60S01B", 70, 79, "1.0100000")
        result = self.refresh(text)
        self.assertEqual(result["counts"]["unsupported_orbits"], 1)
        self.assertEqual(result["counts"]["master_records"], 8)

    def test_invalid_inputs_retain_previous_snapshot_and_exports(self):
        self.refresh()
        self.export()
        previous_store, previous_output = self.tree(self.store), self.tree(self.output)
        rows = self.orbits.splitlines(keepends=True)
        cases = [
            ("", self.dates), ("<html>server error</html>", self.dates),
            (self.orbits[:-80], self.dates), (self.orbits + rows[-1], self.dates),
            (change_field(self.orbits, "00001", 70, 79, "nan"), self.dates),
            (change_field(self.orbits, "00001", 70, 79, "-0.1"), self.dates),
            (change_field(self.orbits, "00001", 92, 103, "0"), self.dates),
            (change_field(self.orbits, "00001", 20, 25, "K262V"), self.dates),
            (change_field(self.orbits, "00001", 0, 7, "???????"), self.dates),
            (change_field(self.orbits, "00001", 166, 194, "(2) Wrong"), self.dates),
            (self.orbits, ""), (self.orbits, self.dates[:-60]),
            (self.orbits, self.dates + self.dates.splitlines(keepends=True)[0]),
            (self.orbits, self.dates.replace("1801 01 01", "1801 02 30")),
        ]
        for orbits, dates in cases:
            with self.subTest(orbits=orbits[:15], dates=dates[:15]):
                self.refresh(orbits, dates, code=1)
                self.assertEqual(previous_store, self.tree(self.store))
                self.assertEqual(previous_output, self.tree(self.output))

    def test_http_refresh_captures_validators_and_readonly_check(self):
        result = self.cli("refresh", *self.http_args())
        snapshot = json.loads((Path(result["path"]) / "snapshot.json").read_text())
        self.assertEqual(snapshot["sources"]["mpcorb"]["etag"], '"original"')
        self.assertEqual(snapshot["sources"]["mpcorb"]["compression"], "gzip")
        for source in snapshot["sources"].values():
            self.assertRegex(source["retrieved_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        before = self.tree(self.store)
        checked = self.cli("check", *self.http_args())
        self.assertEqual(checked["status"], "unchanged")
        self.assertEqual(before, self.tree(self.store))
        self.responses["/dates"]["headers"]["ETag"] = '"changed"'
        checked = self.cli("check", *self.http_args())
        self.assertEqual(checked["sources"]["numbered"]["status"], "changed")
        self.assertEqual(checked["sources"]["mpcorb"]["status"], "unchanged")
        self.assertEqual(before, self.tree(self.store))

    def test_check_without_baseline_creates_nothing(self):
        result = self.cli("check", *self.http_args(), code=2)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(self.store.exists())

    def test_malformed_http_status_does_not_skip_second_source(self):
        self.cli("refresh", *self.http_args())
        self.responses["/orbits"]["malformed_status"] = True
        checked = self.cli("check", *self.http_args(), code=2)
        self.assertEqual(checked["sources"]["mpcorb"]["status"], "unknown")
        self.assertEqual(checked["sources"]["numbered"]["status"], "unchanged")

    def test_check_missing_weak_validators_and_http_failure(self):
        self.cli("refresh", *self.http_args())
        before = self.tree(self.store)
        self.responses["/dates"]["headers"] = {}
        self.responses["/orbits"]["status"] = 500
        result = self.cli("check", *self.http_args(), code=2)
        self.assertTrue(all(row["status"] == "unknown" for row in result["sources"].values()))
        self.assertEqual(before, self.tree(self.store))
        self.responses["/orbits"]["status"] = 200
        for response in self.responses.values():
            response["headers"]["ETag"] = 'W/"weak"'
        fresh = self.directory / "weak"
        self.cli("refresh", *self.http_args(), store=fresh)
        self.assertEqual(self.cli("check", *self.http_args(), store=fresh, code=2)["status"], "unknown")

    def test_download_failures_retain_valid_state(self):
        self.cli("refresh", *self.http_args())
        self.export()
        before, before_output = self.tree(self.store), self.tree(self.output)
        original = self.responses["/orbits"]["body"]
        for response in [
            {"body": b"server error", "status": 503},
            {"body": original[:-20], "headers": {"Content-Length": str(len(original))}},
            {"body": original[:-20]},
            {"body": b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\x07"},
            {"body": b"<html>not MPC</html>"},
            {"body": b"", "status": 206},
        ]:
            with self.subTest(response=str(response)[:50]):
                self.responses["/orbits"] = response
                self.cli("refresh", *self.http_args(), code=1)
                self.assertEqual(before, self.tree(self.store))
                self.assertEqual(before_output, self.tree(self.output))
        self.responses["/orbits"] = {"body": original}
        self.responses["/dates"] = {"body": b"failed", "status": 500}
        self.cli("refresh", *self.http_args(), code=1)
        self.assertEqual(before, self.tree(self.store))

    def test_source_hash_assertions_and_saved_metadata(self):
        metadata = self.directory / "metadata.json"
        metadata.write_text(json.dumps({"mpcorb": {"sha256": "0" * 64}}))
        self.refresh(None, None, "--source-metadata", metadata, code=1)
        self.assertFalse((self.store / "current.json").exists())
        metadata.write_text(json.dumps({"mpcorb": {
            "sha256": hashlib.sha256(self.orbits.encode()).hexdigest(),
            "decoded_sha256": hashlib.sha256(self.orbits.encode()).hexdigest(),
            "last_modified": "Sat, 12 Sep 2026 12:57:00 GMT", "retrieved_at": "2026-09-12T14:00:00Z"}}))
        result = self.refresh(None, None, "--source-metadata", metadata)
        manifest = json.loads((Path(result["path"]) / "snapshot.json").read_text())
        self.assertEqual(manifest["sources"]["mpcorb"]["retrieved_at"], "2026-09-12T14:00:00Z")
        exported = json.loads((Path(self.export()["path"]) / "manifest.json").read_text())
        self.assertEqual(exported["sources"]["mpcorb"]["retrieved_at"], "2026-09-12T14:00:00Z")

    def test_unknown_metadata_fields_fail_before_acquisition(self):
        self.refresh()
        self.export()
        before, before_output = self.tree(self.store), self.tree(self.output)
        metadata = self.directory / "metadata.json"
        for source in ("mpcorb", "numbered"):
            for field in ("decoded_sha265", "sha265", "retrievedAt"):
                with self.subTest(source=source, field=field):
                    metadata.write_text(json.dumps({source: {field: "0" * 64}}))
                    result = self.refresh(None, None, "--source-metadata", metadata, code=1)
                    self.assertIn(source, result["error"])
                    self.assertIn(field, result["error"])
                    self.assertEqual(before, self.tree(self.store))
                    self.assertEqual(before_output, self.tree(self.output))
        # Invalid metadata must fail before opening a network connection, too.
        self.responses["/orbits"]["status"] = 500
        result = self.cli("refresh", *self.http_args(), "--source-metadata", metadata, code=1)
        self.assertIn("retrievedAt", result["error"])
        self.assertEqual(before, self.tree(self.store))

    def test_malformed_metadata_fails_before_local_or_http_acquisition(self):
        self.refresh()
        self.export()
        before, before_output = self.tree(self.store), self.tree(self.output)
        metadata = self.directory / "metadata.json"
        invalid = {
            "sha256": [None, True, 123, {}, [], "", "0" * 63, "g" * 64, "A" * 64, "0" * 64 + "\n"],
            "decoded_sha256": [None, 123, {}, "0" * 65, "z" * 64],
            "retrieved_at": [True, 123, {}, [], "", "yesterday", "2026-09-12",
                             "2026-09-12T14:00:00", "2026-02-30T14:00:00Z",
                             "2026-09-12T24:00:00Z", "2026-09-12T14:00:00+02:60",
                             "2026-09-12T14:00:00Z junk"],
            "content_length": [True, False, -1, 1.5, {}, [], "", "-1", "+1", "1.0", " 1", "١", "1\n"],
            "last_modified": [True, 123, {}, [], "", "yesterday", "2026-09-12T14:00:00Z",
                              "Mon, 30 Feb 2026 12:00:00 GMT", "Sat, 12 Sep 2026 12:57:00 GMT junk"],
            "etag": [True, 123, {}, [], "", "unquoted", 'w/"tag"', '"tag"\r\nX: value', '"two tags", "bad"'],
        }
        for source in ("mpcorb", "numbered"):
            for field, values in invalid.items():
                for value in values:
                    with self.subTest(source=source, field=field, value=value):
                        metadata.write_text(json.dumps({source: {field: value}}))
                        # Nonexistent inputs prove metadata is checked before local copying.
                        for inputs in (["--mpcorb", self.directory / "absent-orbits",
                                        "--numbered", self.directory / "absent-dates"], self.http_args()):
                            error = self.cli("refresh", *inputs, "--source-metadata", metadata, code=1)["error"]
                            self.assertIn(f"{source}.{field}:", error)
        self.assertEqual(self.requests, [])
        self.assertEqual(before, self.tree(self.store))
        self.assertEqual(before_output, self.tree(self.output))
        fresh = self.directory / "fresh-store"
        self.cli("refresh", *self.http_args(), "--source-metadata", metadata, store=fresh, code=1)
        self.assertFalse(fresh.exists())

    def test_valid_metadata_survives_snapshot_and_export(self):
        metadata = self.directory / "metadata.json"
        variants = [
            {"retrieved_at": None, "content_length": None, "last_modified": None, "etag": None},
            {"retrieved_at": "2026-09-12T14:00:00Z", "content_length": 0,
             "last_modified": "Sat, 12 Sep 2026 12:57:00 GMT", "etag": '"strong"'},
            {"retrieved_at": "2026-09-12T16:00:00.123456+02:00", "content_length": "80686609",
             "last_modified": "Sunday, 06-Nov-94 08:49:37 GMT", "etag": 'W/"weak"'},
            {"retrieved_at": "2026-09-12t09:00:00-05:00", "content_length": "000",
             "last_modified": "Sun Nov  6 08:49:37 1994", "etag": '""'},
        ]
        for index, fields in enumerate(variants):
            with self.subTest(fields=fields):
                # Fresh versions retain the first acquisition's provenance.
                self.store = self.directory / f"store-{index}"
                self.output = self.directory / f"exports-{index}"
                values = {}
                for source, data in (("mpcorb", self.orbits), ("numbered", self.dates)):
                    sha = hashlib.sha256(data.encode()).hexdigest()
                    values[source] = dict(fields, sha256=sha, decoded_sha256=sha)
                metadata.write_text(json.dumps(values))
                result = self.refresh(None, None, "--source-metadata", metadata)
                snapshot = json.loads((Path(result["path"]) / "snapshot.json").read_text())
                exported = json.loads((Path(self.export()["path"]) / "manifest.json").read_text())
                for manifest in (snapshot, exported):
                    for source in values:
                        for field, value in fields.items():
                            self.assertEqual(manifest["sources"][source][field], value)
                        self.assertEqual(manifest["sources"][source]["sha256"], values[source]["sha256"])
                        self.assertEqual(manifest["sources"][source]["decoded"]["sha256"], values[source]["decoded_sha256"])

    def test_valid_metadata_keeps_http_acquisition_and_hash_assertions(self):
        metadata = self.directory / "metadata.json"
        values = {}
        for source, route, decoded in (("mpcorb", "/orbits", self.orbits), ("numbered", "/dates", self.dates)):
            values[source] = {"sha256": hashlib.sha256(self.responses[route]["body"]).hexdigest(),
                              "decoded_sha256": hashlib.sha256(decoded.encode()).hexdigest(),
                              "retrieved_at": None, "last_modified": None, "etag": None, "content_length": None}
        metadata.write_text(json.dumps(values))
        result = self.cli("refresh", *self.http_args(), "--source-metadata", metadata)
        snapshot = json.loads((Path(result["path"]) / "snapshot.json").read_text())
        exported = json.loads((Path(self.export()["path"]) / "manifest.json").read_text())
        for manifest in (snapshot, exported):
            for source in values:
                self.assertEqual(manifest["sources"][source]["etag"], '"original"')
                self.assertIsNotNone(manifest["sources"][source]["retrieved_at"])
        before, before_output = self.tree(self.store), self.tree(self.output)
        for source in values:
            for field in ("sha256", "decoded_sha256"):
                original = values[source][field]
                values[source][field] = "0" * 64
                metadata.write_text(json.dumps(values))
                error = self.cli("refresh", *self.http_args(), "--source-metadata", metadata, code=1)["error"]
                self.assertIn(source, error)
                self.assertIn("checksum", error)
                self.assertEqual(before, self.tree(self.store))
                self.assertEqual(before_output, self.tree(self.output))
                values[source][field] = original

    def test_local_retrieval_time_is_unknown_without_saved_provenance(self):
        result = self.refresh()
        snapshot = json.loads((Path(result["path"]) / "snapshot.json").read_text())
        exported = json.loads((Path(self.export()["path"]) / "manifest.json").read_text())
        for manifest in (snapshot, exported):
            self.assertIsNotNone(manifest["created_at"])
            for name in ("mpcorb", "numbered"):
                self.assertIsNone(manifest["sources"][name]["retrieved_at"])
                self.assertEqual(manifest["sources"][name]["acquisition"], "local")

    def test_corrupt_snapshot_does_not_replace_previous_export(self):
        result = self.refresh()
        self.export()
        before = self.tree(self.output)
        (Path(result["path"]) / "mpcorb.input").write_text("corrupt")
        self.assertIn("mismatch", self.export("--limit", 2, code=1)["error"])
        self.assertEqual(before, self.tree(self.output))

    def test_corrupt_existing_export_is_not_silently_reused(self):
        self.refresh()
        result = self.export()
        (Path(result["path"]) / "catalog.json").write_text("[]")
        self.assertIn("mismatch", self.export(code=1)["error"])

    def test_incomplete_release_cannot_be_reused(self):
        self.refresh()
        result = self.export()
        directory = Path(result["path"])
        sums = directory / "SHA256SUMS"
        original = sums.read_bytes()
        sums.unlink()
        self.assertIn("SHA256SUMS", self.export(code=1)["error"])
        sums.write_bytes(original)
        manifest = directory / "manifest.json"
        contents = json.loads(manifest.read_text())
        del contents["artifacts"]["catalog.json"]
        manifest.write_text(json.dumps(contents))
        self.assertIn("required artifacts", self.export(code=1)["error"])

    def test_incomplete_snapshot_provenance_cannot_be_exported(self):
        result = self.refresh()
        directory = Path(result["path"])
        manifest = directory / "snapshot.json"
        original = json.loads(manifest.read_text())
        contents = json.loads(manifest.read_text())
        del contents["sources"]["numbered"]
        manifest.write_text(json.dumps(contents))
        self.assertIn("both", self.export(code=1)["error"])
        original["sources"]["numbered"]["decoded"]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(original))
        self.assertIn("source identity", self.export(code=1)["error"])

    def test_snapshot_count_inconsistency_is_rejected(self):
        result = self.refresh()
        path = Path(result["path"]) / "snapshot.json"
        manifest = json.loads(path.read_text())
        manifest["counts"]["known_discovery"] += 1
        path.write_text(json.dumps(manifest))
        self.assertIn("counts", self.export(code=1)["error"])

    def test_second_writer_fails_without_changing_state(self):
        self.refresh()
        before = self.tree(self.store)
        with (self.store / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.refresh(code=1)
            self.assertIn("Another writer", result["error"])
        self.assertEqual(before, self.tree(self.store))

    def test_invalid_arguments(self):
        for limit in ("0", "-1", "no"):
            result = subprocess.run([sys.executable, "-m", "orrery_data", "export", "--limit", limit],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
        self.assertIn("both", self.cli("refresh", "--mpcorb", FIXTURES / "MPCORB.DAT", code=1)["error"])
        self.assertIn("refresh first", self.export(code=1)["error"])
        self.assertIn("No valid", self.export("--snapshot", "../../escape", code=1)["error"])


if __name__ == "__main__":
    unittest.main()
