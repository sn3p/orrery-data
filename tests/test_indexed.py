"""Exercise the optional derivative through real CLI/files and common fixtures."""

import copy
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from orrery_data.formats import DataError
from orrery_data.indexed import export_indexed, MAX_CHUNK_BYTES
from orrery_data.storage import encode, file_info, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"


class IndexedCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "store"
        self.exports = self.root / "exports"
        self.output = self.root / "indexed"

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, "-B", "-m", "orrery_data", *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertFalse(result.stderr if code == 0 else result.stdout)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def prepare(self, limit=None, empty=False):
        # The first three records share a date, straddling 300-byte boundaries.
        dates = (FIXTURES / "NumberedMPs.txt").read_text()
        for old in ("1801 01 01", "1802 03 28", "1804 09 01"):
            dates = dates.replace(old, "2000 01 01")
        if empty:
            dates = "".join(f"({900001+i})" + line[8:] for i, line in enumerate(dates.splitlines(keepends=True)))
        (self.root / "dates.txt").write_text(dates)
        self.cli("refresh", "--store", self.store, "--mpcorb", FIXTURES / "MPCORB.DAT",
                 "--numbered", self.root / "dates.txt")
        args = ["--limit", limit] if limit is not None else []
        return Path(self.cli("export", "--store", self.store, "--output", self.exports, *args)["path"])

    def indexed(self, source, cap=300, code=0):
        return self.cli("export-indexed", "--export", source, "--output", self.output,
                        "--chunk-bytes", cap, code=code)

    def verify(self, result, code=0):
        return self.cli("verify-indexed", "--bundle", result["path"],
                        "--index-sha256", result["pin"]["sha256"], code=code)

    def tree(self, root):
        return {str(p.relative_to(root)): file_info(p) for p in root.rglob("*") if p.is_file()}

    def test_cli_round_trip_preserves_source_and_bounded_ties(self):
        source = self.prepare()
        before = self.tree(self.exports), self.tree(self.store)
        result = self.indexed(source)
        bundle = Path(result["path"])
        self.assertEqual(self.verify(result), result)
        self.assertEqual((self.tree(self.exports), self.tree(self.store)), before)
        self.assertFalse((self.output / "latest.json").exists())
        self.assertEqual(self.tree(source), self.tree(bundle / "full"))
        index = read_json(bundle / "index.json")
        rows = read_json(source / "catalog.json")
        recovered, position = [], 0
        for chunk in index["chunks"]:
            self.assertEqual(chunk["start"], position)
            raw = (bundle / chunk["url"]).read_bytes()
            self.assertLessEqual(len(raw), 300)
            self.assertEqual(gzip.decompress((bundle / chunk["gzip"]["url"]).read_bytes()), raw)
            batch = json.loads(raw)
            self.assertEqual(len(batch), chunk["end"] - chunk["start"])
            self.assertEqual((batch[0]["disc"], batch[-1]["disc"]), (chunk["first_disc"], chunk["last_disc"]))
            recovered.extend(batch)
            position = chunk["end"]
        self.assertEqual(recovered, rows)
        self.assertEqual(position, len(rows))
        self.assertTrue(any(a["last_disc"] == b["first_disc"] for a, b in zip(index["chunks"], index["chunks"][1:])))
        for date, count in index["date_counts"]:
            self.assertEqual(count, sum(row["disc"] <= date for row in rows))
        self.assertEqual(self.indexed(source), result)

    def test_chunk_size_changes_source_pin_but_not_catalog(self):
        source = self.prepare()
        first, second = self.indexed(source), self.indexed(source, 700)
        self.assertEqual(first["catalog_id"], second["catalog_id"])
        self.assertNotEqual(first["pin"]["sha256"], second["pin"]["sha256"])
        self.verify(first)
        self.verify(second)

    def test_limit_preserves_source_selection_before_sorting(self):
        full = self.prepare()
        limited = Path(self.cli("export", "--store", self.store, "--output", self.exports, "--limit", 2)["path"])
        result = self.indexed(limited)
        index = read_json(Path(result["path"]) / "index.json")
        self.assertEqual(index["selection"]["limit"], 2)
        self.assertEqual(index["counts"]["discovery_export"], 2)
        self.assertEqual(index["exclusions"]["selection_limit"], 4)
        self.assertEqual(index["date_counts"], [[2451544.5, 2]])
        self.assertNotEqual(read_json(full / "catalog.json")[:2], read_json(limited / "catalog.json"))
        self.verify(result)

    def test_empty_known_date_profile(self):
        result = self.indexed(self.prepare(empty=True))
        index = read_json(Path(result["path"]) / "index.json")
        self.assertEqual(index["counts"]["missing_discovery"], 9)
        self.assertEqual(index["counts"]["discovery_export"], 0)
        self.assertEqual(index["chunks"], [])
        self.assertEqual(index["date_counts"], [])
        self.assertFalse((Path(result["path"]) / "chunks").exists())
        self.verify(result)

    def test_shared_consumer_bundles_and_date_range_vectors(self):
        root = FIXTURES / "consumer-v1"
        cases = read_json(root / "cases.json")
        for name, bundle in cases["bundles"].items():
            with self.subTest(bundle=name):
                result = self.cli("verify-indexed", "--bundle", root / bundle["path"],
                                  "--index-sha256", bundle["pin"]["sha256"])
                self.assertEqual(result["catalog_id"], bundle["catalog_id"])
                regenerated = self.indexed(root / bundle["path"] / "full")
                rows = read_json(root / bundle["path"] / "full/catalog.json")
                index = read_json(root / bundle["path"] / "index.json")
                actual = read_json(Path(regenerated["path"]) / "index.json")
                expected = copy.deepcopy(index)
                # Preserve the existing serialized format while allowing a newer
                # producer or different zlib runtime to change its gzip bytes.
                expected["producer"]["tool_version"] = actual["producer"]["tool_version"]
                for old, new in zip(expected["chunks"], actual["chunks"]):
                    old["gzip"].update({key: new["gzip"][key] for key in ("sha256", "bytes")})
                self.assertEqual(encode(actual), encode(expected))
                for query in cases["queries"]:
                    if query["bundle"] == name:
                        self.assertEqual(sum(row["disc"] <= query["through"] for row in rows), query["requiredEnd"])
                        preceding = [count for date, count in index["date_counts"] if date <= query["through"]]
                        self.assertEqual(preceding[-1] if preceding else 0, query["requiredEnd"])
                for read in cases["reads"]:
                    if read["bundle"] == name:
                        self.assertEqual(list(range(read["start"], read["end"])), read["expectedOrdinals"])
                        self.assertLessEqual(read["end"], len(rows))

    def test_corrupt_or_missing_chunk_fails_and_does_not_overwrite(self):
        source = self.prepare()
        result = self.indexed(source)
        bundle = Path(result["path"])
        first = bundle / read_json(bundle / "index.json")["chunks"][0]["url"]
        original = first.read_bytes()
        for corruption in (b"[]\n", None):
            with self.subTest(corruption=corruption):
                if corruption is None:
                    first.unlink()
                else:
                    first.write_bytes(corruption)
                before = self.tree(self.output)
                error = self.verify(result, code=1)
                if corruption is None:
                    self.assertIn("Indexed chunks file inventory mismatch", error["error"])
                self.indexed(source, code=1)
                self.assertEqual(self.tree(self.output), before)
                self.assertFalse(list(self.output.glob(".indexed-*")))
                first.write_bytes(original)
        self.assertEqual(self.indexed(source), result)

    def test_gzip_and_index_corruption_reject_even_without_pin(self):
        result = self.indexed(self.prepare())
        bundle = Path(result["path"])
        index = read_json(bundle / "index.json")
        first = bundle / index["chunks"][0]["gzip"]["url"]
        original = first.read_bytes()
        first.write_bytes(original[:-2])
        self.verify(result, code=1)
        first.write_bytes(original)
        for field, value in (("contract_version", 2), ("contract_version", True), ("schema_version", 1.0),
                             ("encoding", "binary"), ("date_counts", []), ("chunk_bytes", True)):
            with self.subTest(field=field, value=value):
                broken = copy.deepcopy(index)
                broken[field] = value
                write_json(bundle / "index.json", broken)
                self.cli("verify-indexed", "--bundle", bundle, code=1)
        broken = copy.deepcopy(index)
        broken["chunks"][1]["start"] += 1
        write_json(bundle / "index.json", broken)
        self.cli("verify-indexed", "--bundle", bundle, code=1)
        write_json(bundle / "index.json", index)
        self.verify(result)

    def test_wrong_pin_fails_before_catalog_use(self):
        result = self.indexed(self.prepare())
        error = self.cli("verify-indexed", "--bundle", result["path"], "--index-sha256", "0" * 64, code=1)
        self.assertIn("trusted pin", error["error"])

    def test_missing_or_non_directory_bundle_has_clear_error(self):
        ordinary = self.root / "ordinary-file"
        ordinary.write_text("not a bundle")
        linked = self.root / "dangling-link"
        linked.symlink_to(self.root / "absent-target")
        for path in (self.root / "missing", ordinary, ordinary / "nested", linked):
            with self.subTest(path=path):
                result = self.cli("verify-indexed", "--bundle", path, code=1)
                self.assertEqual(result["error"], "Indexed bundle must be a real directory")

    def test_index_field_errors_precede_full_payload_verification(self):
        result = self.indexed(self.prepare())
        bundle = Path(result["path"])
        index = read_json(bundle / "index.json")
        (bundle / "full/catalog.json").write_bytes(b"corrupt payload must not be read first")
        for missing in (False, True):
            broken = copy.deepcopy(index)
            if missing:
                del broken["date_counts"]
            else:
                broken["unknown"] = 1
            write_json(bundle / "index.json", broken)
            error = self.cli("verify-indexed", "--bundle", bundle, code=1)
            self.assertEqual(error["error"], "Invalid indexed fields")
        write_json(bundle / "index.json", index)
        self.assertIn("Checksum or size mismatch", self.verify(result, code=1)["error"])

    def test_invalid_caps_and_oversize_record_leave_previous_output(self):
        source = self.prepare()
        previous = self.indexed(source)
        before = self.tree(self.output)
        for cap in (1, MAX_CHUNK_BYTES + 1):
            self.indexed(source, cap, code=1)
            self.assertEqual(self.tree(self.output), before)
        for cap in (True, 1.0, 0, -1):
            with self.assertRaises(DataError):
                export_indexed(source, self.output, cap)
        self.verify(previous)

    def test_corrupt_source_has_no_output_and_corrected_retry(self):
        source = self.prepare()
        path = source / "catalog.json"
        original = path.read_bytes()
        path.write_bytes(b"[]\n")
        self.indexed(source, code=1)
        self.assertFalse(self.output.exists())
        path.write_bytes(original)
        self.verify(self.indexed(source))

    def test_derivative_protects_existing_candidates_and_input_roots(self):
        source = self.prepare()
        result = self.indexed(source)
        before = self.tree(self.root)
        for output in (source, source / "nested", self.exports, Path(result["path"]), Path(result["path"]) / "nested"):
            self.cli("export-indexed", "--export", source, "--output", output, code=1)
        self.cli("export", "--store", self.store, "--output", result["path"], code=1)
        self.cli("refresh", "--store", Path(result["path"]) / "nested", "--mpcorb", FIXTURES / "MPCORB.DAT",
                 "--numbered", FIXTURES / "NumberedMPs.txt", code=1)
        self.assertEqual(self.tree(self.root), before)

    def test_unexpected_file_and_linked_chunk_reject(self):
        result = self.indexed(self.prepare())
        bundle = Path(result["path"])
        extra = bundle / "chunks/extra.json"
        extra.write_text("[]")
        path = bundle / "chunks/000000.json"
        original = path.read_bytes()
        path.write_bytes(b"corrupt payload must not be read before inventory")
        self.assertIn("Indexed chunks file inventory mismatch", self.verify(result, code=1)["error"])
        extra.unlink()
        saved = self.root / "saved.json"
        shutil.move(path, saved)
        path.symlink_to(saved)
        self.assertIn("regular non-symlink files", self.verify(result, code=1)["error"])
        path.unlink()
        path.write_bytes(original)
        self.verify(result)


if __name__ == "__main__":
    unittest.main()
