# Named-body numerical evidence

These are producer research inputs, not activated web data. The browser product
contains compact definitions; it does not download the 3.4 MB compressed evidence.
The scientific convention, mapping contract and migration are in
[docs/named-bodies.md](../../docs/named-bodies.md).

`definitions.json` contains all thirteen fitted bodies with exact source query
URLs, original raw response SHA-256 hashes, retrieval timestamps, target/solution
headers, and measured validation errors. `evidence.json.gz` retains the parsed
source rows, initial osculating elements, source records, and independent validation
positions. Its hash is embedded in definitions. Original raw responses are retained
in the local feature store; extracted source rows are committed for offline CI and
refitting. The two Ceres raw response fixtures exercise rejection of corrupted
scientific headers/queries, dates and units at the actual parser boundary.

`refit_named_bodies.py` reproduces the twelve coefficients per body using scipy
least_squares with recorded tolerances/seed, x_scale=jac, and 500 odd-index training
positions from the original 1001-point 1800–2100 grid. The other 501 dates (including
endpoints) are held out. A second query adds 6446 points, every 17 days from
1800-01-09 through the last grid date before 2100-01-01. Training coincidences are
excluded from validation. All thirteen use 6947 validation positions each.
The 17-day cadence samples different orbital phases, including Mercury's short
orbit; it does not imply a rigorous continuous bound or a complete Eris revolution
within a 300-year interval. Earth-centre lunar motion is represented by the source
but remains an approximation in the compact fit.

Observed exact refit environment: Python 3.12.7, NumPy 2.4.2, SciPy 1.18.1,
`OPENBLAS_NUM_THREADS=1`. All coefficients reproduced with zero difference here.
Other numerical environments may differ slightly; the reproduction command allows
1e-7 absolute coefficient difference and requires convergence. Production readers
have no NumPy/SciPy dependency. They solve Kepler independently and compare against
all retained validation positions in Python and JavaScript.

To reproduce coefficients without network or private caches:

```sh
OPENBLAS_NUM_THREADS=1 python3 scripts/refit_named_bodies.py
```

For a source update, issue the exact retained query URLs serially, retain the raw
responses and acquisition metadata, and review changed solution identities. The
strict parser `orrery_data.named_body_sources.parse_source` checks target identity,
centre, frame, units, time, geometric output, exact query, finite row widths and all
dates. Refit the training positions and recompute validation against new independent
queries before accepting new definitions. Do not update raw hashes/coefficients to
silence a validation failure. The accepted validation interval is fixed at 1800–2100;
changing it requires a new reviewed model/contract decision.

`build_named_body_evidence.py --cache <retained-research-cache>` can regenerate
accepted definitions/evidence from the retained source responses, source manifests,
linear-rate-report and dense-report. It verifies the source hashes and strict
scientific grids, recomputes all errors and validates definitions before writing.
The tracked refit command independently checks imported coefficients against the
committed source rows. Use a separate `--output` directory when reviewing updates;
this utility does not publish anything.

The measured Ceres maximum after denser validation is 0.0269903 AU / 0.591809°.
Earlier 501-date screen projection put it at about 2–3 CSS pixels in normal desktop
opening/All/Belt views, under 0.4px in Distant and up to 4.6px in Hungarias. That pixel
assessment used the earlier held-out subset, not a rerender or a bound for every
zoom/orientation. No Ceres-specific model complexity was added. Keep extrapolation
explicit and the selectable timeline unrestricted.

Normal manual/scheduled asteroid refreshes now regenerate the exact inactive dwarf
mapping before installing their output. Scientific coefficients are reused until
a separate reviewed science refresh; no Horizons refit runs during MPC updates.
See the [consumer handoff](../../docs/named-bodies-handoff.md). Activation remains
blocked on verified Orrery integration and a separate reviewed activation step.
