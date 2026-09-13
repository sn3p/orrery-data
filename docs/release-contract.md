# Release verification contract

This is the acceptance checklist for release schema 1. Changes to producers,
readers or validation scripts must preserve these guarantees and their boundary
tests. A review result applies to a specific commit and the checks performed.

## Trust and evidence

`verify-release` checks internal consistency of a stable, read-only directory.
It rejects malformed manifests and inconsistent representations even when their
checksum lists have been regenerated. An independently obtained manifest SHA-256
also pins the exact bundle bytes. Neither self-hashes nor schema checks establish
upstream authenticity, truthful acquisition times, the actual producer commit,
or the real runtime used. Source URLs, HTTP headers, generation times and runtime
versions are recorded claims with validated types and internal relationships.
The raw upstream files are absent from the bundle; source parsing/completeness
requires retained-source validation or trusted source hashes and inspected counts.

The saved-data validator separately pins the retained source identity, master
hash and all nine counts, runs exact committed code, compares every SQLite field
and the historical full/100k export hashes, and exercises retries and transfer.
Local Git/Python executables and system libraries are trusted. This does not
promise protection from a hostile user rewriting files during verification,
hardware failure, resource exhaustion, or a compromised operating system.

## Manifest structure

All JSON objects reject duplicate keys. Every object below has an exact field
set except source records, where `resolved_url` is optional for local acquisition
and required for HTTP acquisition. Integers exclude booleans and floating point.
Numeric payload values must be finite; JSON NaN and infinity are rejected.
Schema definitions live in `orrery_data/contracts.py`; serialization stays in
the producer.

The tests maintain an independent field inventory for all four manifest kinds.
Fixtures include full and selected exports, null and populated preparation
baselines/previous counts, and HTTP/local acquisition. Recursive missing-field,
extra-field and wrong-type mutations exercise the schema checker directly;
those probe counts describe direct checks, not total boundary coverage or proof
of completeness. Release metadata mutations also run through `verify_release`
on resealed files, including supported dynamic map entries and populated
preparation objects, and assert that verification writes nothing. Snapshot,
SQLite and cached-export mutations exercise their respective readers/activation
boundaries. Positive compatibility cases and independently resealed relational
and payload cases supplement structural mutations.

| Object | Required fields / rules |
| --- | --- |
| Release | `release_version`, `release_schema_version`, `identity`, `dataset_identity`, `created_at`, `runtime`, `sources`, `counts`, `database_version`, `profiles`, `preparation`, `artifacts` |
| Release identity | Schema integers, dataset/snapshot IDs, producer commit/tool, sorted unique positive selected limits, notice SHA-256 |
| Dataset identity | Exactly the two decoded source hashes under `sources` |
| Producer | Full lowercase 40-hex commit; nonempty tool version |
| Runtime | Nonempty version strings for Python, SQLite and zlib; SQLite/catalog zlib must match contained metadata |
| Snapshot | Snapshot identity/version, schema/tool, UTC creation timestamp, two source records, nine counts, two file records, exclusions and gzip metadata |
| Snapshot identity | Schema integer, nonempty tool version, two decoded source hashes |
| Source | HTTP(S) URL, acquisition mode, nullable retrieval time/validators/length, raw hash/bytes, input compression, decoded hash/bytes; optional resolved HTTP(S) URL |
| Counts | Exactly nine nonnegative integers; orbital = master + unsupported = numbered + unnumbered; master = known + missing; discovery = known + unmatched; known cannot exceed numbered |
| Exclusions | Exact nested objects and integer values agreeing with counts; exports additionally contain selection exclusions |
| File | Exactly lowercase 64-hex SHA-256 and nonnegative integer bytes |
| Export artifact | File fields; master/catalog artifacts additionally require their exact profile and nonnegative integer records |
| Export | Export identity/version, snapshot/schema/tool, UTC creation timestamp, selection, sources, counts, exclusions, compression and five artifacts |
| Selection | Discovery profile, positive integer or null limit, first-known-date source selection and stable ascending discovery sort |
| Database metadata | Exact metadata/identity fields, integer schema versions, valid snapshot, timestamp/runtime, header and notice text bound to hashes |
| Release profile | Export ID, nonnegative integer records and exact selection |
| Preparation | Nullable four-count baseline, nullable nine-count previous snapshot, boolean decrease override; non-overridden decreases reject |
| Pointers | Exactly the supported version key; release pointer also has exact manifest file information; export and release roots reject each other's pointer types |

Every content-derived ID must equal its documented ordered-JSON identity hash.
Readers reconstruct that schema-defined order, including nested identity objects,
before hashing. Reordering JSON object keys preserves the canonical ID; hashing
an alternative key order cannot introduce a second ID for the same identity.
Repeated metadata must agree with the authoritative object, with exact JSON
types. Export selection/counts, copied source/master/header/notice provenance,
database files and release profiles must describe the same snapshot. Generation
ordering permits the documented five-second clock reversal; timestamps themselves
remain the actual recorded values. Recorded versions need not equal the verifier's.

Stored source loading verifies both raw and decoded bytes against their hashes;
local saved HTTP Content-Length may describe the upstream representation rather
than locally recompressed bytes. For HTTP acquisition, Content-Length must agree
with recorded raw bytes. Other HTTP header strings remain recorded observations. Generated master/catalog files contain exactly one complete gzip member with
zero mtime, no optional header fields or stored filename, and no trailing bytes.
The complete frame (including its trailer) is checked; upstream source gzip is
not subject to this generated-artifact framing rule. Compression level and the
actual producing runtime remain recorded claims.

## Payload and state guarantees

| Invariant | Boundary / acceptance |
| --- | --- |
| Fixed layout | Bundle contains exactly the supported files and directories; no symlinks or special files; manifest paths cannot choose files to open |
| Transport | Every byte count/hash and checksum list agrees; trusted manifest pin rejects resealing |
| Master | Strict schema-v1 records, valid MPC identities and consistent displayed numbers, finite supported orbits, exact master/known/missing counts |
| SQLite | Complete rollback-journal artifact without sidecars, checked before SQLite opens; read-only transaction; supported metadata/schema, integrity/FK checks; all rows and source order equal the master |
| Discovery catalogs | Full/selected rows equal the corresponding source-order selection from SQLite/master, then stable discovery sort; exact nine fields and gzip/plain equality |
| Pins | Only omitted snapshot selects current; explicit malformed/empty pins fail at all consumers; dangling pointers fail instead of discarding baselines; pointer targets must be regular files before reads |
| Inputs and outputs | Writers reject destinations at/below immutable snapshots, exports or release candidates, including aliases; source refresh cannot use a candidate as its writable store; workflow append paths must be outside every resolved writer root and distinct from each other |
| Activation | Validate API source values/pairs before writes; build in private stages, verify, then atomically activate; failure preserves previous candidate/pointer and permits retry; concurrent writers are excluded |
| Existing artifacts | Snapshot and export candidates must be real directories with exactly their documented regular, non-symlink files, checked before content reads; reuse validates content and provenance before changing a pointer |
| Saved evidence | Report/output paths cannot overwrite inputs or candidates; copied data uses a private per-run path; report replacement is atomic and previous evidence survives failures |
| Code provenance | Saved validator reads raw committed blobs with replacements disabled and runs helpers/subprocesses in isolation; a dirty producer is rejected |

Hosted workflow dispatch, runner MPC access and artifact upload/download require
separate post-merge verification. Release publication, HTTP hosting and app
integration remain outside this milestone.

Workflow append destinations reserve a portable namespace: paths differing only
by case or Unicode NFC normalization count as overlaps even on a case-sensitive
filesystem. Their parent directories must already exist. Checks cover both
command orders for export/release roots and both framework append destinations
against workflow metadata, including resolved child roots outside the work tree.

The same case/NFC and filesystem-identity comparisons protect saved-validation
inputs, reserved snapshot roots and incomplete candidate names. Reference
manifest leaves and linked store roots are included as inputs. All three saved-validation scripts preflight
their work/report destinations and writer children before writes, require Python
assertions, and publish reports atomically. Malformed URL ports fail source-option
preflight in the CLI, API and workflow helper before stores, locks, baselines or
release outputs are created. Active release candidates receive the same inventory
check as standalone verification before their manifests are opened.

SQLite readers reject WAL-format headers and existing journal/WAL/SHM sidecars
before opening the database. A WAL file can contain data absent from the main
file; recover or rebuild that database before supplying a standalone artifact.
Readers retain ordinary read transactions and do not ignore journals via SQLite's
immutable-file mode.
