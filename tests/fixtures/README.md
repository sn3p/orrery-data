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

## Comet v2 midpoint evidence

`comet-v2-midpoints.json.gz` contains all 10,957 independent Horizons midpoint
positions per object for the six-object v2 catalogue. Rows are `[UT JD,x,y,z]`
in AU, with retained query/header/raw-response SHA-256 provenance. It was
extracted from cached raw responses (seed acquisitions 2026-11-18; additions
2026-11-20), never from interpolated states. Gzip uses mtime=0. Numerical tests
compare final committed object bytes against every row offline. Raw responses
remain acquisition inputs, separately retained outside the public product.
