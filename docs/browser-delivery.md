# Current browser distribution

The producer maintains one complete discovery-dated browser catalogue in `data/`.
It is separate from complete indexed bundle v1. Raw MPC files, full JSON, SQLite,
gzip sidecars and the full master stay in local producer storage. There are no
permanent deployed version sets or required GitHub Releases. Git history still
retains previous changed file contents.

The committed catalogue uses the 18 September 2026 MPCORB refresh and retains
895,910 discovery-dated records. Run `verify-browser` to inspect its current pin
and inventory. Its 114 JSON chunks total 118,824,800 decoded bytes; HTTP transfer
depends on hosting.

## Scheduled automation and manual updates

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
Neither local command makes a Git commit, pushes or publishes data.

For an exceptional manual data update, use one fresh branch/workspace per PR, a
descriptive branch and a draft PR. Review counts/provenance and the reported
added/changed/unchanged/deleted files. If the result is unchanged there is no
data commit or deployment to make. Never reuse a retired branch. Routine weekly
updates use the hands-off path below instead.

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

`Refresh MPC browser data` runs each Monday at 18:17 UTC and supports manual
dispatch. It restores the verified HTTP source cache, runs the same updater
without a count-decrease override, verifies the complete browser inventory, and
rejects any tracked change outside `data/`. It also compares guarded counts with
the committed browser index, so fresh runners retain the count-decrease gate
without depending on a cached local snapshot.

An unchanged projection creates no commit. When its descriptor already matches
the hosted descriptor, it also does not start Pages. A changed projection stages
only `data/`, creates one dated commit on the checked-out `master`, and pushes it
directly with an ordinary non-force push. If `master` advanced during
acquisition, the push is non-fast-forward and fails; the job does not rebase,
force-push or retry stale output.

Before any push, the workflow runs the Python suite on 3.11, 3.12 and 3.13 plus
the Node source-integration suite, including a complete non-image traversal of
the candidate through the production consumer adapter. Source, validation or
regression failures therefore leave `master` untouched. The rendered Playwright
suite remains in the normal PR/push test workflow; scheduled data refreshes do
not create its image artifacts.

The repository keeps its read-only default workflow token and does not need the
setting that permits Actions to create or approve pull requests. This workflow
requests `contents: write` for the data commit and `actions: write` to dispatch
`browser-pages.yml`; `pages: read` supplies the configured publication URL. A
push made by `GITHUB_TOKEN` does not start another workflow, so the workflow
compares the verified local descriptor with the hosted one and explicitly
dispatches the existing Pages publisher when publication is needed. The
publisher re-verifies the committed tree and retains its normal no-op and
failure behavior. If a dispatch or deployment fails after a successful data
push, the next acquisition run sees the hosted mismatch and retries publication
even when MPC data itself is unchanged.

### Cadence decision and measured churn

The first live refresh on 18 September 2026 compared the 12 September MPCORB
seed with that day's MPCORB while NumberedMPs remained unchanged. The catalogue
kept 895,910 discovery records in 114 chunks and grew only 161 decoded bytes,
but orbital changes replaced all 114 content-addressed chunks. The public diff
was 115 additions, 115 deletions, one changed `latest.json`, and two unchanged
provenance files. A two-commit isolated Git pack grew by 32,237,619 bytes.

At that measured increment, daily commits project to about 11.77 GB/year and
weekly commits to about 1.68 GB/year. Daily Git refreshes were therefore
rejected. Weekly automation is the initial balance between MPCORB freshness and
repository growth; the local/manual path remains available for an urgent orbit
refresh or an exceptional recovery. This is not a delta format, compaction plan
or hosting change.

## Conditional acquisition

`refresh --reuse-unchanged` uses a separate verified HTTP cache. Strong ETags can
produce 304 responses; revalidation retains original acquisition provenance.
Missing, corrupt, malformed/weak-validator or different-URL entries cause a full GET.
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
(HTTP is permitted only for `localhost`, `localhost.`, IPv4 loopback `127.0.0.0/8`
or IPv6 loopback `[::1]`), then pins the verified index for its
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

The repository's Pages source is already configured as GitHub Actions, with HTTPS
enforced. **Publish current browser data** runs automatically on pushes to `master`
that change any of these paths:

- `data/**`: committed browser data;
- `.github/workflows/browser-pages.yml`: publication workflow;
- `scripts/prepare_browser_site.py`: verification and staging entry point;
- `orrery_data/**`: the producer package imported by the verification CLI,
  including its shared validators.

Documentation, tests, consumer code and the manual update script alone do not
trigger publication. Pull requests, feature-branch pushes and tags do not publish.
The manual `workflow_dispatch` entry point remains available on `master`; runs
selected on other branches skip preparation and deployment.

Every eligible run verifies the committed data before staging a complete artifact. A
data-only push or normal manual run skips upload/deployment when the live
`latest.json` descriptor equals the committed descriptor. An unavailable or
invalid live descriptor permits a valid local dataset to be prepared. Invalid
local hashes or inventory fail before staging, even when forcing deployment.

For push runs, the checkout includes Git history so the stager can compare the
push's `before` commit with the checked-out `HEAD`, including multi-commit pushes.
Changes to any of the three publication-code paths above force a redeploy even
when the descriptor is identical. If that comparison is unavailable (such as a
first push or an unreachable pre-force-push commit), the run also redeploys the
verified data. Manual runs keep the existing descriptor no-op behavior; select
`force: true` to repair or explicitly redeploy unchanged data. A triggered run
therefore does not always mean a deployment took place; its result is `prepared`
or `unchanged`.

The workflow uses GitHub's Node 24 checkout/Python/Pages actions and uploads the
complete inventory plus `.nojekyll`. Preparation has only contents/Pages read
permissions; deployment retains Pages write/OIDC permissions and the `github-pages`
environment. The `browser-pages` concurrency group does not cancel a running
deployment. GitHub can replace a pending run with a newer one; this is not a FIFO
queue. Publication does not fetch MPC sources, regenerate data, write to Git or
create releases.

The live discovery address is `https://sn3p.github.io/orrery-data/latest.json`.
The site root intentionally returns 404 because this data artifact has no
`index.html`. Initial publication verified descriptor/index/sample integrity,
JSON MIME types and CORS headers. Full hosted browser, negotiated compression,
cache freshness and stale-session recovery remain separate acceptance work; the
local browser suite does not establish GitHub's HTTP configuration. The PR6
push-triggered rollout and live descriptor were verified on 14 September 2026;
each later automated data commit is explicitly handed to its own Pages run and
becomes visible through the live descriptor after successful deployment.

Manual publication from GitHub or the CLI remains available:

```sh
gh workflow run browser-pages.yml --ref master
gh workflow run browser-pages.yml --ref master -f force=true
```

Local staging and browser checks:

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
performance remain separate from distribution correctness. The first real
refresh churn measurement is recorded above; later byte-size boundaries and old
orbital corrections can still change a different number of files.
