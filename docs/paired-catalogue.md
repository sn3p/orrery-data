# Paired catalogue publication, version 1

The producer prepares `paired/latest.json` alongside the unchanged asteroid-only
`latest.json` format. A reviewed merge publishes the complete verified Pages
artifact; a local candidate or draft PR is not a hosted release. Orrery PR126
supports fixed pairs only. A fresh Orrery reader/config change is required to
consume this endpoint and enable all thirteen bodies by default.

## Descriptor and acquisition

The endpoint is `https://sn3p.github.io/orrery-data/paired/latest.json`.
It is UTF-8 JSON, at most 4096 decoded bytes, with exactly these fields:

```json
{
  "paired_catalogue_version": 1,
  "asteroids": {"url": "https://sn3p.github.io/orrery-data/paired/asteroids/HASH/index-HASH.json", "bytes": 123, "sha256": "HASH"},
  "namedBodies": {"url": "https://sn3p.github.io/orrery-data/paired/generations/PAIR/named/index-HASH.json", "bytes": 456, "sha256": "HASH"}
}
```

`PAIR`, `HASH` and sizes above are placeholders. Hashes are lowercase SHA-256;
sizes are positive integers (asteroid index at most 4 MiB, named index at most
128 KiB). Each reference has exactly `url`, `bytes`, `sha256`. URLs are absolute
HTTPS, with loopback HTTP allowed for tests; no credentials, query or fragment.
The producer's generation identity hashes the canonical JSON array of asteroid
and named index hashes, including its trailing newline. `pair.json` in that
generation contains the same descriptor. Asteroid catalogues live once per index
hash under `paired/asteroids/HASH/`; science-only pairs share those URLs. The
initial draft candidate was relocated before its first publication, preserving
every scientific byte and hash. No previously hosted paired URL is retired by
that draft-only relocation. Relative child references
retain the existing browser and named-body contracts.

Read the descriptor **once**, with cache revalidation, then resolve it to
`{pin: asteroids, namedBodies}`. Fetch and hash-check both products and validate
the mapping against the exact asteroid index before replacing any scene. The
mapping's five identities include explicit absence; no inference from elements
or hardcoded ordinals is permitted. Definitions remain producer-owned, validated
1800–2100 with unrestricted approximate extrapolation. Source rows, classes,
discovery dates and counts are unchanged; Orrery owns display suppression.

`consumer/PairedCatalogSource.js` provides `resolvePairedLatest` and
`openPairedLatest`. Copy its dependencies from the consumer README. The resolver
fits Orrery's existing atomic load/draw/rollback path; it does not add polling.
The reference opener closes its candidate source on failure. The app must still
cancel stale loads and commit both products only after a successful draw.

## Retention and recovery

At most **four distinct pairs** are retained, including the current pair. The
immediately previous pair remains available for rollback. Older pairs have a
**minimum 14-day grace period after confirmed hosted supersession**. A refresh
starts that clock only when it observes that Pages serves the currently committed
replacement; preparation or commit time alone does not start it. A later no-op
refresh may record this confirmation. Network failure/mismatch cannot authorize
pruning. Expired pairs are pruned only while installing a new pair, with matching
hosted confirmation. The current and immediately previous pair are never pruned.

If four slots fill before a safe expiry, refresh defers **only the new pair** and
returns `deferred` with an explanation. The legacy root asteroid catalogue still
advances. The last valid pair and all its supported files remain available, and
newly observed supersession stamps are committed even during deferral. Thus root
latest and paired latest can intentionally refer to different asteroid hashes;
consumers must obtain both products from the paired descriptor. A fresh runner
can resume after confirmation and expiry without losing retirement clocks.
The scheduled summary reports deferral. Never delete ledger entries to bypass
support promises. Re-selecting a retained pair uses no new slot and renews its
protection. Science-only releases consume pair slots but share the same asteroid
payload. Normal weekly releases fit this window; rapid releases or delayed Pages
publication can defer paired freshness without freezing the legacy endpoint.

The measured asteroid catalogue is about 119 MB. Four science-only pairs now use
one shared catalogue plus the legacy root copy and comets, about 257 MB total;
four distinct asteroid catalogues plus root and comets still use about 614 MB.
The legacy root copy preserves its existing relative-reference contract. A shared
catalogue is removed only when no retained pair refers to it. The cap bounds
count, not future catalogue growth. Complete-site verification enforces a
900,000,000-byte publication budget including comets. Exceeding that budget still
requires an explicit hosting/retention decision; promised pins are never silently
pruned. Git history is not pruned. The measured old/new real catalogues share
0 of 114 chunk hashes, so sharing by catalogue hash saves science-only copies,
not those distinct weekly payloads.

Authoring snapshots hard-link unchanged payloads on the same filesystem and
privately copy mutable descriptors. All cooperating writers replace payload
files rather than modifying their inodes. Verification runs outside the writer
lock and checks each unique retained asteroid catalogue once per refresh. A
run-local cache reuses that result only for the same file identities, sizes and
modification times; each generation's named mapping and inventory are still
checked. There is no cross-run trust cache. Change detection compares mutable
metadata and already-verified hash paths without another payload digest pass.
Concurrent changes to any product invalidate the final exchange and require a
retry, preserving the other writer's result.

This guarantee applies to **URLs referenced by retained paired descriptors**. The pre-existing root
asteroid endpoint remains current-only, with its existing pruning behaviour;
old root pins do not gain a new support promise. Permanently pinned clients must
retain their own complete copy. After a pin expires, a missing immutable resource
must leave the existing scene intact. Re-read paired/latest once and retry a
complete pair; never combine a newer asteroid index with an older named mapping.
If recovery fails, retain the scene and expose a retry action. No infinite retry,
transparent partial swap, or indefinite offline-pin availability is promised.
Explicit deployment rollback outside the maintained publisher requires reviewing
and resetting retention evidence; the ledger must describe actual publication.

## Publication and failure boundaries

`scripts/update_browser.py` builds the exact identity-derived companion before
installation, preserves no-op asteroid clocks and constructs a private candidate.
It verifies the root and every retained pair independently of comet content.
The comet namespace is preserved byte-for-byte, including defects; an asteroid
refresh does not repair or certify the separate product. `verify-asteroids`
validates this authoring boundary. `verify-site` validates the entire publication,
including comets and the total byte budget.

The common external writer lock covers snapshot creation and the final conflict
check/exchange, not rebuilding, network confirmation or payload verification.
macOS/Linux native atomic directory exchange is required; unsupported filesystems
fail closed. A crash before exchange leaves the old site; after exchange it leaves
the complete new authoring snapshot. A temporary old-tree residue is not a
publication source. Raw `export-browser` rejects already paired sites under lock.

Pages snapshots bind asteroid, comet, paired and retention roots, reject corrupt
inventories/symlinks and verify all bytes before publishing one artifact. Scheduled
change inventories include paired files. The scheduled workflow first commits
source-verified, count-guarded asteroid/pair changes, then reconciles a complete verified Pages candidate and runs the existing Python
and Node regressions before dispatch. An unpublished no-change retry also runs
those regressions, so a previous failure cannot be skipped after its data commit. A failing
regression or comet verification can therefore leave generated data committed,
but prevents this workflow from dispatching Pages. This deliberately separates
repository freshness from publication availability. Source/asteroid validation or
count-guard failures still prevent the commit; Git push races fail without force.
A failed Pages deployment leaves the preceding hosted artifact and remains
recoverable through the existing publication retry path.

Do not infer application acceptance from producer tests. Orrery follow-up must
cover both renderers, exact dwarf suppression, filters/counts, labels and orbit
lines, dates and absent dwarfs, cancellation, stale/concurrent loading, offline
failure/recovery and unchanged cameras/group fits. All thirteen remain reference
bodies independent of discovery date; ordinary clipping/display controls remain.
No comet playback, new camera action, or standalone Orrery3D rollout is included.

## Forks and Pages base changes

`update_browser.py --base-url https://owner.github.io/repository/` selects both
the absolute reference base and publication-confirmation host. The scheduled
workflow passes configure-pages through `--pages-base-url`: HTTP is normalized
to HTTPS, preserving the consumer security requirement. If Pages configuration
is unavailable, pairing is deferred and root asteroid authoring continues. A
host without working HTTPS cannot confirm supersession or expire pins; HTTPS
must work before paired consumer activation. Generic `--base-url` remains strict.
HTTPS bases may
not contain credentials, query or fragment. The default remains production for
manual compatibility. Migrating a base rewrites retained pair-reference metadata
without changing scientific payloads or hashes, and resets retirement clocks so
old-host evidence cannot prematurely expire new-host URLs. Confirmation always reads the
configured host; it cannot use production's availability to authorize fork pruning.
All paired metadata, including retention.json, has the same 4096-byte bound at
verification, snapshot and remote reconciliation boundaries. Missing paired files
raise contextual DataError exceptions.
