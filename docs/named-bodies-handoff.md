# Orrery activation handoff

The producer now prepares paired publication using the exact [version-1 contract](paired-catalogue.md).
This is implementation, not evidence of merge, deployment or hosted acceptance.
The final candidate descriptor is committed at `data/paired/latest.json`; use its
exact URL/byte/hash references together, never mix it with another generation.

Orrery PR126 is retired and supports only fixed pairs. In a fresh workspace from
current master, add an indexed `pairedLatest` profile and resolve that descriptor
once into the existing `pin + namedBodies` load. Reject conflicting profile
selectors. Preserve build/runtime validation, both renderers' atomic scene
replacement, stale cancellation, failed-draw rollback and source lifetimes.
Enable the paired endpoint as default only after verifying the deployed producer
and actual app paths. Keep asteroid-only and explicit fixed-pair profiles usable.

Use `consumer/PairedCatalogSource.js` as the producer reference, adapting the
resolver into the existing app load so requests remain cancellable. Retain all13
producer definitions/provenance, always-visible references, exact dwarf mapping,
source discovery metadata and unchanged view/camera/group fits. Test the full
state matrix described in paired-catalogue.md. Comet playback stays parked.

Pins have bounded support: at most four pairs, previous-pair rollback, minimum
14 days after observed hosted supersession. On expired-resource failure, retain
the old scene, reacquire pairedLatest once and validate a complete new pair;
show retry if it still fails. Never retry by independently reading asteroid latest.
Permanent fixed clients must store their own complete product copy. Legacy root
asteroid URLs retain their previous current-only policy. See the contract for
capacity deferral, publication-confirmation clocks and manual rollback limitations.

Check current provider state and actual hosted descriptor/reader before rollout.
No merge, mark-ready, deployment, server or live-browser permission is conveyed.
