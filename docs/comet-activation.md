# Historical comet activation record

Prepared 2026-11-24 from producer `b0909248536811f65be3d4f5405041cf83ef59f6`
(merged PR17). **Deployment and candidate acceptance gates passed.** The PR
remains draft for review and final CI; merge/deployment require separate explicit
authorization. No deployment is part of this change.

## Replacement and preservation

- Sole public endpoint: `comets/latest.json`, schema 3.
- Approved index: `a9de1f2f711dd65e880102de7c1f0c14bf20598a20d9cc4e001081406e40c2e2`.
- Previous schema-2 index: `b1b10e6d1fba1cb94cd1e03c2e39ea7ec93b584a89f74ece193dcdf8ba379567`.
- Asteroid index: `e33e6b13a0d2ff5fd5afa56788f903203aad13ee267bce1acb09d6778a3dbd10`;
  all 118 asteroid files preserved byte-for-byte (895,910 records, 114 chunks).
- Ten comet files: descriptor, approved index and eight payloads. All six modern
  payloads retain their original bytes; only two historical payloads are added.
  The obsolete public schema-2 index is removed.
- Complete `verify-site` and immutable-source Pages staging passed. No temporary
  endpoint or preview deployment was created.

A verified complete pre-activation snapshot and SHA-256 manifest are retained in
local durable feature storage under `data/artifacts/20261124-cancun-activation/`,
outside the worktree. Keep it through rollout acceptance. Recovery does **not**
require access to that Mac: Git retains the exact previous comet product at
`b0909248536811f65be3d4f5405041cf83ef59f6`. The extraction below was verified
byte-for-byte against the retained snapshot, then staged as a complete current
site. The committed schema-2 test fixture is never a public endpoint.

## Portable rollback preparation

Run from a fresh workspace based on current master; fetch history if this is a
shallow clone. This only prepares a local rollback artifact. It does not modify
committed data, merge, publish or deploy.

```sh
mkdir -p .context
rollback_work=$(mktemp -d .context/comet-rollback.XXXXXX)
git archive b0909248536811f65be3d4f5405041cf83ef59f6 data/comets | tar -x -C "$rollback_work"
python3 -m orrery_data verify-comets --directory "$rollback_work/data/comets"
python3 - "$rollback_work" <<'PYTHON'
import json, shutil, sys
from pathlib import Path
root = Path(sys.argv[1])
comets = root / 'data/comets'
assert json.loads((comets / 'latest.json').read_bytes())['index']['sha256'] == \
    'b1b10e6d1fba1cb94cd1e03c2e39ea7ec93b584a89f74ece193dcdf8ba379567'
# Copy current asteroid data, never the old full-site snapshot.
shutil.copytree('data', root / 'site',
                ignore=lambda directory, names: ['comets'] if Path(directory) == Path('data') else [])
shutil.copytree(comets, root / 'site/comets')
PYTHON
python3 -m orrery_data verify-site --directory "$rollback_work/site"
python3 scripts/prepare_browser_site.py --source "$rollback_work/site" --output "$rollback_work/pages"
```

For an authorized rollback PR, replace only committed `data/comets` with this
verified `site/comets`, recheck current asteroid pins and rerun complete-site
verification/staging. Never overlay an old full site onto newer asteroids. Keep
Git source commit, comet pin and deployment identifier with the recovery record.
The deployed transitional loader must still accept schema 2; confirm that before
publishing rollback. Merge/deployment need separate explicit authorization.

## Loader gate and remaining acceptance

Orrery PR118 merged as `445c599351b5b3aee98c3518dd9b7451e4483895`.
Production deployment **8039291024** reports success for that exact SHA
([Pages run](https://github.com/sn3p/Orrery/actions/runs/45475046532)).
Direct served HTML inspection confirms `assets/main.7a6135fb.js`.
This supersedes the preparation-time observation of the older bundle.

The deployed app passed isolated acceptance in **Chromium and WebKit**, each
starting in **Pixi and Three** and switching renderers. App HTML/JS/assets were
loaded from production, unchanged; only data requests were intercepted with the
exact complete replacement site. No local server, personal browser, screenshots,
temporary hosting or preview deployment was used.

All four cases passed with zero page errors: all eight payloads and exact source
pin, historical starts, 1980 seam ownership, January 2 support-only sample,
2099-12-30 end, four modern-only objects, disconnected paths, hidden-path Fit,
metadata date precision, failure/retry, schema-2 restoration and explicit bundled
rollback. About/timeline layout, accessible copy and keyboard focus passed at
320×568 and 1280×800. A 30-second harness timeout at the full-catalogue end-date
jump was diagnosed as streaming latency: the requested date committed with no
render failure. The complete matrix passed with a 180-second seek timeout; no
application behavior was bypassed. This is functional acceptance, not a latency
or subjective visual-quality claim.

Source pins were rechecked against current `origin/master` and live descriptors:
asteroids remain `e33e6b13…8a3dbd10`, live comets remain schema 2 `b1b10e6d…ba379567`.
All 118 current asteroid files remain byte-identical. No new source data was
acquired. Evidence and reproducible isolated scripts are retained with the local
activation record; the public source/deployment identifiers above are portable.

Remaining release steps:

1. Review this draft and require final-head CI success. Recheck source pins at
   approval/merge time; if asteroids refreshed, retain those current files and
   repeat whole-site staging, never overlay an old complete site.
2. Only after explicit merge/deploy authorization, verify the **served schema-3**
   descriptor/index and all eight payload bytes/hashes, CORS and both renderers.
   Intercepted candidate acceptance does not claim that data is already public.
3. Remove temporary hosting if later introduced; none exists from this work.
   Retain rollback through acceptance.

Rollback restores only retained `rollback/comets` over the comet product, then
verifies/stages the complete current site while preserving newer asteroid data.
Use a fresh reviewed change; deploying rollback also requires authorization.
No permanent schema-2 public endpoint or automatic fallback blending is introduced.

## Verification and limits

Initial activation passed 170 server-free Python tests and 49 Node tests. The
review fixes passed 80 focused Python tests and 50 Node tests, including baseline
pin mutation, writer/alias rejection, both schema evidence paths, and publication
consumers. The affected Node suites pass from `/tmp`; generic history acceptance
also passes a reader-compatible changed index pin while initial-release-only byte
reuse remains explicitly scoped. Both actual no-argument script commands pass
with retained raw inputs; candidate and midpoint evidence bytes remain unchanged.
Whole-site verification, full Pages staging and the portable rollback procedure
pass. Independent script/test review found no remaining issue.

Selected PR CI is Python 3.11, 3.12, 3.13 and browser delivery. The initial PR18
head passed all four jobs; review-fix head status is reported separately at
publication without waiting. Full Python discovery includes HTTP-server fixtures;
Node browser-source and Playwright delivery likewise start servers, and Playwright
captures images. These prohibited local boundaries remain GitHub CI checks.
Future served schema-3 hashes/CORS checks necessarily follow an authorized data
deployment; isolated candidate interception does not establish that public state.
