# Expanded comet directory activation

Prepared against producer master `53a6ce491c5b5a119236fc629e54449bf15920a5`
(PR19 merged). This is a **draft activation change**, not a deployed release.
Merge and activation require separate explicit authorization. A merge to master
triggers the existing Pages publication workflow, so merge is the release step.

## Exact release and deployed-reader gate

- Endpoint: `comets/latest.json`, contract 4.
- Candidate index: `907f8cb552c65db81eb3ec3167b0d422e764ab5fef1fcbbb948e63cae21f1070`.
- 4,089 searchable entries, fourteen trajectories, 27 files: descriptor, two
  indexes, eight manifests, sixteen payloads. All prior eight payloads and the
  schema-3 index remain byte-identical. Defaults remain `1P, 2P, 55P, 9P, 10P, 67P`.
- Previous index: `a9de1f2f711dd65e880102de7c1f0c14bf20598a20d9cc4e001081406e40c2e2`.
- Asteroid files are unchanged. Always preserve current asteroids when preparing
  activation or rollback; never restore an old complete site over newer data.

[Orrery PR122](https://github.com/sn3p/Orrery/pull/122) merged as
`6e3d88db5585abc46b27daaed9b5012224bd1b85`.
[Pages run 45636961280](https://github.com/sn3p/Orrery/actions/runs/45636961280)
succeeded. The app owner's 25 November deployed acceptance record verified twenty
served app files, ten current schema-3 files, and Chromium/WebKit in Pixi/Three
against both current production and this exact intercepted expanded candidate.
Eight primary and eight boundary/interaction scenarios passed, covering defaults,
selection, status copy, aliases, discovery/seams, Fit, retry and rollback.
Firefox and subjective image comparison were not exercised in that deployed pass.
This establishes the accepting-reader gate; it does not establish live v4 data.
No Orrery3D adoption or change to popular/currently-active defaults is implied.

## Publication safety and local verification

The Python publication gate accepts only this exact v4 index and authenticates
its entire transitive inventory, including the retained legacy index, manifests
and payloads. Unapproved pins, corruption, missing/extra files and symlinks fail
closed. Publication remains Python-only. Schema 2/3 verification remains available
for recovery. Ordinary refresh cannot overwrite immutable schema 3 or 4 products.

The candidate builder and Node verifier replay all 87,656 retained independent
midpoints for the eight additions. This measures interpolation fidelity, not
physical uncertainty. Whole-site verification and Pages staging must pass for
both release and rollback. Tests exercise real CLI preparation, stale source pins,
writer guards, exact file preservation and invalid-release rejection.

Selected PR CI is Python 3.11/3.12/3.13 (including complete-site verification) and
browser delivery (Node plus Chromium/Firefox/WebKit). Local server-free Python
and Node suites supplement it. Full local Python discovery and server-backed
browser workflows are excluded under the user's server/image restrictions; their
CI status must be reported separately, without treating a pending job as passing.

## Portable schema-3 rollback

Keep rollback through live acceptance. The exact preceding comet tree is retained
in Git at the base commit above; no access to a particular Mac is required.
Run from a fresh workspace based on current master after fetching that commit:

```sh
mkdir -p .context
rollback_work=$(mktemp -d .context/directory-rollback.XXXXXX)
git archive 53a6ce491c5b5a119236fc629e54449bf15920a5 data/comets | tar -x -C "$rollback_work"
python3 -m orrery_data verify-comets --directory "$rollback_work/data/comets"
python3 - "$rollback_work" <<'PYTHON'
import json, shutil, sys
from pathlib import Path
root = Path(sys.argv[1])
assert json.loads((root/'data/comets/latest.json').read_bytes())['index']['sha256'] == \
    'a9de1f2f711dd65e880102de7c1f0c14bf20598a20d9cc4e001081406e40c2e2'
shutil.copytree('data', root/'site', ignore=lambda directory, names:
                ['comets'] if Path(directory) == Path('data') else [])
shutil.copytree(root/'data/comets', root/'site/comets')
PYTHON
python3 -m orrery_data verify-site --directory "$rollback_work/site"
python3 scripts/prepare_browser_site.py --source "$rollback_work/site" --output "$rollback_work/pages"
```

This prepares local recovery only. For an explicitly authorized rollback, use a
fresh reviewed PR replacing **only** `data/comets` with the verified rollback
product, removing v4-only files. Preserve current asteroid files, recheck deployed
reader schema-3 support, and repeat complete-site staging. Keep the rollback PR,
source commit, index pin and deployment ID in the recovery record. The older
schema-2/bundled fallback remains documented in `comet-activation.md`.

## Post-activation verification (pending authorization and deployment)

1. Before approval, reconcile current master again, require final-head CI success,
   recheck the deployed accepting app and current live descriptors, and preserve
   any newer asteroid refresh. A changed comet baseline requires reassessment.
2. After the authorized merge, record the merge SHA and successful Pages
   deployment SHA/run/ID. Download the served descriptor, both indexes, eight
   manifests and sixteen payloads from the sole public endpoint. Require exact
   candidate hashes and lengths, correct CORS for the app origin, and compare
   every byte against the merged tree. Re-read latest after downloads to detect
   a mixed generation; verify the downloaded complete site with current asteroids.
3. Run `node scripts/verify_comet_directory.mjs <downloaded-comet-directory>`;
   require pin `907f8cb5…1070`, 4,089 entries, fourteen trajectories and 87,656
   independent midpoint checks. Candidate interception alone cannot satisfy this.
4. Exercise the deployed app against **actual hosted data** in Pixi and Three:
   six defaults/remembered selections including empty/missing IDs; search/paging
   and aliases over all entries; all eight additions; unassessed/pending versus
   date gaps; historical starts, 1980 seam and final coverage date; paths and
   hidden-path Fit; retry, stale requests, reload/persistence and bundled rollback.
   Use isolated headless tests without personal browser sessions or image capture.
   Record app revision, live pin, engines and any unverified scenarios.
5. If integrity or app acceptance fails, retain evidence and prepare the rollback
   above for explicit authorization. Do not silently blend generations or fall
   back to an old public endpoint. Keep rollback until live acceptance is recorded.

No temporary hosting, preview deployment, live browser access, server, merge or
activation is part of this draft preparation.

Preparation results (25 November 2026): all 127 retained base files match Git
(118 asteroid files and nine legacy comet files); only the comet descriptor is
replaced. Candidate assembly, all midpoint checks, complete-site staging and the
portable Git rollback recipe passed. All 69 server-free Node tests and isolated
Chromium/WebKit rollback/production/candidate reader lanes passed. Local Firefox
failed at launch with “Could not find profile folder”; no reader execution was
claimed for that engine. The wheel build passed. Independent publication and
consumer review found no remaining issue after compatibility updates.
The server-free Python selection passed 201 distinct tests across the initial
run and targeted reruns: the first runner omitted the repository import path for
three modules, and one regression assertion retained obsolete error wording.
Corrected runner imports and the updated assertion passed; no product failure
was hidden. Full CI-equivalent coverage remains subject to the exclusions above.
