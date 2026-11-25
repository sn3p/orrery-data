# Pinned unpublished comet directory fixture

See [contract and integration handoff](../../docs/comet-directory.md).
`fixture.json` authenticates compressed index/manifests/payloads; assembly combines
these with the unchanged production schema-3 index and eight payloads. The
candidate is an immutable research fixture, not an acquisition pipeline.

The SBDB response is retained in `sbdb.json.gz`; its raw hash is in the index.
Its exact retrieval clock was not captured. `midpoints.json.gz` contains all
independent midpoint positions for the eight additions. Candidate manifests
retain exact Horizons request URLs, raw hashes and scientific metadata. The
ordinary test suite independently recomputes all 87,656 final-byte errors.

New IDs were allocated once from initial SPK identifiers; retain this registry
when implementing future source reconciliation. Aliases currently include primary
designation/name, not an exhaustive alternate-designation inventory. Fragment
parentage and unsupported assessments are unknown in this source snapshot and
are not invented. Fourteen entries are available; other inspected research cases
are pending, all untested entries are explicitly unassessed.

Raw acquisition evidence and the one-off research projection script are retained
in the owning local feature store under the Amsterdam expansion investigation;
scientific refresh requires restoring/revalidating them. Committed compressed
bytes make exact candidate replay and reader acceptance portable without them.
There is no scheduled refresh, hosted publication or application integration here.
