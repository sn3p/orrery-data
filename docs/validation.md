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
`--work-dir`, outside Git. Each invocation regenerates both JSON profiles under
a fresh `exports-*` directory, including when `--work-dir` is reused; the report's
`export_directory` identifies that directory relative to `--work-dir`. Previous
exports are retained but cannot bypass the current serialization check.
The existing saved-source test below independently
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

Tool 0.2.0 passed all 47 tests (the original 29, 15 SQLite regressions and three
saved-validator regressions)
on Python 3.12.7/macOS arm64. Independent review found no actionable issues.
The installed wheel's CLI passed refresh/build/query/integrity/export checks
from outside the source directory. Both full database builds matched every
field of every saved master row. SQLite 3.51.0 integrity and foreign-key checks
passed; full/100k JSON payload hashes remain identical to the retained 0.1.2
exports. The final database contains 1,563,495 records (895,910 dated and
667,585 null), occupies 377,061,376 bytes, and was built in about 24–27 seconds
on this machine. Timings are observations, not performance guarantees.
See [SQLite verification results and hashes](sqlite-validation-result.json).
The generated database is local and untracked; no data release is published.
The validator regressions exercise real export CLI subprocesses in a reused
work directory, proving that both profiles regenerate and cached artifacts
cannot conceal a failure in the current serializer.
The committed report is regenerated with that validator and checked for its
fresh-export directory metadata. Provenance regressions also confirm that
altered MPC header/notice text or file manifests fail every database read
command even when SQLite's structural integrity check passes.

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

## Release preparation regression

Tool 0.3.0 adds CLI coverage for complete/copyable bundles, exact unchanged
JSON profiles, local/HTTP provenance, code/data identity separation, pinned
snapshots, immutable reruns, count decreases, removals/corrections, input and
output damage, mixed versions/metadata, interruption, failure/retry and locks.
Additional CLI regressions cover schema-1 records and manifests, count-decrease
overrides, preservation of bundles when an output path is at or beneath a named
candidate, and differing zlib build/runtime versions. An unrelated `manifest.json`
in a parent directory permits fixture refresh/export; a marker in the exact
writable target directory still prevents writing there.
Empty or malformed explicit snapshot pins fail before locks or snapshot reads,
preserve existing source/candidate bytes and create no output for missing roots.
Valid pins remain offline and independent of the mutable current pointer.
The same empty-pin safeguard applies to `export` and `build-db`. Nested snapshot,
export, artifact and pointer shapes produce contextual JSON errors, including
malformed gzip streams and missing bundle inventory. Snapshot count-field errors
are distinguished from inconsistent arithmetic. Regressions also cover clock
reversal within the five-second tolerance, complete count baselines, explicit
pinned timeouts, workflow preflight and reuse without duplicate verification.
CLI regressions reject identical source/output roots before
refreshing or writing, retain existing sources and candidates, and verify
recovery with distinct roots and pinned preparation with a shared root.
The actual manual-workflow helper runs against a local HTTP server, including
reruns, invalid inputs, source failures, empty failure outputs and invocation
from another working directory. `actionlint` validates the workflow YAML.

Full saved release validation (no network and no mutation of reference inputs):

```sh
python3 scripts/validate_saved_release.py \
  --store /path/retained/store \
  --reference-exports /path/retained/releases \
  --work-dir /path/release-validation \
  --report /path/release-validation.json
```

Run from committed producer code with assertions enabled. The release validator
captures that commit's exact Git blobs into a private temporary directory, then
runs its validator, comparison helper and producer subprocesses from that
snapshot. Python and system libraries remain those of the invoking machine.
The final report is written to a temporary file in the report directory, flushed
and synced before atomic replacement. Complete fixture runs inject partial-write,
flush, sync and replacement failures, checking preservation of prior evidence,
temporary-file cleanup and successful retry.
The release validator
rejects `python -O`, `python -OO` and enabled `PYTHONOPTIMIZE` before validation
or output writes, so disabled checks cannot produce a passing report.
It selects the retained snapshot version recorded in the committed release result
directly from `snapshots/`, independently of missing, changed or malformed
`current.json`, and verifies that snapshot, all nine pinned counts and its pinned
master hash. A missing
or corrupted retained snapshot fails before creating the work directory; another
current snapshot cannot substitute even when its master/export payloads match.
Reference export manifests are checked before preparation: full and 100k limits
must exist, shapes must validate, and duplicate limits must describe identical
artifact payloads. Missing, malformed or conflicting references produce a concise
path/limit diagnostic and preserve any prior report.
Every invocation prepares into a fresh output
root, compares every SQLite field against all 1,563,495 master records, compares
all full/100k export payload hashes to the retained references, repeats
preparation in the same directory, copies the candidate independently,
verifies it with a pinned manifest hash, exercises known/null/rounded-endpoint
queries, and confirms damaged copied bytes are rejected. The complete candidate
is retained; the temporary copied candidate is removed. `--clone-copy` uses
macOS APFS copy-on-write for the independent transfer copy when space is tight.
Normally allow at least 2 GB free. This validator reads the already validated
saved snapshot; the earlier saved-source validator covers original parsing.
The release report identifies the exact producing commit and artifact hashes.
GitHub-hosted dispatch/upload/download and app rendering are separate,
unverified surfaces; no manual workflow run or data publication is implied.

The historical full saved candidate matched every SQLite field and every retained full/100k export
payload hash. Its original fresh preparation succeeded; the rerun encountered
host disk exhaustion while writing the latest pointer. After reclaiming space
with identical APFS-cloned payloads (reference hashes unchanged), two real CLI
reruns preserved the candidate manifest exactly, every-row/hash comparisons
passed again, and standalone copied-bundle queries/verification and deliberate
corruption rejection passed. This recovery is retained explicitly in the
[machine-readable release results](release-validation-result.json). Hosted
manual dispatch/upload/download remains unverified; no data release was published.

That JSON is historical evidence for producer commit
`d3e80458e8e1ee2cb55bccfa0e70e6453685ea62`, assembled from the original run and its
recovery continuation. Its `recovery` section was added to document that event;
it is not a verbatim report emitted by today's validator, which also records
`python`. This historical result does not establish validation of later producer commits.
Keep later saved-data reports with their own producer identity and release
version; preserve this historical report alongside them.


## Integrity and review scope

Validation follows the [trust statement](release-contract.md#trust-and-evidence).
Integrity checks detect accidental damage, truncation and transfer corruption.
They do not establish authenticity. A party able to write inside the bundle,
source store or output root and regenerate hashes can produce a bundle that
verifies; that is out of scope. Obtain the `release.json` SHA-256 from a trusted
channel and pass it as `--manifest-sha256` to establish authenticity. Without
that pin, verification checks internal consistency only.

The saved validator rejects work/report destinations that overlap retained inputs,
producer code or immutable candidates. Reports may live inside a dedicated work
directory. Transfer verification owns a unique temporary subtree, so retrying a
run never deletes an unrelated `downloaded-copy` directory.

Classify review findings against this scope before changing validation. A
resealed bundle or an attacker writing into the local tree does not call for
additional validation layers. Real operability bugs receive a regression at the
failing entry point and a fix. Test results and saved-data reports record the
commit and checks performed; hosted workflow execution remains separate evidence.
