# Update and release preparation

This page describes complete immutable exports and their original build-time
integration. For the current shared browser dataset, conditional refresh and
runtime latest discovery, use [browser distribution](browser-delivery.md).

1. Run `orrery-data check --store .data` and inspect each source result. Save
   stdout to an ignored file if an audit of check times is useful. A HEAD
   request uses validators from the selected snapshot, not a previous check.
   Equal Last-Modified or file size alone does not prove unchanged content.
2. Run `orrery-data refresh --store .data`. Downloads use full HTTP 200
   responses, verify available Content-Length and gzip integrity, and validate
   every data row. A source can fail independently; activation requires both.
3. Run `orrery-data export --store .data --output artifacts`, optionally with
   `--limit N` or `--snapshot snapshot-v1-...` to pin an older input version.
   Inspect the printed counts and manifest. `SHA256SUMS` covers every release
   file including the manifest (but does not hash itself).
4. Keep the version directory unchanged. A later, explicitly authorized data
   release can upload its artifacts. This milestone does not publish one.
   The code PR and a data release are different review/publication steps.

Each store/output root has a nonblocking advisory writer lock; concurrent
writers receive an error and can retry. Checks are read-only, and exports read
an immutable pinned snapshot even if another refresh advances current. Work
is staged on the destination filesystem before directory rename and atomic
pointer replacement. A process interrupted before activation leaves the
previous pointer valid; an interruption can leave a hidden staging directory
or an unreferenced complete version. Inspect these only after confirming no
writer is running. Completed versions are never overwritten. This handles
process/download failures; it is not a guarantee against hardware/filesystem
loss. Back up durable stores as needed.

A refresh rejects count decreases by default. Compare source counts,
coverage/exclusions, URLs, hashes, dates and representative records before
accepting an intentional removal with `--allow-count-decrease`. This flag does
not bypass field, gzip or duplicate validation. Syntactically valid incomplete
first snapshots cannot be proven complete from untrusted text alone: use
official sources and expected source hashes/counts when importing an archive.

## Offline and reproducible imports

```sh
orrery-data refresh --store .data \
  --mpcorb /path/to/MPCORB.DAT.gz \
  --numbered /path/to/NumberedMPs.txt.gz \
  --source-metadata /path/to/source-metadata.json
```

Local inputs may be plain or gzip (detected by magic bytes). Both local paths
are required together. Metadata is optional; omit unknown values. The JSON is
keyed by `mpcorb` and `numbered`, with optional `retrieved_at`, `last_modified`,
`etag`, `content_length`, `sha256` (supplied file bytes), and `decoded_sha256`.
Unknown fields and malformed values are rejected before file acquisition or
network access. Expected hashes must be 64 lowercase hexadecimal characters;
null is not a hash assertion. The provenance fields accept null for unknown
values, or these formats:

- `retrieved_at`: a valid calendar timestamp with seconds, optional fractional
  seconds, and `Z` or an explicit `±HH:MM` UTC offset (for example,
  `2026-09-12T14:57:32Z`). Leap-second timestamps are not supported.
- `last_modified`: an [HTTP date](https://www.rfc-editor.org/rfc/rfc9110.html#name-date-time-formats),
  including the two obsolete HTTP date formats accepted for saved responses.
- `etag`: a quoted HTTP entity tag, optionally prefixed with `W/`.
- `content_length`: a nonnegative JSON integer or an ASCII decimal digit string.
  Booleans, fractions, signs and whitespace are rejected.

Without explicit saved provenance, local `retrieved_at` remains null; the
snapshot's `created_at` records generation time separately.
The two expected hashes, when present, are checked before activation. Saved
HTTP Content-Length describes the original response; it may differ from a
losslessly gzipped local archive's byte size. The manifest separately records
the stored bytes and decoded bytes. Paths to local private files are not
written into release manifests. The URLs default to the official MPC endpoints;
use `--mpcorb-url` / `--numbered-url` to explicitly record other upstream URLs.

Tool 0.1.1 corrected unverified local retrieval times; 0.1.2 also rejects
malformed supplied provenance. Refreshing with 0.1.2 creates a new snapshot
identity even for the same source bytes, leaving older immutable snapshots
intact. Reimport older snapshots with valid saved metadata or omit unknown
values; do not infer retrieval time from file timestamps.

## Consumer integration (separate app workspaces)

For local inspection of a prepared snapshot, use `orrery-data build-db`,
`orrery-data db-info --verify` and `orrery-data query`. The database is a
separate complete replacement artifact; existing static export selection and
release preparation stay unchanged. [SQLite workflow and recovery](sqlite.md)
documents pinning, queries, schema versions and failure handling.

Choose a fixed `export-v1-...` and verify the downloaded compressed artifact
against its manifest SHA-256 during the app build. Decompress `catalog.json.gz`
into the app's static build assets, then serve those assets normally. Pin the
trusted manifest/checksum alongside the version; a checksum downloaded from
the same untrusted location alone is not authentication. Keep the MPC header,
notice, master/provenance and consistent manifest available with redistribution.
This pinned build-time path keeps its selected version. Consumers adopting the
separate browser distribution instead discover latest at session start and pin
that verified index for the session; see its coherence and recovery requirements.

Orrery and Orrery3D can choose different limits. The full dated saved export is
a candidate for Orrery3D integration, not a proven browser/GPU capacity. Test
the actual loader, Float32 preparation, transfer, memory, playback and relevant
devices in that app. The nullable-date master is not a drop-in discovery
animation catalog. UI semantics for unknown dates and full-catalog/date-range
views remain deferred. Neither app is changed by the producer milestone.
