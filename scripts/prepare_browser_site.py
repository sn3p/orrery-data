#!/usr/bin/env python3
"""Verify committed browser data and stage a complete Pages artifact."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
# Keep in sync with the workflow's non-data push paths. The CLI imports the
# producer package transitively; no installation or external config is needed.
PUBLICATION_PATHS = ('.github/workflows/browser-pages.yml', 'scripts/prepare_browser_site.py', 'orrery_data')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new, empty staging directory')
    parser.add_argument('--latest-url', help='optional trusted published descriptor to detect an unchanged deployment')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--publication-base', help='push before commit; redeploy when publication code changed since it')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        parser.error('staging output must not exist')
    source = (ROOT / 'data').resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        parser.error('staging output must not overlap source data')
    result = subprocess.run([sys.executable, '-m', 'orrery_data', 'verify-browser', '--directory', ROOT / 'data'],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    verified = json.loads(result.stdout)
    force = args.force
    if args.publication_base is not None:
        comparison = subprocess.run(
            ['git', 'diff', '--quiet', '--no-ext-diff', args.publication_base, 'HEAD', '--', *PUBLICATION_PATHS],
            cwd=ROOT, capture_output=True)
        # An unavailable base (for example a first/force push) must not silently
        # skip a publication-code change. Local data has already passed verification.
        force = force or comparison.returncode != 0
    unchanged = False
    if args.latest_url and not force:
        try:
            with urlopen(args.latest_url, timeout=20) as response:
                current = json.loads(response.read(4097))
            unchanged = current == json.loads((ROOT / 'data/latest.json').read_text())
        except (OSError, ValueError):
            # Unavailable/unconfigured host: prepare a complete candidate normally.
            pass
    if not unchanged:
        shutil.copytree(ROOT / 'data', output)
        (output / '.nojekyll').write_text('')
    print(json.dumps({**verified, 'status': 'unchanged' if unchanged else 'prepared', 'output': str(output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
