# Local SQLite database

Tool 0.2.0 adds a local database derived from an existing validated snapshot.
It does not download sources. Run `refresh` first, or use an existing schema-1
store, including snapshots created by 0.1.2. The builder verifies the snapshot
identity, source files, master and header checksums before building.

```sh
orrery-data build-db --store .data --database artifacts/orrery.sqlite3
orrery-data build-db --store .data --snapshot snapshot-v1-<hash> \
  --database artifacts/orrery.sqlite3
orrery-data db-info --database artifacts/orrery.sqlite3 --verify
```

The database contains all supported master rows in the selected snapshot,
including numbered objects without discovery matches and unnumbered objects.
It never applies an export limit. The build result includes the snapshot and
database versions, three coverage counts, database path, SHA-256 and byte size.
The complete source manifest, original MPCORB header and attribution notice
are embedded in the database, so they remain available without the source store.
Keep these credits and provenance with any later authorized redistribution.

## Query commands

All commands return JSON to stdout. Operational errors return JSON to stderr
with exit code 1; invalid command arguments use argparse diagnostics and code 2.
An exact lookup with no match succeeds with an empty `records` array and zero
`matched_records`.

```sh
orrery-data query --number 1
orrery-data query --id mpc:designation:J60S01B
orrery-data query --packed-designation '~0000'
orrery-data query --discovery missing --limit 10
orrery-data query --discovery known --order discovery --limit 20 --offset 20
orrery-data query --discovered-from 1801-01-01 --discovered-to 2000-01-01
```

`--database` defaults to `artifacts/orrery.sqlite3` for all three commands.
`--number`, `--id` and `--packed-designation` are mutually exclusive exact
selectors. Authority IDs and packed designations are case-sensitive. A selector
can be combined with discovery filters. Values are bound SQL parameters.

`--discovery all|known|missing` defaults to `all`. Calendar bounds are inclusive
`YYYY-MM-DD`, using the same midnight Julian-day conversion as the existing
discovery profile. Bounds exclude null dates; combining bounds with `missing`
is an error. Discovery dates remain distinct from source dates, orbital epochs
and simulation time. A range query selects discoveries in that range; a future
animation needing an earlier baseline must request those earlier objects too.

`--order source` is the default. `--order discovery` sorts ascending by date,
puts null dates last and retains source order for ties. `--limit` is a page
size of 1–10000, default 20; `--offset` is nonnegative, default zero. Pagination
is applied **after filtering and sorting**. This differs deliberately from the
unchanged `export --limit` selection policy (first eligible source rows before
date sorting). Query results always use the full master field names, including
distinct `W` and `w`, and carry `snapshot_version` and `database_version`.
`matched_records` counts the entire filtered result before pagination. A new
build can change pages; pin a separate database path when paging across calls.

`db-info` returns complete metadata and checks actual object/orbit and dated/null
counts against the embedded snapshot. `--verify` additionally runs SQLite's
full integrity and foreign-key checks. These commands open existing files with
`mode=ro`, enable `query_only`, and use one read transaction per command. Missing
files fail without creating directories or databases. Unsupported application
IDs/schema versions, invalid metadata or corrupt files fail; rebuild from the
retained source snapshot. There is no arbitrary SQL execution command.
Every read also checks the embedded MPC header's UTF-8 bytes/hash and the
attribution notice hash against the database identity, and requires the
identity's file manifest to match the embedded snapshot's file manifest.
SQLite's structural integrity check alone does not validate those values.

## Schema and versions

SQLite schema version 1 is independent of the existing JSON schema version 1.
`PRAGMA application_id` is `0x4F525259` (ORRY); `PRAGMA user_version` is 1.

| Table | Columns / purpose |
|---|---|
| `objects` | `source_order` integer primary key; unique `id`, nullable unique `number`, unique case-sensitive `packed_designation`; nullable `readable_designation`, `disc` |
| `orbits` | `source_order` integer primary/foreign key to objects; `epoch`, `a`, `e`, `i`, `ascending_node`, `perihelion_argument`, `M`, `n`; nullable `orbit_reference`, `orbit_computer` |
| `metadata` | Unique text `key` and JSON-encoded text `value`; database identity, builder/runtime versions, generation time, complete snapshot manifest, MPCORB header and notice |

One object has exactly one current orbit. `source_order` is the one-based order
of supported master rows, preserved for stable selection; it is **not** an
object identity or a cross-version identifier. `id` remains the MPC authority
key described in [JSON schema and units](schema.md). Identity across later
numbering/designation corrections still requires a separate crosswalk.

All orbital/date numbers use SQLite REAL, preserving Python's binary64 values.
MPC numbers use INTEGER. SQLite column names are case-insensitive, so the
distinct JSON `W` and `w` map to `ascending_node` and `perihelion_argument`.
The CLI maps them back without rounding, including the printed 360° endpoint.
The remaining units and null semantics are unchanged. Identity/number/packed
indexes support exact lookup; `(disc, source_order)` indexes discovery filters.
Constraints and import validation reject duplicate identities, invalid fields,
nonfinite numbers, unsupported orbits and counts inconsistent with the snapshot.

`database_version` is `sqlite-v1-<SHA-256>` of the embedded ordered `identity`
JSON: database schema version, builder tool version, source snapshot version,
master/header file hashes and sizes, and the attribution notice hash. It names
the logical build inputs, not the resulting SQLite file bytes. `created_at`
records database generation, independently of the preserved snapshot creation
and source retrieval times. SQLite runtime version is recorded separately.
Repeated builds retain logical identity, but timestamps and SQLite runtimes may
change file bytes; use the emitted artifact SHA-256 for an exact file identity.
No byte-for-byte SQLite reproducibility or cross-version migration is promised.

Rebuild to adopt a new database schema/tool. Existing immutable snapshots and
JSON exports are not rewritten. Tool 0.2.0 changes newly generated version IDs
because those IDs include the tool version; the existing JSON fields, selection
and payload bytes remain unchanged for the same master and gzip runtime.

## Atomic replacement and recovery

The builder streams the verified master into a private `.sqlite-build-*`
directory beside the destination, using a transaction and rollback journal.
It commits, checks integrity/foreign keys, closes SQLite, rechecks input file
hashes, flushes the completed file and atomically replaces the destination.
The destination parent uses the same nonblocking `.lock` writer protocol as
exports; simultaneous writers there fail and can retry. Output must end in
`.sqlite`, `.sqlite3` or `.db`, cannot be a symlink, and cannot be placed inside
immutable snapshot directories.

Every build replaces the entire dataset: corrected orbits/metadata update,
new objects with old discovery dates appear, and disappeared or newly
unsupported objects are removed. Count-decrease protection remains at `refresh`;
building an already validated or deliberately pinned older snapshot does not
require a second override. Roll back by rebuilding with `--snapshot` pointing
to the retained version.

Validation, insert, commit, integrity or replacement failures before activation
leave the previous usable database untouched. A process killed before replacement
may leave a staging directory; the previous database remains readable and a
retry uses a fresh stage. After replacement, new readers see the new complete
database; already open readers retain the old complete file. This handles local
process failures, not hardware/filesystem loss, and needs space for both files
plus build/index/journal overhead.

Treat generated databases as read-only artifacts. External writable SQLite
connections do not participate in the builder's advisory lock and are unsupported.
The builder refuses an output with `-wal`, `-shm` or `-journal` sidecars. Close
external writers and let SQLite recover/checkpoint their files before rebuilding;
do not delete live journals. After confirming no builder is running, abandoned
`.sqlite-build-*` directories may be removed. Do not remove `.lock` while any
writer could be using it. A damaged destination can be recovered by a complete
build from a valid snapshot. Generated databases, sidecars and stages stay out
of Git. No service, release publication, browser SQLite or app adoption is implied.

Implementation references: [Python sqlite3 transactions and URI connections](https://docs.python.org/3/library/sqlite3.html),
[SQLite read-only URI mode](https://sqlite.org/uri.html),
[SQLite schema/integrity pragmas](https://sqlite.org/pragma.html),
[SQLite atomic commits](https://sqlite.org/atomiccommit.html).
