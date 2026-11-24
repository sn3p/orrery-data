# Historical comet activation record

Prepared 2026-11-24 from producer `b0909248536811f65be3d4f5405041cf83ef59f6`
(merged PR17). This is a draft activation, **not ready to deploy** until the
remaining gates below pass. No deployment is part of this change.

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
outside the worktree. This storage is local to the operator's Mac, not GitHub or
cross-machine backup. The workspace `.context/comet-rollout/` records preparation,
verification and preservation evidence. Keep the rollback until rollout acceptance.
The committed `tests/fixtures/comets-schema2/` is test input, never a Pages endpoint.
It preserves transition regressions; it is not the complete rollback snapshot.

## Loader gate and remaining acceptance

Orrery PR118 merged as `445c599351b5b3aee98c3518dd9b7451e4483895`.
At preparation, the latest successful GitHub Pages deployment was `8036893810`,
SHA `82206260081d747827cdd9321c1e21fe31ab2c4e` (before PR118).
PR118 Pages run `45475046532` was in progress; it was not polled or awaited.
The served HTML referenced `main.75ebc267.js`, whereas the retained PR118 build
report references `main.7a6135fb.js`. **Merged does not mean deployed.**

Before marking activation ready:

1. Confirm successful production deployment of PR118 or a verified descendant,
   record its SHA/deployment identifier, and verify the served build.
2. Exercise this exact complete replacement through that deployed loader in both
   renderers, covering the [rollout acceptance matrix](comet-production-rollout.md):
   historical starts, 1980 ownership seam, support-only January 2 sample, final
   December 30 date, modern-only objects, separate paths, hidden-path Fit, metadata
   precision, failure/retry and explicit bundled rollback. Prior app build checks
   are evidence for the merged tree, not deployed acceptance.
3. Recheck current master and live source pins before approval/merge. If asteroids
   have refreshed, preserve those current files and repeat whole-site staging;
   never replace them with the prepared snapshot.
4. Only after explicit merge/deploy authorization, verify served descriptor/index
   and all eight payloads against exact bytes/hashes, CORS and both renderers.
   Remove temporary hosting if later introduced; retain rollback through acceptance.

Rollback restores only retained `rollback/comets` over the comet product, then
verifies/stages the complete current site while preserving newer asteroid data.
Use a fresh reviewed change; deploying rollback also requires authorization.
No permanent schema-2 public endpoint or automatic fallback blending is introduced.

## Verification limits

Selected PR CI is Python 3.11, 3.12, 3.13 and browser delivery. Local verification
passed 170 server-free Python 3.12 tests and 49 Node tests, including the real
serialized release reader, complete-site verification and staging. Offline raw
scientific replay reproduced all ten committed comet files exactly. An independent
pass verified exact inventory/hashes, unchanged asteroids and rollback retention. Full Python discovery includes HTTP-server
fixtures; Node browser-source and Playwright delivery likewise start servers and
Playwright captures images. Those boundaries are left to GitHub CI under the
operator's no-local-server/no-image rules. CI status is reported at publication,
without waiting. Deployed renderer/CORS acceptance remains explicitly outstanding.
