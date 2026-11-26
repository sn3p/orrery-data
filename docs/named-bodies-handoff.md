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
pair-only capacity deferral with continued legacy asteroid refresh, configured-host publication-confirmation clocks and manual rollback limitations.

Check current provider state and actual hosted descriptor/reader before rollout.
No merge, mark-ready, deployment, server or live-browser permission is conveyed.

## Initial reviewed candidate (not yet hosted)

```json
{
  "paired_catalogue_version": 1,
  "asteroids": {
    "url": "https://sn3p.github.io/orrery-data/paired/asteroids/f83dd056cb36594475ccf82827fb07d5845d131387ea41c41fcf7c282cb300c8/index-f83dd056cb36594475ccf82827fb07d5845d131387ea41c41fcf7c282cb300c8.json",
    "sha256": "f83dd056cb36594475ccf82827fb07d5845d131387ea41c41fcf7c282cb300c8",
    "bytes": 256907
  },
  "namedBodies": {
    "url": "https://sn3p.github.io/orrery-data/paired/generations/ae8e7b4b6437fb596acfbc66d37f874b7e432cbe7e8acfec8a8306a56c01a1f1/named/index-ef3423709abd3436393059b835ba1c3db5f9543559a99009d1239a324c30b4a2.json",
    "sha256": "ef3423709abd3436393059b835ba1c3db5f9543559a99009d1239a324c30b4a2",
    "bytes": 415
  }
}
```

Asteroid payloads are shared by index hash across science-only pairs. The initial
draft asteroid URL changed to that shared location; scientific bytes and hashes
are unchanged. Never infer pairing from root latest, which may advance while a
new pair is deferred.

This candidate has 895,910 asteroid rows, all thirteen definitions and all five
present dwarf mappings. It reuses the retained PR22 candidate exactly; the
previous hosted e33e6b… asteroid generation cannot use this mapping. Local complete
site staging and disk-backed reference-reader verification passed. Recheck PR/CI
and hosted status when activating; these local results are not app acceptance.
