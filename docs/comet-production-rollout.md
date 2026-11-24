# Historical comet production rollout

The activation change replaces committed `data/comets` with the approved schema-3
release. It remains a draft pending the deployment and acceptance gates recorded
in [the activation record](comet-activation.md). Preparing or merging support
code alone does not establish production readiness.
The approved migration replaces that URL with schema 3. Older clients may break;
there will be no permanent schema-2 API or versioned public catalogue.

## Prepare offline

The initial production release is the scientifically verified PR16 candidate:
`a9de1f2f711dd65e880102de7c1f0c14bf20598a20d9cc4e001081406e40c2e2`.
`verify-comets` and `verify-site` accept schema 2 or this exact schema-3 release.
They authenticate the index and every payload against the approved hashes and
reject missing/extra files, symlinks and unapproved pins. This is a deliberately
release-pinned publication policy, not a general schema-3 science validator.
A different index, metadata change or orbital solution needs scientific replay,
reader compatibility checks and a reviewed update to the approved pin.

The candidate builder and its raw-evidence replay remain unchanged. Restore the
retained candidate or build/verify it as described in
[the candidate contract](comet-history-candidates.md). Then:

```sh
python3 -m orrery_data verify-comets --directory "$CANDIDATE"
python3 -m orrery_data prepare-comet-rollout \
  --candidate "$CANDIDATE" --site data \
  --expected-current-pin b1b10e6d1fba1cb94cd1e03c2e39ea7ec93b584a89f74ece193dcdf8ba379567 \
  --output .context/comet-rollout/release
python3 scripts/prepare_browser_site.py \
  --source .context/comet-rollout/release/site \
  --output .context/comet-rollout/pages
```

Use the actually verified current pin if the source changes; do not bypass a
mismatch without reassessment. Output must be new and disjoint from both inputs.
Preparation captures both source descriptors and copies one rollback snapshot
under the shared source lock. It then releases that lock, verifies the snapshot,
and derives the replacement from it without copying obsolete comet payloads.
The copied descriptors and final approved historical pin must match. An atomic
exclusive rename exposes `release/site` and `release/rollback` together, refusing
any output entry created concurrently. macOS and Linux are supported; other
platforms or filesystems without exclusive rename fail closed.
Explicit `--source` Pages staging copies and verifies its immutable input without
creating lock files in the release bundle.
Failure leaves inputs unchanged and no partially installed release. A retry uses
a new output or removes only the failed operator-owned output after inspection.
Nothing acquires JPL data, writes `data/`, commits, dispatches Pages or deploys.

Retain `release/rollback` outside temporary staging until rollout acceptance.
It is a complete verified site snapshot, **not** a published legacy endpoint.
Retain the producer commit, descriptor/index pins and deployment identifier with
it. The prepared replacement has exactly one comet endpoint: `comets/latest.json`.
No schema-2-only index or historical staging endpoint is added to production.

## Coordinated activation (separate change)

1. Deploy Orrery's production descriptor-version dispatch at the existing URL,
   supporting schema 2 during transition and schema 3 after replacement. PR117
   alone is insufficient: its default uses the strict schema-2 reader. Update the
   About coverage text and preserve explicit bundled rollback, generation guards,
   retries, both renderers and source-specific coverage. Do not automatically
   blend fallback trajectories on verification failure.
2. Confirm the loader deployment's actual SHA and exercise the exact candidate
   through that production loader. If using temporary hosting, use the prepared
   complete site; record its URL and include removal in the activation checklist.
3. In a separately reviewed activation change, replace committed `data/comets`
   with the verified prepared `site/comets` tree. Preserve current asteroid data;
   re-run `verify-site` and full staging against the final committed tree. Existing
   Pages workflow publishes the whole site and compares the active descriptor,
   so the schema/pin replacement triggers publication. Do not copy a stale full
   prepared site over newer asteroid data. Check source pins again before commit.
4. Verify served descriptor/index and all eight payloads, bytes/hashes, CORS and
   both renderers. Check 1P start 1835-08-21, 55P start 1865-12-22, 1980 seam,
   January 2 support-only sample, 2099-12-30 end, the other four modern-only objects,
   separate path strips, hidden-path Fit, date precision and failure/retry.
   Acquisition is eager/atomic per selected object; modern-only reader independence
   does not imply app-level fallback on a failed historical segment.
5. Remove any temporary endpoint/preview deployment after acceptance. Do not add
   an enduring legacy descriptor. Retire rollback storage only after the rollout
   is accepted; old clients/open sessions may break by the approved policy.

## Rollback and subsequent refresh

During rollout, restore `rollback/comets` to the committed comet product, verify
and redeploy the complete current site; preserve newer unrelated asteroid data.
The transitional Orrery loader must still accept schema 2. For an app regression,
redeploy the known compatible app build or use its explicit bundled rollback.
If rolling back the entire Pages artifact in an emergency, the retained complete
site is available, but its asteroid snapshot may be older. Keep the exact rollback
artifact through the incident; do not rebuild it from refreshed upstream data.

Ordinary schema-2 `refresh-comets` is intentionally rejected on a schema-3 product
before acquisition. It cannot silently downgrade or prune history. Asteroid
refresh preserves the comet namespace. Subsequent history updates require a new
scientifically verified release; automated schema-3 source refresh is outside this
initial rollout. Complete-site verification excludes temporary/legacy namespaces,
so they cannot accidentally become permanent production APIs.
