# Unpublished searchable comet catalogue contract

Contract 4 separates catalogue identity, assessment and published trajectory
coverage. It is an unpublished candidate alongside production contract 3;
`data/`, publication commands and the active endpoint are unchanged.

## Exact acceptance inputs

- Production index: `a9de1f2f711dd65e880102de7c1f0c14bf20598a20d9cc4e001081406e40c2e2`.
- Expanded index: `907f8cb552c65db81eb3ec3167b0d422e764ab5fef1fcbbb948e63cae21f1070`.
- Candidate: 4,089 searchable entries, fourteen available trajectories; eight new
  numbered periodic comets. Other research probes are `under_assessment`, and
  untested entries are `not_yet_assessed`. There are no unsupported assertions in
  this scientific candidate. Unsupported, fragment links and short/dense/gapped
  trajectories have explicitly synthetic contract tests.
- Existing six modern payloads and two historical payloads are reused byte for
  byte, with their exact legacy IDs, metadata and seam guards. Defaults remain
  `1P, 2P, 55P, 9P, 10P, 67P`; never derive defaults from catalogue sort order.

Build and verify without a server or network acquisition:

```sh
python3 scripts/comet_directory_candidate.py
node scripts/verify_comet_directory.mjs data/comets
node scripts/verify_comet_directory.mjs .context/comet-candidates/directory-v4-907f8cb552c6
npm run test:comets
```

The builder authenticates committed compressed fixtures and the production
baseline, validates the complete assembled candidate through the reader, and
installs only an immutable direct child of `.context/comet-candidates/`. Reruns
verify identical contents. It cannot refresh sources or publish. Changed
baseline/fixture bytes require deliberate reassessment. Candidate inputs,
including all 87,656 independent midpoint positions for the additions, are
portable in Git. The raw scientific queries/hashes remain in each manifest;
raw HTTP responses are retained in the local research store, not needed for
ordinary candidate assembly. The midpoint fixture validates numerical fidelity
to those responses, not physical uncertainty or a continuous error bound.

## Wire contract

`latest.json` is `{comet_contract_version:4,index:{url,sha256,bytes}}`.
The index is limited to 4 MiB decoded and 20,000 entries. Its snapshot records
source URL, hash, retrieval day and nullable exact retrieval clock. The actual
candidate's catalogue clock was not captured and remains null.

Index fields are `comet_contract_version`, `snapshot`, `defaults`, `legacy` and
`objects`. `legacy` authenticates the preserved schema-3 index; it and its payloads
live beside the v4 index with unchanged hash filenames. Objects contain:

- `id`: opaque stable registry key. Existing selection IDs remain unchanged.
  New IDs are allocated once from the initial source snapshot (`c` plus its
  initial SPK number); consumers must never parse or regenerate them. This
  committed fixture is the initial registry. A future acquisition pipeline must
  reconcile source changes against it rather than reallocate IDs.
- `designation`, `name`, `aliases`, `source_ids`, nullable `parent_id`. Aliases
  are search terms; ambiguous alias lookup fails instead of choosing an object.
  Source-backed parent relationships must be explicit. Current full metadata
  has primary designation/name aliases, not an exhaustive alternate-designation
  census. Ingestion of further source aliases is later producer work.
- `assessment`: `state`, `reason`, evaluated interval `scope`, and `evidence`.
  States are `available`, `not_yet_assessed`, `under_assessment`, `unsupported`.
  Unsupported requires nonempty scope, a reason from `no-source-solution`,
  `invalid-model`, `numerical-validation-failed`, `outside-physical-lifetime`,
  and evidence containing `assessed_at`, `policy`, `source`, `sha256`.
  Timeouts, ambiguous targets and unfinished denser-grid work remain pending.
- `coverage`: ordered nonoverlapping intervals with inclusive start and explicit
  `end_inclusive`. Gaps stay gaps. This release requires exact segment intervals,
  not merged min/max envelopes. Any validated positive-length interval qualifies.
- `trajectory`: null, `{kind:'legacy',id}`, or
  `{kind:'manifest',manifest:{url,sha256,bytes}}`.

Manifests are limited to 256 KiB, payloads to 2 MiB decoded. Each manifest declares
its identity and ordered segments. A segment has `key`, `coverage`,
`grid:{start,step,count}`, `data`, `provenance:{vectors,checks}`, `validation`
and `join`. Uniform position/velocity blocks may use different positive steps;
minimum two states, maximum 25,000. Payloads carry version 4, identity, unchanged
Sun/Ecliptic J2000/UT/AU/AU-day/geometric/Hermite conventions and the series.

At touching intervals, `join` is `continuous` or `separate`; otherwise it is null.
Continuous joins require matching model identity and actual boundary position
and velocity differences within 0.000052 AU and 0.000013 AU/day. Loading either
side also authenticates the adjacent support block. Separate joins render as
separate strips and permit an explicit model discontinuity. Samples outside
owned coverage do not extend marker/path coverage. Never draw a connector across
strips or gaps. The legacy historical joins retain their stronger existing rules.

New segments retain vector and independent-check provenance with target, solution,
scientific headers, exact request URL, raw hash, API version and retrieval clock.
The reader checks catalogue target mapping, scientific header agreement excluding
request endpoints, payload integrity, grid and representative numerical evidence.
The candidate verifier checks every retained midpoint for all eight additions.
Production historical evidence and ordinary science tests remain independently
applicable; this change does not relabel midpoint validation as quarter-point
validation. Future adaptive generation must supply its declared validation evidence.

## Reference reader

Copy all files from the consumer README's file list and the MIT license at the
same revision. No npm dependency or application server is required.

```js
import CometDirectorySource from './CometDirectorySource.js';
const source = await CometDirectorySource.openLatest(url, {signal});
const page = source.search('Halley', {limit:30});
const object = source.getObject('1P');
const availability = source.availabilityAt(object.id, jd);
const details = await source.getDetails(object.id, {signal});
const window = await source.loadWindow(object.id, {start, end, signal});
const marker = await source.positionAt(object.id, jd, {signal});
const strips = await source.pathSegments(object.id, start, end, {signal});
source.close();
```

`openDescriptor` dispatches an already-fetched schema-3 or schema-4 descriptor
without fetching latest again. Schema-2 bundled rollback remains the app's
separate existing path. `source.info.defaults` is the preserved identity list.
`getDetails` preserves legacy discovery/events exactly; new objects currently
return `discovery:null`, not invented dates. Unknown identity is an error and is
not confused with an unassessed known entry. Selection reconciliation must retain
missing saved IDs according to the settled Mogadishu behaviour.

Search eagerly reads bounded metadata only; no trajectory fetches. It searches
identity, designation, name and aliases in the entire pin. The stable cursor
includes the pin/query and rejects reuse across generations or queries. It
returns `items`, `total`, `complete:true`, `nextCursor`. Sorting/filtering the
selected section remains app work; search does not mutate selection.

`availabilityAt` returns `available_at_date`, `outside_coverage`, or the
assessment state for objects without coverage. A scoped unsupported assessment returns
`not_yet_assessed` outside its evaluated intervals. Runtime loading/error is separate.
`loadWindow` returns `{id,status,segments}`; `status` concerns the start date,
while segments cover any intersecting supported ranges. No intersection returns
an empty list without downloading. A required-file failure rejects the operation;
no successful partial Fit. Intersecting files are lazy, and adjacent continuous
join support may also load. `positionAt` returns null outside published coverage.

Requests share subscriber cancellation, retry and source lifetime semantics.
New-data downloads have a two-request queue and a 16 MiB decoded-byte FIFO cache;
requests exceeding a 16 MiB window fail explicitly instead of accumulating an
unbounded result. The fixed legacy inventory has a separate existing bound of
16 MiB. These are serialized-byte accounting limits, not measured JavaScript
heap limits. App-held arrays and GPU buffers require separate release. The app
must use a bounded display window and may request smaller windows on budget
failure. Metadata remains eagerly resident (index cap 4 MiB).

## Separate Orrery integration PR

Owner: Orrery agent, building on the settled Mogadishu chooser review changes.
If that review unit has merged/closed, start a fresh branch/workspace from current
Orrery master. This producer change does not modify or deploy Orrery.

1. Reconcile Mogadishu's final selection-race, missing-ID, cross-tab and search
   performance fixes first. Keep selected-first/search/Browse/paging/Clear/Undo,
   remembered empty selection and missing saved IDs. Extend aliases and stored-ID
   validation without replacing explicit defaults or choosing popular/active lists.
2. Install the accepting reader and descriptor dispatch, preserve existing schema-3
   production and bundled rollback paths, and use window-based acquisition with
   stale-generation guards. Carry discovery details through the existing history
   consumers. Null periods use a bounded presentation window, never an invented
   orbital period. Generalize numeric-ID colour assumptions and DOM IDs safely.
3. Keep covered normal rows blank. Gaps retain “No trajectory for this date”.
   Exceptional rows use “Trajectory not yet assessed”, “Trajectory not yet
   available” or “No trajectory available”, respectively. Selected loading/error
   feedback stays retryable. No routine status badges/per-row years. Aggregate
   date-gap messages must not count unassessed entries as gaps. Missing saved IDs
   are a different state. Published objects remain selectable outside coverage;
   unpublished trajectories cannot be shown but entries remain searchable.
4. Run the two acceptance lanes below. Deploy the accepting reader while hosted
   data is still schema 3. Only after deployed-reader acceptance may a separate
   producer activation PR change the hosted pin. Keep rollback. This PR provides
   no activation or publication path, and no authority to switch hosted data.

| Acceptance lane | Required app evidence before activation |
|---|---|
| Existing production, exact `a9de1f2f…c2e2` | All six/legacy selections/defaults; unchanged positions and historical discovery metadata; Halley/55P historical starts, seam, support-only overlap, modern end; Pixi/Three paths and hidden-path Fit; loading/cancellation/retry and bundled rollback |
| Exact expanded candidate, `907f8cb5…1070` | Exact descriptor/index/payload hashes; all 4,089 searchable, fourteen available; same existing six/defaults; each of eight additions; unassessed versus pending versus gaps; alias lookup and paging; persistence/replacement/empty/missing IDs; both renderers and Fit; bounded loading/memory, corruption/retry and stale requests |
| Synthetic boundary fixtures | Short ranges, gaps, dense-grid joins, explicit discontinuities, fragments/ambiguous aliases, scoped unsupported, keyboard/focus and narrow/wide layout states |

Use an explicit candidate input/network fixture for the exact assembled bytes;
do not substitute zero trajectories for candidate acceptance or silently swap
pins. Preserve one acceptance record per lane with app revision, reader revision,
index pin, environment and limitations. Deployed accepting-reader acceptance
must precede the hosted data switch; actual live expanded-data acceptance follows
that separately authorized switch. Producer tests are not app acceptance.
