# Verification

Fast offline regression suite:

```sh
python3 -m unittest discover -s tests -v
```

Tests spawn the actual CLI and a local HTTP server. They cover all/limited
exports, exact consumer fields, stable tie order, MPC packed-number/provisional
identities, optional magnitude fields, Julian dates, missing discoveries,
unsupported-orbit counts, updates/removals, pinned snapshots, immutable reuse,
gzip determinism, stored corruption, missing provenance, count-decrease guards,
concurrent writers, expected source hashes, validator outcomes, malformed HTTP,
partial downloads, corrupt/truncated gzip and previous-output retention.
CI runs this suite on Python 3.11–3.13 with no live-source dependency.

SQLite regressions also use real CLI processes: complete field/provenance
round trips, case-sensitive authority lookups, distinct W/w values, null dates,
inclusive date filters, stable order/pagination, repeat/update/removal/rollback,
existing readers across replacement, writer locks, invalid/corrupt inputs and
outputs, read-only missing-file behavior, and injected insert/commit/integrity/
replacement failures. A process is killed after real inserts before commit to
verify retained output and retry recovery. Existing full/limited JSON exports
are compared before and after database construction.

## Full saved SQLite regression

Build from the already validated 2026-09-12 store and compare against its
retained full and 100k JSON exports:

```sh
python3 scripts/validate_saved_database.py \
  --store /path/to/retained/store \
  --reference-exports /path/to/retained/releases \
  --work-dir /path/to/sqlite-validation \
  --report .context/sqlite-validation.json
```

This checks the expected master SHA-256, invokes the real builder twice,
compares every field of every database row against the master after both builds,
checks SQLite integrity/foreign keys and complete dated/null counts, exercises
CLI queries, verifies embedded provenance/credits and read-only file hashes,
and regenerates both JSON exports to compare exact retained artifact hashes.
No network fetch occurs. Allow several minutes and at least 2 GB free for the
database, replacement stage and exports. The final database/exports remain in
`--work-dir`, outside Git. The existing saved-source test below independently
checks upstream parsing and the original importer algorithm.

The full saved-source regression is opt-in because its roughly 100 MB of
compressed inputs do not belong in Git. Use the original 2026-09-12 MPCORB gzip,
the losslessly archived NumberedMPs gzip, and their saved `.headers` files:

```sh
python3 scripts/validate_saved_snapshot.py \
  --sources /path/to/2026-09-12-sources \
  --work-dir /path/to/local-validation \
  --report .context/saved-validation.json
```

It verifies expected input hashes; invokes real refresh/export commands;
checks the 1,563,495-row orbital master, 895,910 dated records and 667,585 null
dates; compares every output numeric field against the original importer's
algorithm for both all-record and 100k exports; verifies output checksums; and
rebuilds in a fresh temporary store to compare artifact hashes. The final first
store and release directories remain in `--work-dir`; the second deterministic
rebuild is removed. Allow several minutes and at least 2 GB of free disk space.
The script stops at a failed assertion/CLI command and emits a passed report
only after every check completes.

Saved-source counts refer to that dated snapshot, not a current global object
count. Generation timings measure this producer only. No transfer, browser,
GPU preparation, playback, mobile or positional-accuracy benchmark is implied.
Those remain required in a later app integration workspace.

## SQLite milestone result

Tool 0.2.0 passed all 43 tests (the original 29 plus 14 SQLite regressions)
on Python 3.12.7/macOS arm64. Independent review found no actionable issues.
The installed wheel's CLI passed refresh/build/query/integrity/export checks
from outside the source directory. Both full database builds matched every
field of every saved master row. SQLite 3.51.0 integrity and foreign-key checks
passed; full/100k JSON payload hashes remain identical to the retained 0.1.2
exports. The final database contains 1,563,495 records (895,910 dated and
667,585 null), occupies 377,061,376 bytes, and was built in about 23–25 seconds
on this machine. Timings are observations, not performance guarantees.
See [SQLite verification results and hashes](sqlite-validation-result.json).
The generated database is local and untracked; no data release is published.

## First milestone: validated 2026-09-12 snapshot

All checks above passed with tool 0.1.2 on Python 3.12.7/macOS arm64. Both
live MPC strong ETags matched the saved sources at 2026-09-12T18:28:56Z.
The 29 CLI tests and installed-package refresh/export upgrade check passed.
Provenance regressions cover unknown fields, malformed known values, null
local retrieval times and valid metadata round trips into both manifests.
Read-only checks also cover numeric Content-Length equivalence, leading zeros,
zero-length changes, missing lengths and malformed HTTP lengths for both sources.
The 0.1.1-to-0.1.2 upgrade rejects previously accepted malformed metadata,
creates a corrected snapshot and preserves the old snapshot unchanged. The initial
independent review and full-catalog checks are also recorded in this PR.
See [machine-readable results and full SHA-256 hashes](validation-result.json).

| Artifact | Records | Bytes |
|---|---:|---:|
| Master JSONL gzip | 1,563,495 | 92,289,968 |
| Full discovery JSON | 895,910 | 118,824,557 |
| Full discovery JSON gzip | 895,910 | 34,197,622 |
| Limited discovery JSON | 100,000 | 13,258,794 |
| Limited discovery JSON gzip | 100,000 | 3,867,423 |

The master has 667,585 null discovery dates and no unsupported orbits in this
snapshot. Source record `K17S44L` prints `w=360.00000`; the parser and regression
suite preserve this rounded endpoint. Full and limited exports match every
numeric field from the original importer, with the limit applied before sort.
These are locally prepared artifacts, not a published data release or completed
app/GPU integration.
