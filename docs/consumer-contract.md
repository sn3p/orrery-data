# Experimental consumer contract v1

This is the concrete trial contract for [the delivery plan](consumer-delivery-plan.md).
OrreryData produces it; Orrery and Orrery3D own their runtime adapters and loaders.
Consumer adoption/conformance is pending. Breaking trial changes require a new
`contract_version`; existing JSON and release schema 1 remain unchanged.

## Preparing a trial

First prepare the ordinary `export`. Then run:

```sh
orrery-data export-indexed --export /path/to/export-v1-HASH --output artifacts/indexed
orrery-data verify-indexed --bundle /path/to/delivery-v1-HASH --index-sha256 HASH
```

The first command reads and verifies an existing export, creates an immutable
standalone bundle, and returns its path and index pin as JSON. It neither changes
the input export nor activates a `latest` pointer. No network access is required.
The second command checks a retained/transferred bundle. Obtain the index SHA-256
through a trusted channel; hashes alone are not authenticity. The existing
[release trust scope](release-contract.md#trust-and-evidence) applies.

```
delivery-v1-<SHA256 of index.json>/
  index.json
  full/                       # unchanged source export, including manifest
    catalog.json
    catalog.json.gz
    master.jsonl.gz
    manifest.json
    SHA256SUMS
    MPCORB-header.txt
    NOTICE.txt
  chunks/                     # absent or empty when selected population is zero
    000000.json
    000000.json.gz
    ...
```

This derivative is separate from release schema 1. `prepare-release` does not
include it yet. The bundle retains the master for portable provenance/access;
browsers fetch only metadata and their selected catalog files. A later publishing
step may host master/provenance separately with a new pinned descriptor.

## Index and identity

`index.json` is UTF-8 strict JSON with these fields (no duplicate or unknown keys):

| Field | Meaning |
| --- | --- |
| `contract_version` | Integer `1`. |
| `schema_version` | Existing numeric record schema, integer `1`. |
| `encoding` | `json-array`: an outer array of objects, each with exactly the nine named numeric fields in [schema v1](schema.md). Rows are objects such as `{"disc":2451544.5,"epoch":2451545,"a":2.5,"e":0.1,"i":5,"W":10,"w":20,"M":30,"n":0.2}`. |
| `catalog_id` | Original `export-v1-…` data version. Same selected, ordered catalog for both adapters and every chunk size. |
| `snapshot_version` | Original immutable snapshot. |
| `producer` | `{tool_version}` of the indexed exporter; original producer version remains in `full/manifest.json`. |
| `selection`, `counts`, `exclusions`, `sources` | Original export metadata, unchanged. `counts.discovery_export` is the selected population. Missing dates remain excluded and counted. |
| `full` | File descriptor for the original full JSON. |
| `provenance` | `manifest`, `master`, `header`, `notice` file references. Master is gzip JSONL. |
| `chunk_bytes` | Maximum decoded bytes per chunk, including brackets, commas and final newline. |
| `date_counts` | Strictly date-ascending `[discoveryJulianDay, cumulativeCount]` pairs; positive strictly increasing counts, ending at the selected total. Empty array for an empty profile. |
| `chunks` | Contiguous descriptors in ordinal order; empty for empty data. |

A file reference is `{url, bytes, sha256}`. URLs are relative to `index.json`,
using the exact layout above. A catalog file descriptor is
`{url, bytes, sha256, gzip: {url, bytes, sha256}}`. Its outer hash covers the
decoded `.json` bytes; `gzip` identifies the stored download representation.
Each chunk adds `start`, `end`, `first_disc`, `last_disc` to that descriptor.
Ranges are half-open `[start,end)`; `end-start` equals its nonzero row count.
Chunks cover `[0,counts.discovery_export)` exactly. A discovery date can cross
file boundaries. No object ID or identity across catalog versions is implied.

A source pin is `{url, bytes, sha256}` for `index.json`. The index's exact SHA-256
is **source identity**: changes to packaging, provenance or encoding require a
new pin, even if `catalog_id` is unchanged. It is deliberately outside the index
to avoid a self-hash. Bind caches, resume ranges and asynchronous results to both
catalog and source identity. Never mix files from different pins.

The index has a 4 MiB decoded ceiling. Chunk size defaults to 1 MiB; the CLI
allows a positive byte cap up to 8 MiB. A record that cannot fit alone fails the
export; the cap is never silently exceeded. These are trial format ceilings,
not demonstrated optimal network/renderer budgets.

## Adapter operations

These operations define behavior; each app may use its native language/types.
No browser runtime package is introduced in OrreryData.

Resolve a CLI pin's relative `url` against the published bundle directory before
calling `open`; do not resolve it against the app page or the repository root.

```ts
open(pin, {signal}) -> Promise<CatalogSource>
source.info                      // verified index metadata, immutable
source.sourceId                  // exact index SHA-256
source.countThrough(julianDay) -> number
source.read({start, end}, {signal}) -> AsyncIterable<Batch | Complete>
source.close()                    // cancels work and releases adapter resources

Batch    = {type: "batch", catalogId, sourceId, start, end, records}
Complete = {type: "complete", catalogId, sourceId, start, end}
```

`countThrough(T)` accepts a finite numeric Julian day, including fractional days.
It returns the cumulative count at the last indexed date `<= T`, or zero before
the first date. All records means `end = info.counts.discovery_export`. A loader
requests missing ranges within `[0,countThrough(T))`; starting later or moving
backward does not require replaying discovery events. Strings and JS `Date`
objects do not cross this boundary. Calendar controls must convert explicitly:
`2000-01-01` means JD `2451544.5`, independent of the browser timezone. This is a
published calendar date convention, not a TT conversion for orbital epochs.

`read` accepts integer bounds `0 <= start <= end <= selectedTotal`. It yields
ordered nonempty batches that partition exactly the requested range, followed
by one `complete` with the original bounds. Empty reads yield only `complete`.
Fetching whole boundary files is permitted; extra rows are not yielded. A
whole-file adapter may fetch the whole catalog then slice; an indexed adapter
fetches intersecting chunks. Neither changes the requested population.

Adapters report unsupported versions/encoding, invalid metadata/ranges, missing
files, checksum mismatch and invalid records as errors; no completion event is
emitted after failure. Cancellation rejects with `AbortError` and is not a data
failure. The `open` signal controls opening only; each `read` signal controls
that read only. Concurrent reads may share a download, but cancelling one must
not invalidate another. Closing is idempotent, aborts active reads and rejects subsequent reads.
Already yielded verified ranges can be reused only under the same pin.

The loader attaches its own request-generation token, ignores stale results,
and tracks retained intervals. Source completion means the requested read ended;
a **complete scene** additionally requires every row `[0,countThrough(T))` to be
verified, prepared and committed to display. Out-of-order requests, retries and
equal-date splits cannot produce gaps or duplicate visible rows. A late chunk
holds playback at a completely covered date, preserving speed and resetting the
elapsed-time clock before resuming. Rate units are simulated days per elapsed
second. Pause/hidden states stop unnecessary prefetch.

Workers and transferred buffers remain app-owned. Define transfer ownership so
stale work cannot mutate active buffers; graphics recovery must rebuild from
retained verified data or reload it. Renderer/marker/UI behavior remains native
to each app. New date-navigation UI is deferred.

## Verification policy for comparable trials

The producer verifies the input export and output bundle; app builds verify the
selected index and deployed artifact bytes against their pins. At runtime both
adapters verify the index before using it and each decoded catalog file before
yielding records. Validate records, count/range/date bounds and exact date-count
agreement for received rows. HTTP `Content-Encoding: gzip` is transparent to
`fetch`: hash the decoded bytes returned to the adapter against the outer hash.
A manually fetched `.gz` file instead needs explicit decompression; its stored
hash is distinct. Do not decompress twice.

Browser `SubtleCrypto.digest()` requires the full input buffer. Count that buffer
in the whole-file baseline's peak memory; chunks bound the corresponding input.
Do hashing/decoding off the main thread where appropriate, but record memory,
time and any copies. Limit outstanding fetch/verification/preparation bytes;
a per-file ceiling alone does not bound concurrent memory. Do not weaken one
adapter's verification to improve its benchmark.

Shared fixtures under `tests/fixtures/consumer-v1/` provide real producer bundles
and request/error/state vectors. Consumer tests must exercise their actual
adapters and loader against them. Producer tests establish artifact conformance;
they do not establish app cancellation, rendering or recovery behavior.

## Future service

The user expects a database-backed service may become the eventual direction.
A future adapter can implement the same count/range operations against an
explicit immutable snapshot. It need not mimic static chunk boundaries. New
queries or live updates require new semantics; they are not speculated into
this trial. Hosting on the available Ubuntu server remains a later choice.

See the [first producer measurements and verification limits](indexed-delivery.md).
