# Unpublished comet history research

These files are research inputs, not a public schema or a Pages product. No
consumer or publication path reads this directory. Hosted schema 2 and `data/`
remain unchanged.

`discoveries.json` contains all six catalogue discovery assertions verbatim,
together with separately sourced historical events. `sources/` retains original
SBDB bytes and the metadata records their hashes/query URLs. The acquisition date
is known; the earlier assessment did not retain an exact clock time, which remains
null. Historical sources carry checked dates and links. Event lists are deliberately
not exhaustive lists of every apparition/recovery.

- Halley: ancient recorded sighting and 1758 recovery are separate; original
  discovery is unknown. Astronomical year -239 means 240 BCE.
- Encke: Méchain's discovery is distinct from Encke's later orbit identification.
- Tempel–Tuttle: earlier 1366/1699 sightings, Tempel's December 1865 independent
  discovery, Tuttle's 1866 independent discovery and later recoveries remain
  distinct. Tempel's day is unspecified here; Tuttle's exact day is explicitly a
  catalogue assertion.
- Tempel 1: discovery, the later-confirmed 1967 photograph and 1972 recovery are
  separate events.
- Tempel 2: SBDB July 3 is preserved. A research abstract reports July 4; the
  interpreted event uses July precision pending resolution. This conflict is not
  silently corrected. The publisher's full page returned 403 during research.
- 67P: Gerasimenko's September exposure and October recognition/discovery preserve
  both contributors and distinct dates.

Ancient date precision and historical calendar uncertainty are explicit. A year or
month is not converted to January 1/the first day for animation. Event metadata
does not establish trajectory availability or a visibility policy.

## Pilot reproduction

From the repository root, `python3 scripts/comet_history_pilot.py --acquire`
explicitly permits serial downloads of eight missing JPL queries. Subsequent
`python3 scripts/comet_history_pilot.py` runs are offline and fail on absent or
corrupt retained responses. Outputs are fixed under `.context/comet-history-pilot/`,
never `data/`. Preserve that directory for replay; the raw files are intentionally
not committed as public data.

Pinned solutions are Halley record 90000030/JPL#75 from 1835-08-21 and
Tempel–Tuttle record 90000626/JPL#J985/69 from 1865-12-22, the starts of their
reported observation arcs. Full experimental series extend through the final
four-day grid sample at or before 2099-12-30. No ancient vectors or extensions for
the other four comets are acquired by this script.

Each pilot verifies identity, conventions, exact grid/count, matching scientific
headers, serialized 12-significant-digit states and every interval at days 1, 2
and 3 against separate Horizons queries. These tests establish interpolation
agreement with a fitted model, not physical historical accuracy. Raw responses,
query URLs, retrieval timestamps and SHA-256 hashes remain available offline.

See `results-summary.json` for measured results; detailed query provenance is in
the workspace's `results.json`. Run the server-free focused regression suite with
`python3 -m unittest discover -s tests -p test_comet_history_pilot.py -v`.

## Results and next decision

| Pilot | Max sampled error (AU) | Full JSON / gzip bytes | Historical JSON / gzip bytes |
|---|---:|---:|---:|
| Halley | 0.000003240645 | 2,397,702 / 961,555 | 1,309,617 / 524,507 |
| Tempel–Tuttle | 0.000000260027 | 2,145,361 / 875,940 | 1,046,327 / 427,295 |

Both pass the existing 0.000052 AU tolerance: 72,417 and 64,104 independent
quarter/midpoint comparisons respectively. Gzip sizes are local compression
measurements, not hosted transfers. Sizes include a small experimental envelope;
final contract overhead has not been decided. Compact metadata is 9,937 bytes
(2,464 gzip), including provenance links but excluding retained raw SBDB files.

Halley covers 1835-08-21–2099-12-30. Its historical subset ends exactly on
1980-01-01; all overlapping baseline states and the seam velocity agree exactly
at serialized precision. Tempel–Tuttle covers 1865-12-22–2099-12-27; its historical
subset ends 1980-01-02 because its grid has a different phase. At 1980-01-01 its
interpolated position differs from the baseline by 2.35e-9 AU and velocity by
4.22e-9 AU/day. Maximum position difference over all compared baseline dates is
1.46e-7 AU. These are agreement measurements, not a finalized seam policy.

The full files exceed 2 MiB (2,097,152 bytes); the historical subsets each fit.
Recommend evaluating one historical segment plus the existing modern segment per
pilot instead of mandatory 50-year chunks. Keep an explicit byte limit, overlap
selection and gap rules. The Tempel–Tuttle grid phase must be resolved deliberately
when specifying the final seam, and its full experimental file must not replace
modern data because it ends three days earlier. Schema, chunking, application
integration and publication remain unfinalized. Ancient trajectories and the other
four extensions remain deferred.

Validation includes offline replay, source hash verification, seven focused
regressions and independent recomputation of every comparison and file hash.
The independent review identified an incomplete scientific-header comparison;
it was fixed and tested before replay. No physical historical uncertainty bound
has been established. No claim of a continuously bounded interpolation error is made.

Final local checks: 129 Python tests on Python 3.12.7, 38 Node tests and complete
site verification passed. The Python selection excluded 12 modules that start
HTTP servers or depend on those fixtures; the exact selection is retained in the
workspace evidence. Python 3.11/3.13 and the browser workflow were not run (server
and image restrictions apply). No push/CI/publication was requested or performed.
A final hosted-descriptor fetch matched the assessment's unchanged catalogue pin.
