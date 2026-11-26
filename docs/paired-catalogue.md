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
  "asteroids": {"url": "https://sn3p.github.io/orrery-data/paired/generations/PAIR/asteroids/index-HASH.json", "bytes": 123, "sha256": "HASH"},
  "namedBodies": {"url": "https://sn3p.github.io/orrery-data/paired/generations/PAIR/named/index-HASH.json", "bytes": 456, "sha256": "HASH"}
}
```

`PAIR`, `HASH` and sizes above are placeholders. Hashes are lowercase SHA-256;
sizes are positive integers (asteroid index at most 4 MiB, named index at most
128 KiB). Each reference has exactly `url`, `bytes`, `sha256`. URLs are absolute
HTTPS, with loopback HTTP allowed for tests; no credentials, query or fragment.
The producer's generation identity hashes the canonical JSON array of asteroid
and named index hashes, including its trailing newline. `pair.json` in that
immutable generation contains the same descriptor. Relative child references
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

If four slots fill before a safe expiry, the next new-pair refresh fails before
installation. Retry after successful publication and grace expiry; never delete
ledger entries to bypass support promises. Re-selecting an already retained pair
uses no new slot and renews its protection. Science-only releases consume a pair
slot; unchanged science/catalogue bytes do not. Normal weekly refreshes fit this budget; expiry-boundary scheduling jitter,
rapid manual releases or delayed Pages publication can temporarily defer updates.
This explicit operational tradeoff preserves promised pins while bounding storage.

The measured asteroid payload is about 119 MB per generation: four retained pairs
plus the current root copy use about 595 MB before comet data and compression.
Git may deduplicate shared blobs; Pages contains the actual copies. This is a
count bound, not a fixed-byte guarantee as the catalogue grows. Check artifact
size before increasing limits or adding products. Git history is not pruned.

This guarantee applies to **paired generation URLs**. The pre-existing root
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
installation, preserves no-op asteroid clocks, constructs a private complete site,
verifies every retained generation and optional comet product, then atomically
exchanges site directories under the common external writer lock. macOS and Linux
native atomic exchange are required; unsupported filesystems fail closed. A crash
before exchange leaves the old site; after exchange it leaves the complete new
site. A temporary old-tree residue is not a publication source.

The raw `export-browser` primitive rejects already paired sites, including a
recheck under lock. Complete-site verification rejects cross-generation pointers,
corruption, invalid inventories and symlinks. Pages snapshots bind asteroid, comet,
paired and retention roots, verify all bytes, and publish one artifact. Scheduled
change inventories include paired files; a science-only change triggers commit
and publication even when asteroid latest is identical. Reconciliation compares
paired latest and retention metadata too. Failure prevents commit/dispatch;
Git push races fail without force. A failed Pages deployment leaves the preceding
hosted artifact and is recoverable through the existing publication retry path.

Do not infer application acceptance from producer tests. Orrery follow-up must
cover both renderers, exact dwarf suppression, filters/counts, labels and orbit
lines, dates and absent dwarfs, cancellation, stale/concurrent loading, offline
failure/recovery and unchanged cameras/group fits. All thirteen remain reference
bodies independent of discovery date; ordinary clipping/display controls remain.
No comet playback, new camera action, or standalone Orrery3D rollout is included.
