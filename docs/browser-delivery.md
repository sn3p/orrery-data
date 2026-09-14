# Current browser distribution

The producer maintains one complete discovery-dated browser catalogue in `data/`.
It is separate from complete indexed bundle v1. Raw MPC files, full JSON, SQLite,
gzip sidecars and the full master stay in local producer storage. There are no
permanent deployed version sets or required GitHub Releases. Git history still
retains previous changed file contents.

The committed seed uses the retained 895,910-record export, not a newly acquired
MPC snapshot. Run `verify-browser` to inspect its current pin and inventory. Its
114 JSON chunks total 118,824,783 decoded bytes; HTTP transfer depends on hosting.

## Manual update and review

Python 3.11+ is sufficient for generation. From a fresh checkout:

```sh
python3 scripts/update_browser.py
python3 -m orrery_data verify-browser --directory data
git diff --stat
```

This fetches/revalidates both official sources, fully reconciles records, exports
and validates a complete candidate, then updates only changed/new public files
and removes obsolete ones. `--allow-count-decrease` is an explicit option after
inspecting an intended source-count reduction. Errors are not an unchanged result.
No command makes a Git commit, pushes or publishes data.

Use one fresh branch/workspace per data-update PR, a descriptive branch and a
draft PR. Review counts/provenance and the reported added/changed/unchanged/deleted
files. If the result is unchanged there is no data commit or deployment to make.
Merge and publication remain explicit owner actions. Never reuse a retired branch.

For the first seed or repeatable offline processing:

```sh
python3 scripts/update_browser.py --from-bundle /path/to/complete/indexed-bundle
# Or supply both saved inputs, optionally with acquisition provenance:
python3 scripts/update_browser.py --mpcorb /path/MPCORB.DAT.gz \
  --numbered /path/NumberedMPs.txt --source-metadata /path/metadata.json
```

The normal online path requires no local Mac-only bundle. Snapshots, exports and
the optional HTTP cache live in ignored `.data/` and `artifacts/`. The existing
complete export/indexed/release formats and commands remain available.

## Conditional acquisition

`refresh --reuse-unchanged` uses a separate verified HTTP cache. Strong ETags can
produce 304 responses; revalidation retains original acquisition provenance.
Missing, corrupt, weak-validator or different-URL entries cause a full GET.
If a redirect destination changes, a 304 cannot reuse the previous resource's
validator: the producer retries unconditionally against the selected URL.
Each 200 body is checked and parsed before activation, and both selected sources
must validate. Identical decoded sources/settings reuse the existing snapshot.
An invalid immutable snapshot remains an error; cache repair does not repair
damaged authoritative snapshots. Plain `refresh` retains its full-GET behavior.

This is source reuse and complete reconciliation, not daily delta ingestion.
Continue using MPCORB.DAT.gz and NumberedMPs.txt. The compressed discovery JSON
was missing entries in the 14 September 2026 comparison. Check timestamps are
operational output; real source changes can legitimately update provenance even
when individual browser chunks are unchanged.

## Browser contract v1

The fixed discovery URL is `latest.json` at the data site's root:

```json
{"browser_contract_version":1,"index":{"url":"index-<SHA256>.json","bytes":12345,"sha256":"<SHA256>"}}
```

`<SHA256>` denotes exactly 64 lowercase hexadecimal characters. Each reference
hash/length describes decoded bytes. The descriptor has only those fields and
is at most 4 KiB. Its index is at most 4 MiB, with a content hash in its filename.
Every new session revalidates latest against its configured trusted HTTPS origin
(HTTP is permitted for local development), then pins the verified index for its
lifetime. No polling, automatic source replacement or hard-coded build-time
dataset hash is needed. The trusted origin authenticates discovery; hashes
establish consistency, not independent authentication against that origin.

The index retains v1 `schema_version`, `encoding`, `catalog_id`, `snapshot_version`,
`producer`, `selection`, `counts`, `exclusions`, `sources`, `chunk_bytes`,
`date_counts` and ordinal chunk metadata. Differences:

- `browser_contract_version: 1` replaces `contract_version`.
- No `full` descriptor; whole-file mode is explicitly unavailable.
- Each chunk URL is `chunks/<decoded-sha256>.json`; it has no gzip descriptor.
- `provenance` contains only `header` and `notice` references, with root filenames
  `header-<sha256>.txt` and `notice-<sha256>.txt`. Source metadata remains in index.

Chunk ranges partition `[0, counts.discovery_export)`. Date counts are strictly
ascending dates and cumulative totals. A date may span several chunks; inclusive
`disc <= T` requires its entire population. The unchanged nine numeric fields,
units, selection rules, validation, read/count contract and loader semantics are
defined in [consumer contract v1](consumer-contract.md). App playback, GPU readiness,
source cancellation and stale-generation protection remain app-owned.

`verify-browser` checks the exact inventory, all hashes/lengths, metadata, decoded
records, ordinal/date coverage and complete counts without needing full/master
files. The exporter first verifies the complete original input bundle; projection
does not weaken `verify-indexed`. A caller may supply `--index-sha256` from a
trusted channel. Fixtures are in `tests/fixtures/browser-v1`; old fixtures remain.

## Coherence and failure recovery

The generator builds/verifies privately. Under a writer lock it installs hashed
files first, atomically replaces latest last, then removes obsolete managed files.
Identical files retain their bytes and modification times. Unexpected files or
symlinks in the dedicated output tree are rejected rather than removed.

An interrupted authoring-tree sync can leave extra managed files or an incomplete
prune; rerun the command. Old chunk paths are never overwritten with new content.
An exact-inventory verification must pass before a commit/deployment is accepted.
Pages publishes a complete staged artifact from committed data, not this mutable
authoring directory. Existing immutable source/export evidence is not deleted.

An old session can continue only while its required old chunks remain available.
After removal, it must stop on missing/hash-invalid data and present reload. A
reload opens latest and starts a new coherent scene; never append new-source rows
to the old scene or mark an incomplete read complete. Hash paths prevent mixing;
they do not promise uninterrupted old sessions without retaining old files.
An older cached latest/index can still select a coherent older session; hosting
cache freshness must be checked at rollout.

The [reference consumer](../consumer/README.md) ports the real Orrery3D adapter and
adds `CatalogSource.openLatest(url)`. Its harness demonstrates cancellation,
stale-source rejection, reload, v1 compatibility and complete population. It is
not production UI adoption by Orrery/Orrery3D. Consumers must carry the migration
through their real loader and recovery UI in their own review units.

## Pages publication and hosted acceptance

After the change is merged, configure the repository's Pages source to GitHub
Actions and manually run **Publish current browser data** on `master`. The workflow
verifies data, stages the complete inventory and uses GitHub's Pages artifact/deploy
actions. It does not refresh sources or change Git. Concurrent deployments queue.
An equal published latest descriptor skips deployment; `force` permits repair.

The expected address is `https://sn3p.github.io/orrery-data/latest.json`; it is not
an available endpoint until Pages setup and publication succeed. No host is enabled
by generating files. Current Pages size/traffic limits and actual compression,
MIME types, revalidation, CORS from localhost, decoded hashes and stale-session
recovery must be verified on that real endpoint before hosted delivery is complete.
The browser suite uses a separate local data origin with negotiated gzip; that
establishes adapter behavior, not GitHub's HTTP configuration.

```sh
python3 scripts/prepare_browser_site.py --output .context/pages-site
npm ci
npx playwright install chromium firefox webkit
npm test
FULL_BROWSER_DATA=data npm run test:browser
```

The last command verifies the complete committed population in Chromium plus
fixture lifecycle/desktop/narrow workflows in Chromium, Firefox and WebKit.
Scientific positional accuracy, physical-device speed and production app GPU
performance remain separate from distribution correctness. Chunk churn and
Git storage savings must be measured across real updates; byte-size boundaries
and old orbital corrections can change many files.
