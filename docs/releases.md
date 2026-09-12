# Versioned data release preparation

Tool 0.3.0 prepares a complete, immutable release candidate locally or in a
manually dispatched GitHub Actions run. It does not create tags, GitHub
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
`--snapshot` cannot be combined with source acquisition arguments. Invalid
CLI arguments return 2; preparation/verification failures return 1; success
returns 0. Structured command results/errors use the existing JSON boundary.

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

`release.json` lists every payload file with relative path, SHA-256 and bytes,
and records counts, exclusions through the snapshot/export manifests,
profile selections, generation time, producer/runtime versions and the baseline
used during first preparation. The root `SHA256SUMS` also covers `release.json`
and each nested manifest/checksum file. Neither contains private local source
paths. Preserve the MPC header, notice and matching master when redistributing
catalogs; the software license does not relicense upstream data.

| Identity | Meaning |
|---|---|
| Producer `tool_version` and `commit` | Code that prepared the bundle; currently 0.3.0 |
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
filesystem. Every artifact and cross-file identity is verified before the
candidate directory is installed; only then does `latest.json` advance atomically.
An already existing candidate is verified before reuse. A damaged existing
candidate or latest pointer fails closed and is never silently overwritten.
Prepare into a fresh output root to recover while retaining evidence of damage.
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

The file must contain exactly four nonnegative integer counts. For the inspected
2026-09-12 source snapshot these were:

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

The verifier checks the exact file inventory, disallows symlinks and unexpected
paths, verifies all hashes/sizes and checksum lists, checks schema/version/count
and source/credit consistency across SQLite and every export, runs SQLite
integrity/FK checks, and checks catalog fields, finite values, date order,
counts and gzip/plain equivalence. It does not fetch upstream sources or rerun
the original importer; the full saved-data validator separately compares every
SQLite field and the reference JSON payload hashes. Run verification before
consuming downloaded data and keep the directory read-only during verification
and use. Hashes detect damage; they are not authenticity signatures. Obtain the
expected manifest hash from a trusted channel; omitting it checks internal
consistency only.

## Manual GitHub Actions workflow

`Prepare data release` has only a `workflow_dispatch` trigger, `contents: read`
permissions and pinned action commits. It runs tests, refreshes from MPC,
prepares/independently verifies the bundle, then uploads that directory as a
**seven-day Actions review artifact**. It includes all files needed for local
verification; it excludes the input store and abandoned stages. Names include
the release version, run ID and attempt. Missing files fail the upload. Inputs
are passed through environment variables and parsed as data by the same
`scripts/prepare_release_workflow.py` exercised against local HTTP fixtures.

The required `baseline_counts` JSON supplies the four counts above because
GitHub-hosted runners have no persistent source store or previous candidate.
`selected_limits` defaults to `100000`; `allow_count_decrease` defaults to false.
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
