# Comet discovery metadata candidate

This PR prepares an explicitly loaded sidecar; it does not publish or activate it.
The schema-4 directory, defaults, coverage, trajectories and all `data/` bytes
remain unchanged. No metadata date is a visibility boundary or orbital epoch.

## Current contract verified before implementation

On 25 November 2026, producer master `de39aab` (merged PR20) already contains
schema 4, index `907f8cb552c65db81eb3ec3167b0d422e764ab5fef1fcbbb948e63cae21f1070`:
4,089 entries, fourteen trajectories. Earlier “unpublished directory” descriptions
are historical. Orrery master was checked through GitHub at `6e3d88db`; its matching
local checkout was inspected read-only. `CometDirectorySource.getDetails` returns
legacy histories for six entries and null discovery for the rest. `App.js` uses
legacy sources; `Timeline.updateCometHistory` reads selected catalogue entries;
`history.eventDate` assumes a non-null date and only formats year/month/day.
These are concrete integration boundaries, not an assumption that an earlier
handoff remains current. The deployed app was not inspected in this task.

## Source assessment (before implementation)

The fourteen priority objects are the fourteen numbered comets with trajectories.
Each has a retained, nonempty JPL discovery record and curated events. Hale–Bopp
and NEOWISE are two additional unnumbered objects without trajectories: their
curated events come from NASA articles, not meaningful JPL discovery records.
Thus curated events cover sixteen objects, while priority trajectory coverage is
fourteen of fourteen. The
[JPL discovery documentation](https://ssd-api.jpl.nasa.gov/doc/sbdb.html) limits
this facility to numbered comets; its
[overview](https://ssd.jpl.nasa.gov/discovery.html) says selected comets.
The bulk query's `first_obs` is an orbital-fit observation, not discovery.
It must never substitute for missing discovery metadata. All 610 numbered
catalogue entries (including fragments) were targeted individually, by SPK ID;
identity is checked against the pinned directory. Unnumbered Hale–Bopp, Hyakutake,
NEOWISE and Tsuchinshan–ATLAS spot checks returned empty discovery objects.
NASA articles additionally support Hale–Bopp and NEOWISE events. Other unnumbered
entries remain outside this source's documented scope, not proven historically
unknowable. Broad historical-article/MPEC reconciliation remains future research.

The Meyer compilation's documentation was assessed as an alternative; a usable
bulk download was not found, and its documentation says some fractional days
were estimated. No inferred precision or designation-derived dates were imported.

Curated priority checks preserve Pons–Brooks's July 1812 day disagreement
(JPL 21 / ESA 12), Wirtanen's January 1948 disagreement (JPL 15 / UMD 17),
Pons–Brooks's 1883 rediscovery, Giacobini–Zinner's 1913 rediscovery, and Tuttle's
independent 1862 discovery. Sources supplying only a year remain year-only.
The original six, their events, catalogue claims, notes and citation registry
are preserved deeply, including Halley recovery, ancient sightings, Tempel 2's
disputed day and 67P's photographic versus recognition distinction.

## Candidate format and reproducibility

`research/comet-discovery/metadata.json` is metadata schema 1, pinned to the exact
schema-4 directory hash. It contains one ordered row per catalogue entry, a
citation registry, and statuses `preserved`, `sourced`, `source-empty`, or
`outside-source-scope`. The last two describe source coverage, not trajectories.
A `sourced` row may have catalogue assertions, curated events, or both.
`event_inventory_complete:false` always applies; an empty event list does not
mean no discovery occurred.

New records retain `catalogue_discovery` separately from `events`. The raw date
text remains unchanged; `catalogue_date` is its conservative structured parse,
or null when absent/unparseable. Catalogue assertions are not automatically
promoted into historical discovery events: the original six demonstrate why.
Dates have year/month/day, precision, calendar and astronomical year numbering.
No missing month/day is filled. Unspecified-calendar dates are retained when
possible in either the Julian or Gregorian calendar (including 1700-02-29);
acceptance does not choose a calendar. Explicit Gregorian dates and source
`checked_on` dates use Gregorian validation. The reader independently checks
that each catalogue date equals the conservative projection of its raw text. New source dates use `historical-unspecified`
unless a source explicitly establishes a calendar; no UTC instant or JD is
implied. Existing historical dates retain their original calendar conventions.
Events distinguish discovery, independent discovery, rediscovery, recovery,
earlier sighting, photographic observation and recognition. Certainty supports
documented, day-disputed, approximate and uncertain; an unknown event date is
null with uncertain certainty. Approximation remains qualitative, never an
invented numeric error interval. Disputed days cannot retain day precision.

`sbdb-responses.json.gz` retains canonicalized complete API responses and actual
request URLs/retrieval timestamps, including empty results. Per-row SHA-256 is
of canonical JSON (sorted keys, compact separators, UTF-8, trailing newline),
explicitly **not** a hash of original HTTP response bytes. Original-six raw
provenance is unchanged. `events.json` is the reviewed editorial input.

```sh
python scripts/comet_discovery_metadata.py --output .context/comet-candidates/discovery/metadata.json
python -m unittest discover -s tests -p test_comet_discovery_metadata.py -v
node --test tests/comet-discovery-metadata.test.mjs
```

The offline builder checks identities, rejects conflicting original citations,
and rejects writes outside `.context/comet-candidates`; an existing different candidate is not
overwritten. Rebuilding against another directory pin requires reassessment.

## Orrery handoff and activation boundary

Before building, reconcile current producer/Orrery commits and PR states again.
Reproduce the actual selected-comet history path, identify practical user impact,
and distinguish live behavior from candidate-only acceptance. Use a fresh review
unit if the earlier consumer branch is merged or closed.

Copy `cometDiscoveryMetadata.js` with the complete reference-reader file list and
MIT license. `loadDiscoveryMetadata(source, {sha256,bytes}, absoluteURL, {signal})`
checks integrity and binds the sidecar to `source.sourceId`; it returns immutable
metadata and does not attach it or change source state. Supply the trusted
reference explicitly. Existing schema-3/schema-4 readers and descriptors stay
unchanged. This separate metadata schema avoids silently extending their strict
key validators. Hosting, descriptor discovery and automatic acquisition need a
separately reviewed activation contract; do not guess a sidecar URL.

App work requiring coordination:

- Join rows to catalogue/details by exact ID and expose the combined citation
  registry to `App`/`Timeline`. Preserve the six original histories.
- Handle null dates, approximate/uncertain certainty and source-empty coverage;
  present JPL catalogue claims distinctly from curated events. Do not render
  outside-source-scope as a claim that no historical discovery is known.
- Bind async responses to the current source generation and selection; handle
  cancellation, failed/corrupt downloads, retry and source replacement. Metadata
  failure must leave trajectories and selection usable.
- For future playback, explicitly choose calendar conversion and the display
  policy for year/month/approximate dates. Do not map them silently to January 1,
  midnight, an observed instant or a trajectory start. No playback activation is
  included here.
- Test actual selected catalogue → details → history for original six, all eight
  additions, sourced entries without trajectories and unknowns; both renderers,
  selection persistence/reload, source replacement, failure/retry, keyboard and
  narrow/wide layouts. This producer PR is not app UI acceptance.

The producer tests verify exact rebuild, preservation, identity, structured dates,
provenance/citation integrity, candidate path restrictions and sidecar loading.
Publication requires a later explicit action after Orrery acceptance. Do not
change `data/comets/latest.json` in this task.

## Coverage and verification

The retained source pass covers all 610 numbered entries. Total sourced metadata:
126/4,089 (six preserved + 120 additions). Empty/all-null numbered responses:
486. Outside this assessment's source scope: 3,477. Curated histories cover
16 objects / 29 events, including 14/14 trajectory-bearing objects. Of 118 new
catalogue dates, 114 are day precision and four are month precision because the
source day is `00`. Those zeros are unavailable days, never day zero or day one.
NASA supplies the two additional unnumbered histories. See the research README
for the exact candidate SHA-256 and byte count.

Local verification uses Python 3.12 and Node 22. The full selected CI is Python
3.11/3.12/3.13 discovery, Python 3.13 whole-site verification, and the browser job
(`npm test`, directory-browser, browser-workflows). The local server-free selection
excludes `test_cli`, `test_source_cache`, `test_browser_scripts`, `test_releases`,
`test_release_contract`, `test_release_paths`, `test_release_review`,
`test_release_boundaries`, `test_candidate_inventory`, `test_identity_order`, and
`test_export_activation`; these start servers directly or through their fixtures.
`browser-source.test.mjs` and browser-workflows also start servers and remain CI
checks under the standing no-server rule. No development/server permission was
requested or assumed. Full multi-version CI equivalence is therefore unverified
locally. No app UI, screenshots or live-browser access was used.

Results: 206 Python tests passed in the maximal permitted selection; the final
five focused metadata tests also passed after the baseline-pin regression was
added. 75 server-free Node tests passed. Whole-site verification passed with
4,089 comet entries, fourteen trajectories and unchanged production bytes.
Isolated headless Chromium and WebKit passed rollback/production/candidate lanes,
including real browser sidecar integrity/pin loading. Firefox failed before
reader execution with “Could not find profile folder”, also with a workspace-local
temporary directory; local Firefox acceptance remains unverified. CI includes it.
Independent review prompted and verified regressions for provenance overwrites,
malformed partial dates, path escape, source-lifetime cancellation and directory
rebinding. GitHub CI status is reported separately at PR creation, without waiting.

Review follow-up: the fresh-checkout test now owns an isolated root and verifies
creation of `.context/comet-candidates`, escape rejection and immutable reuse.
Every citation insertion checks conflicts, including generated per-object keys.
Producer/reader share date expectation fixtures, accept Julian-compatible dates
without choosing a calendar, reject impossible Gregorian source-check dates and
require raw/structured catalogue-date agreement. The loader uses shared transport
cancellation and labels errors with the metadata product and requested URL.
Status rules are explicit, original histories use an ID map, and browser counts
come from the input fixtures. The retained metadata bytes and coverage are unchanged.
Seven focused Python tests, 79 server-free Node tests and isolated Chromium/WebKit
rollback/production/candidate lanes pass after these fixes. The earlier GitHub
Python 3.12 job failed on the missing `.context` fixture; its sibling Python jobs
were cancelled and its browser job passed. The fix push runs all selected CI jobs;
these local results do not claim the new CI run has completed.
