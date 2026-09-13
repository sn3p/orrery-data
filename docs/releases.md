# Versioned data release preparation

Introduced in tool 0.3.0, release preparation builds a complete, immutable
candidate locally or in a manually dispatched GitHub Actions run. It does not create tags, GitHub
Releases, release assets, a server, or app updates. The proposed eventual
public download destination is GitHub Releases; publication is a separate task.

## Prepare and inspect locally

Use a clean checkout of the exact producer commit and install that code:

```sh
python3 -m pip install .
git diff --exit-code HEAD --
orrery-data prepare-release --producer-commit "$(git rev-parse HEAD)"
```

This refreshes both MPC sources, validates complete inputs, builds SQLite,
exports the full discovery profile and a first-100000 profile, then verifies
and installs the complete bundle under `artifacts/releases/release-v1-…/`.
JSON on stdout includes the absolute bundle path, counts, versions, profile
selection and the SHA-256/size of `release.json`. `latest.json` names the last
successfully prepared candidate, not a published release.

`--producer-commit` must be the full lowercase 40-character Git SHA of the
code actually used. It is caller-supplied provenance, not a signature or an
automatic assertion that an installed package matches the current directory.
Use the same interpreter/environment for installation and invocation. The
manual workflow checks out and records the exact event SHA and checks tracked
checkout cleanliness before preparing.

Local source imports use the same validation/provenance options as `refresh`:

```sh
orrery-data prepare-release --producer-commit "$(git rev-parse HEAD)" \
  --mpcorb /path/MPCORB.DAT.gz --numbered /path/NumberedMPs.txt.gz \
  --source-metadata /path/source-metadata.json
```

For an offline rebuild, explicitly pin a validated, retained snapshot. This
reads and verifies the store in place without refreshing it:

```sh
orrery-data prepare-release --producer-commit "$(git rev-parse HEAD)" \
  --store /path/retained/store --snapshot snapshot-v1-FULL_HASH \
  --output /path/candidates --selected-limit 100000 --selected-limit 250000
```

The full profile is always present. Repeated `--selected-limit` options replace
the default selected profile; values are deduplicated and sorted. Each is the
first N eligible MPCORB records **before** stable discovery-date sorting, just
like the existing `export --limit`. All supported master records, including
null discovery dates, remain in SQLite and in each export's master file.
An explicit `--snapshot` must be `snapshot-v1-` followed by 64 lowercase
hexadecimal characters. Empty or malformed pins fail before locking or reading
snapshots; they never fall back to the current snapshot. `--snapshot` cannot be
combined with source acquisition arguments, including an explicit `--timeout`.
Argparse syntax/type errors return 2. Semantic argument combinations and
preparation/verification failures return 1 with a JSON error; success returns 0.

## Bundle layout and versions

```text
release-v1-<logical-build-hash>/
  release.json
  SHA256SUMS
  snapshot.json
  orrery.sqlite3
  MPCORB-header.txt
  NOTICE.txt
  exports/
    full/
      catalog.json
      catalog.json.gz
      master.jsonl.gz
      manifest.json
      SHA256SUMS
      MPCORB-header.txt
      NOTICE.txt
    first-100000/
      ... same unchanged export format ...
```

Each export is self-contained and retains its existing format, attribution,
master, manifest and checksum file. This duplicates the compressed master
across profiles deliberately so a profile can be copied as a unit. Raw source
files are retained in the source store, not shipped inside the candidate.
The snapshot manifest carries their raw/decoded hashes, sizes, URLs, timestamps
and HTTP validators. SQLite embeds the same snapshot and per-orbit provenance.

`release.json` lists every payload under a relative path. Each root artifact
record contains exactly `sha256` and `bytes`. The manifest also records counts,
exclusions through the snapshot/export manifests,
profile selections, generation time, producer/runtime versions and the baseline
used during first preparation. The root `SHA256SUMS` also covers `release.json`
and each nested manifest/checksum file. Neither contains private local source
paths. Preserve the MPC header, notice and matching master when redistributing
catalogs; the software license does not relicense upstream data.

Schema 1 requires the complete top-level manifest fields. Its `created_at` is a
valid UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ`), as is the snapshot's `created_at`.
Snapshot loading validates the calendar date, time and type before exports or
database builds can propagate it; standalone release verification applies the
same check. Generation ordering permits up to
five seconds of backward wall-clock movement between database/export generation
and release assembly; larger contradictions fail with a clock diagnostic.
Actual recorded timestamps are preserved rather than clamped or replaced with
one shared start time. `runtime` contains Python, SQLite and zlib version
strings; the SQLite and catalog zlib versions must match the contained metadata.
These are the producing runtime's versions and may differ from the verifier's.
New compression metadata uses the loaded zlib runtime version, including in
snapshot and export manifests; pinned snapshots retain their original metadata.
Each export's copied master compression object must match the snapshot exactly,
even when newly generated catalogs use a different zlib runtime.
`preparation` records a boolean `allow_count_decrease`, the four explicit
`baseline_counts` or null, and the complete prior snapshot `previous_counts` or
null. Counts must be nonnegative integers, prior snapshot counts must reconcile,
and a decrease against either recorded baseline requires a true override.
Standalone verification and candidate reuse enforce the same requirements.

| Identity | Meaning |
|---|---|
| Producer `tool_version` and `commit` | Code that prepared the bundle; current tool 0.4.0 |
| `dataset_version` (`data-v1-…`) | Hash of the decoded MPCORB and NumberedMPs source hashes in fixed order; independent of producer code, acquisition time, gzip wrappers and profile limits |
| `snapshot_version` | Existing snapshot identity, including its parser tool/schema version; preserved when pinning an older snapshot |
| JSON schema 1 / SQLite schema 1 | Existing independent payload contracts, unchanged |
| Export / database versions | Existing logical output identities, including the producer tool and snapshot |
| Release schema 1 / `release_version` | Bundle format and hash of snapshot, dataset identity, producer commit/tool, schema versions, selected limits and notice hash |
| Artifact SHA-256 / manifest SHA-256 | Exact prepared bytes, including retained generation/acquisition metadata |

Logical identities use the repository's compact JSON encoding and documented
field order in `identity`. They are not hashes of the finished SQLite bytes.
A source-content identity alone does not specify parser behavior: pin the
release version **and** manifest hash for an exact consumable candidate.
A fresh rebuild may have different SQLite bytes or generation timestamps;
Python/SQLite/zlib versions are recorded. No cross-runtime byte reproducibility
is promised. Same-output reruns verify and reuse the first complete candidate,
preserving its first-acquisition/generation provenance. To exercise serialization
again, use a fresh output root. Tool 0.3.0 changes logical export/snapshot/database
IDs for newly built data; it leaves JSON fields, selection rules and payload
bytes unchanged for the same retained master and gzip runtime.

## Failure safety and count policy

Preparation uses an advisory output lock and a private stage on the destination
filesystem. Without `--snapshot`, `--store` and `--output` must resolve to different
directories; preparation rejects identical roots before locking or refreshing.
Pinned offline preparation may use the same directory for both.
The output must also be outside named release candidates and their subdirectories.
A marker file (`snapshot.json`, `manifest.json` or `release.json`) in the exact
output directory also prevents writing there. An unrelated marker in a parent
directory does not prevent using its children. Use the containing release root
as `--output`; invalid placements fail before locking.
Every artifact and cross-file identity is verified before the
candidate directory is installed; only then does `latest.json` advance atomically.
An already existing candidate is verified before reuse. A damaged existing
candidate or latest pointer fails closed and is never silently overwritten.
Prepare into a fresh output root to recover while retaining evidence of damage.
If `latest.json` references a deleted or incompatible bundle, restore that bundle
or use a fresh output root with `--baseline-counts` from a previously inspected
dataset. Retain the previous manifest/count evidence when reclaiming payload space;
do not delete the pointer to silently discard the count baseline. Web exports and
release candidates require separate output roots because their `latest.json`
pointers have different formats. An identical rerun reuses the previous bundle's
verification result instead of verifying the same candidate twice.
Interrupted `.release-*` stages can be removed after confirming no preparation
is running. Do not remove a live `.lock`. A verified candidate left behind by
failure to update the pointer is reusable on retry. Concurrent writers fail
without waiting. Readers pin a candidate directory rather than following
`latest.json` repeatedly across calls.

Source refresh and bundle installation are separate transactions. A successful
refresh may advance `store/current.json` even if a later build, baseline check,
export or verification fails; the previous release candidate remains intact.
Retry the new snapshot or explicitly pin a retained one. This is complete
replacement, including orbital corrections, late-numbered old discoveries and
removals; discovery-date watermarks and append-only updates are not used.

Existing store counts are guarded by `refresh`. Preparation also compares
orbital, discovery-source, supported-master and known-discovery counts with
the prior candidate in that output root, and with an optional explicit baseline:

```sh
orrery-data prepare-release --producer-commit "$(git rev-parse HEAD)" \
  --baseline-counts /path/baseline-counts.json
```

The file accepts the four nonnegative integer counts below or the complete
nine-field counts object emitted by `refresh`/`prepare-release`. Complete counts
must reconcile; preparation records only the four guarded baseline fields.
For the inspected 2026-09-12 source snapshot these were:

```json
{"orbital_records":1563495,"discovery_records":895910,"master_records":1563495,"known_discovery":895910}
```

These are dated observations, not current minimums built into the tool. Use
counts from the last inspected dataset for later runs. Any decrease requires
source inspection and an explicit `--allow-count-decrease` override. A pinned
older snapshot is subject to the same candidate/baseline checks. Counts alone
cannot prove completeness or reject a malformed but plausible first source;
use trusted expected source hashes where available.

## Verify after transfer

Copy the entire candidate directory, or download and extract its Actions review
artifact. The original store and producer checkout are unnecessary when the
compatible CLI is installed:

```sh
orrery-data verify-release --bundle /path/downloaded-candidate \
  --manifest-sha256 EXPECTED_RELEASE_JSON_SHA256
orrery-data db-info --database /path/downloaded-candidate/orrery.sqlite3 --verify
orrery-data query --database /path/downloaded-candidate/orrery.sqlite3 --number 1
cd /path/downloaded-candidate
shasum -a 256 -c SHA256SUMS
```

The verifier checks the bundle inventory, verifies hashes/sizes and checksum
lists, checks schema/version/count
and source/credit consistency across SQLite and every export, runs SQLite
integrity/FK checks, and checks catalog fields, finite values, date order,
counts and gzip/plain equivalence. It streams the complete master, validates each
record, compares every SQLite field and source order, and checks every catalog
against the corresponding first-N selection and stable sort from those records.
The stored SQLite schema, constraints and indexes must match schema 1.
It does not fetch upstream sources or rerun
the original importer. Catalog validation enforces schema 1's supported elliptic
orbital ranges, including the valid printed 360-degree endpoint. Release identity
schema versions must be integers; boolean and floating-point values are rejected.
The identity and its nested producer object must have exactly the schema-1
fields; missing or additional fields are rejected before identity hashing.
The full saved-data validator independently repeats the every-field comparison
and checks the historical reference JSON payload hashes. Run verification before
consuming downloaded data and keep the directory read-only during verification
and use. Integrity checks detect accidental damage, truncation and transfer
corruption; they do not establish authenticity. A party able to write inside the
bundle, source store or output root and regenerate hashes can produce a bundle
that verifies; that is out of scope. Obtain the `release.json` SHA-256 from a
trusted channel and pass it as `--manifest-sha256` to establish authenticity.
Omitting that pin checks internal consistency only. See the
[trust statement](release-contract.md#trust-and-evidence).

## Manual GitHub Actions workflow

`Prepare data release` has only a `workflow_dispatch` trigger, `contents: read`
permissions and pinned action commits. It runs tests, refreshes from MPC,
prepares/independently verifies the bundle, then uploads that directory as a
**seven-day Actions review artifact**. It includes all files needed for local
verification; it excludes the input store and abandoned stages. Names include
the release version, run ID and attempt. Missing files fail the upload. Inputs
are passed through environment variables and parsed as data by the same
`scripts/prepare_release_workflow.py` exercised against local HTTP fixtures.

The required `baseline_counts` JSON supplies the four counts above (or a complete
nine-field counts object) because GitHub-hosted runners have no persistent source
store or previous candidate.
`selected_limits` defaults to `100000`; `allow_count_decrease` defaults to false.
Whitespace around comma-separated limits and empty separators are ignored;
at least one positive integer is required. Local helper execution requires
`RELEASE_PRODUCER_COMMIT` and `RELEASE_BASELINE_COUNTS`. Missing required variables
and invalid inputs fail before creating the work directory or output files.
There is no schedule, release/tag creation, release asset upload, or deployment.
A code merge does not publish data. Review artifacts expire and require GitHub
access; they are not a durable public distribution endpoint. Future publication
must choose and retain an exact verified candidate and manifest hash, inspect
source/count changes and establish a separately authorized release policy.

GitHub requires the dispatched workflow to exist on the default branch. Local
helper execution and workflow linting can be verified before merge; actual
GitHub-hosted dispatch, live MPC access from that runner, artifact upload and
subsequent download remain unverified until an authorized post-merge run.
The proposed eventual GitHub Release assets must respect the current provider
per-file limit; no release is created by this milestone.

Provider references inspected 2026-09-12: [manual dispatch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow),
[Actions artifact behavior and retention](https://github.com/actions/upload-artifact),
[release assets](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

The complete acceptance contract and its verification boundaries are recorded in
[release-contract.md](release-contract.md). All writers, including the refresh
store and workflow work directory, reject destinations inside named
snapshot/export/release candidates or at marker-bearing target directories
before locking or writing. The local workflow
helper locks its work directory across baseline preparation, build and verification.
