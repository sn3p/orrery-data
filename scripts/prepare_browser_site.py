#!/usr/bin/env python3
"""Verify committed browser data and stage a complete Pages artifact."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orrery_data.storage import writer_lock, site_lock_path
# Keep in sync with the workflow's non-data push paths. The CLI imports the
# producer package transitively; no installation or external config is needed.
PUBLICATION_PATHS = ('.github/workflows/browser-pages.yml', 'scripts/prepare_browser_site.py', 'orrery_data')


def remote_descriptor(url, *, optional=False):
    """Read a bounded descriptor; only optional products may be absent."""
    try:
        with urlopen(url, timeout=20) as response:
            return json.loads(response.read(4097))
    except HTTPError as exc:
        if not optional or exc.code != 404:
            raise
        exc.close()
        return None


def published_site_matches(source, latest_url):
    """Every product must match, including optional product presence/removal."""
    descriptors = ('latest.json', 'comets/latest.json')
    try:
        for name in descriptors:
            optional = name != 'latest.json'
            path = source / name
            local = None if optional and not path.exists() else json.loads(path.read_text())
            url = urljoin(latest_url, name) if optional else latest_url
            remote = remote_descriptor(url, optional=optional)
            if local != remote:
                return False
        return True
    except (OSError, ValueError):
        # Unavailable/unconfigured host: prepare a complete candidate normally.
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='verified complete site to stage (default: repository data)')
    parser.add_argument('--output', type=Path, required=True, help='new, empty staging directory')
    parser.add_argument('--latest-url', help='optional trusted published descriptor to detect an unchanged deployment')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--publication-base', help='push before commit; redeploy when publication code changed since it')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        parser.error('staging output must not exist')
    source = (args.source if args.source is not None else ROOT / 'data').resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        parser.error('staging output must not overlap source data')
    with writer_lock(site_lock_path(source)):
        result = subprocess.run([sys.executable, '-m', 'orrery_data', 'verify-site', '--directory', source],
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
        unchanged = bool(args.latest_url and not force
                         and published_site_matches(source, args.latest_url))
        if not unchanged:
            shutil.copytree(source, output)
            (output / '.nojekyll').write_text('')
        print(json.dumps({**verified, 'status': 'unchanged' if unchanged else 'prepared', 'output': str(output)}))
        return 0



if __name__ == '__main__':
    raise SystemExit(main())
