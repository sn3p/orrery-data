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

## Validated 2026-09-12 snapshot

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
