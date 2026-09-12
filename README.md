# Orrery Data

Validated, versioned minor-planet snapshots and static exports for Orrery and
Orrery3D. Python 3.11+ on macOS/Linux; no runtime dependencies or server.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

Run the installed `orrery-data` command, or `python3 -m orrery_data` directly
from this checkout. Three commands cover the update workflow:

```sh
# Inspect both upstream validators; does not download catalogs or change versions.
orrery-data check

# Download both inputs, validate every row, then activate a complete snapshot.
orrery-data refresh

# Prepare versioned local release artifacts (all eligible discovery records).
orrery-data export

# Optional consumer limit: first 100k eligible MPCORB rows, then date-sort.
orrery-data export --limit 100000
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

Raw inputs, local stores and large generated exports are ignored by Git.
This repository does not automatically publish releases or update either app.
The first milestone prepares complete snapshots locally; database, deltas,
sampling, date-range UI and app integration remain separate work.

[Schema and units](docs/schema.md) · [Repeatable updates and pinned artifacts](docs/workflow.md)
· [Validation and saved-source regression](docs/validation.md)

The field mapping and selection behavior derive from the MIT-licensed
[Orrery importer](https://github.com/sn3p/Orrery/blob/1968ca40f02b36a7190153ebcda4c706c30e0e7b/data/data_to_json.py).
Data comes from the [Minor Planet Center](https://minorplanetcenter.net/iau/MPCORB.html).
Keep the MPC header and [attribution notice](orrery_data/NOTICE.txt) with
redistributed artifacts; the code license does not relicense upstream data.
