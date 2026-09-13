# Consumer adapter trial handoff

OrreryData supplies the optional indexed export and a concrete trial contract.
Orrery and Orrery3D implement their adapters, loaders and graphics preparation
in separate consumer workspaces. This is an incremental loading experiment;
consumer adoption and the production default remain pending independently.

Start with:

- [Results and verification limits](indexed-delivery.md)
- [Consumer contract and adapter operations](consumer-contract.md)
- [Shared fixture bundles, pins and request cases](../tests/fixtures/consumer-v1/README.md)
- [Delivery plan and later milestones](consumer-delivery-plan.md)

## Available producer work

Tool 0.4.0 adds `export-indexed` and `verify-indexed`. A selected existing export
becomes an immutable derivative containing the unchanged full export, bounded
JSON/gzip chunks, cumulative date counts, provenance and an index pin. It does
not change active pointers or the existing JSON/release schemas.

The reviewed producer implementation passed 201 local tests. A saved full
895,910-record export produced 114 chunks capped at 1 MiB decoded, preserving
numeric values and order. See the results above for exact sizes and identities.
Those measurements establish producer output, not browser performance.

The small fixture bundles are included in this repository. The measured full
bundle is retained locally; this work has not published a public data download.
Use the fixtures first, then obtain the retained full bundle or generate a
derivative from an available verified export. A fresh generation may have a
different exact index pin; record the pin actually used for both adapters.

## Work in each consumer

1. Read the contract and run the same shared cases through the real adapter
   boundary. Acknowledge any compatibility issue before changing its semantics.
2. Introduce the whole-file adapter using the producer index and pinned catalog.
   Keep the historical 100k catalog as a separate regression control; do not
   invent matching producer provenance for it.
3. Add the indexed adapter against the same catalog and index pin. The app's
   loader owns missing ranges, prefetch, buffering, stale results and graphics
   commitment. Keep preparation and renderer details out of the data contract.
4. Exercise boot, errors/retries, cancellation, overlapping reads, date ties,
   reverse movement, reload and graphics recovery. Test later starts/date jumps
   at the loader boundary without introducing new navigation UI.
5. Choose explicit trial device/network settings and compare complete initial
   render, short-session/full-run bytes, buffering, peak/retained memory and
   sustained rendering. Select a production default only after these checks.

Discovery inclusion is `disc <= T`; an entire date may span multiple files.
A complete scene requires all earlier selected records to be verified, prepared
and committed. Preserve the selected playback speed and reset elapsed-time
accounting after buffering. Cancelling one read must not invalidate another.

Resolve relative artifact URLs against the bundle directory, including under
an app subpath. Verify decoded bytes when HTTP gzip is transparent. The
whole-file hash buffer is part of that adapter's memory budget; workers do not
remove it. Reuse data only under the same catalog and source identities.

## Later work

Consumer integration, public data distribution, actual Pages compression and
deployment remain separate steps. A service backed by a database is a recorded
possible eventual direction, with an available Ubuntu server as a future option.
New date UI, unknown-date display and dynamic query semantics remain deferred.

The current producer review is [PR4](https://github.com/sn3p/orrery-data/pull/4).
Check its current state before selecting an implementation base.
