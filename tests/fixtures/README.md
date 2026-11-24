Small, unmodified MPC source excerpts from the saved 2026-09-12 investigation.
The orbital fixture contains the full MPCORB attribution header and nine rows:
three small numbers, three packed-number boundaries (100000, 360000, 620000),
a classic provisional, a survey and an extended provisional designation.
The discovery fixture includes the six corresponding numbered records.

Original MPCORB gzip SHA-256:
`14ee5188cb0188b5e2cefba10cbed0312b3fef7a3020a0e482541370ca48b3e2`

Original decoded NumberedMPs SHA-256:
`56229cf0f045beb62180a62f4b9a9351af9955e7e88aab5976256fa62347a705`

Source URLs and attribution: [NOTICE](../../orrery_data/NOTICE.txt).
Tests construct invalid/changed variants at runtime; large inputs stay outside Git.

## Comet midpoint evidence

`comet-midpoints.json.gz` contains all 10,957 independent Horizons midpoint
positions per object for the current comet catalogue. Rows are `[UT JD,x,y,z]`
in AU, with retained query/header/raw-response SHA-256 provenance. It is
extracted from the raw response hashes pinned by the current published index,
never from interpolated states. The initial inputs were acquired on 2026-11-18
(seeds) and 2026-11-20 (additions). Rebuild after a refresh with
`python3 scripts/rebuild_comet_evidence.py`, then commit the product and evidence
together. This offline command checks both source series and every midpoint
against final published values before atomic replacement. Gzip uses mtime=0,
no filename, and a portable header. Numerical tests compare final committed
object bytes against every row offline. Raw responses
remain acquisition inputs, separately retained outside the public product.

`comet-history-payloads/*.json.gz` contains the exact two historical payloads from
PR16's scientifically verified candidate, deterministically gzip-compressed. The
existing `comet-history-index.json` is the approved index (a9de1f2f…c2e2); six modern
payloads are reused from committed data. These fixtures let CI exercise the real
production rollout CLI and publication boundary without network acquisition or
private raw research caches. Provenance and JPL source URLs remain in the index.
