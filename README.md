# Orrery Data

The current browser dataset lives in `data/`. Use `python3 scripts/update_browser.py`
for a manual, validated update with conditional MPC source reuse, or
`python3 -m orrery_data verify-browser --directory data` to verify it offline.
The [browser distribution and Pages workflow](docs/browser-delivery.md) define
latest discovery, changed-file updates and the consumer migration. Public hosting
and production consumer adoption require the documented publication steps.

Validated, versioned minor-planet snapshots, local SQLite queries and static
exports and release candidates for Orrery and Orrery3D. Python 3.11+ on macOS/Linux with its standard
`sqlite3` module; no third-party runtime dependencies or server.

The orbital elements and discovery circumstances used by this project are
maintained by [The Minor Planet Center (MPC)](https://minorplanetcenter.net/):

- [The MPC Orbit (MPCORB) Database](https://minorplanetcenter.net/iau/MPCORB.html):
  orbital elements of minor planets.
- [NumberedMPs.txt](https://minorplanetcenter.net/iau/lists/NumberedMPs.txt):
  discovery circumstances of numbered minor planets.

Thank you to the MPC, and to the astrometric observers and orbit computers
whose work makes these datasets possible.

## Usage

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

Run the installed `orrery-data` command, or `python3 -m orrery_data` directly
from this checkout:

```sh
# Inspect both upstream validators; does not download catalogs or change versions.
orrery-data check

# Download both inputs, validate every row, then activate a complete snapshot.
orrery-data refresh

# Prepare versioned local release artifacts (all eligible discovery records).
orrery-data export

# Optional consumer limit: first 100k eligible MPCORB rows, then date-sort.
orrery-data export --limit 100000

# Optional indexed-loading trial, using an existing export (no new acquisition).
orrery-data export-indexed --export artifacts/export-v1-HASH
orrery-data verify-indexed --bundle artifacts/indexed/delivery-v1-HASH --index-sha256 HASH

# Build a complete local database from the current validated snapshot.
orrery-data build-db
orrery-data db-info --verify
orrery-data query --number 1
orrery-data query --discovery missing --limit 10
orrery-data query --discovered-to 2000-01-01 --order discovery --limit 20
```

`check` prints `unchanged`, `changed`, or `unknown` for each source. Matching
strong ETags are server evidence; only refresh compares content hashes.
Missing/weak validators and network failures remain `unknown`. Exit codes:
0 success (including a detected change), 1 error, 2 any unknown check result
or invalid command arguments. JSON results go to stdout; errors go to stderr.

`refresh` uses `.data/` by default. Both sources must validate before the
atomic `current.json` pointer advances. Existing records are replaced as part
of a full snapshot, so corrections and removals are represented. Decreases in
source, supported-orbit or discovery-match counts require inspecting the
inputs and explicitly passing `--allow-count-decrease`. No data is appended
to previous catalogs, and failed operations retain the previous valid state.

`export` writes a new immutable directory under `artifacts/` and updates a
small `latest.json` pointer after success. It includes:

- `master.jsonl.gz`: all supported elliptic orbital records, stable MPC keys,
  per-orbit credits, and nullable discovery dates; one JSON object per line.
- `catalog.json` and `catalog.json.gz`: the existing nine-field discovery
  profile, with known dates only and stable ascending discovery-date order.
- `manifest.json` and `SHA256SUMS`: source and output checksums, byte sizes,
  separate tool/schema/data versions, source timestamps and validators,
  counts, exclusions and selection settings.
- `MPCORB-header.txt` and `NOTICE.txt`: upstream header and attribution.

`export-indexed` creates a separate experimental bundle with the unchanged full
export, bounded discovery files and a small index of cumulative date counts.
`--chunk-bytes` controls the decoded file cap (default 1 MiB). It prints the
exact index pin, changes no active pointer and leaves the source export intact.
Both consumer adapters can use this same bundle for comparison. See the
[trial contract, adapter operations and shared fixtures](docs/consumer-contract.md).
Consumer loading and renderer integration remain separate app work.

`build-db` atomically replaces `artifacts/orrery.sqlite3` with the complete
master, including unknown discovery dates. Use `--database /path/catalog.sqlite3`
on the build and read commands for another location, or `--snapshot` on the
builder to pin an existing snapshot. `db-info` returns embedded provenance and
credits; `--verify` adds integrity checks. `query` returns bounded pages with
the master JSON fields and a total matching count. Reads never create a missing
database. See [SQLite commands, schema and recovery](docs/sqlite.md).

`prepare-release --producer-commit "$(git rev-parse HEAD)"` refreshes sources and
assembles a verified SQLite/full/selected JSON bundle. Use `--snapshot` with a
retained store for offline preparation. `verify-release --bundle /path/candidate`
checks a standalone copy. The manual Actions workflow retains a short-lived
review artifact; it does not publish a release. See [release preparation,
versions and verification](docs/releases.md).

Raw inputs, local stores, generated databases and complete exports are ignored by Git;
the current browser projection in `data/` is tracked.
This repository does not automatically publish releases or update either app.
Manual Pages deployment is prepared; hosted verification, release publication, browser SQLite, deltas, sampling, date-range
UI and app integration remain separate work.

[Schema and units](docs/schema.md) · [Repeatable updates and pinned artifacts](docs/workflow.md)
· [Validation and saved-source regression](docs/validation.md)

The [consumer delivery plan](docs/consumer-delivery-plan.md)
describes the agreed exploration of full-file and indexed loading through
consumer-owned adapters, with a possible database service later. The optional
complete producer format remains available for local trials. The current
[browser distribution](docs/browser-delivery.md) adds latest discovery and manual
Pages staging; public deployment and production app adoption remain pending.

## Attribution

The field mapping and selection behavior derive from the MIT-licensed
[Orrery importer](https://github.com/sn3p/Orrery/blob/1968ca40f02b36a7190153ebcda4c706c30e0e7b/data/data_to_json.py).
Keep the MPC header and [attribution notice](orrery_data/NOTICE.txt) with
redistributed artifacts; the code license does not relicense upstream data.
