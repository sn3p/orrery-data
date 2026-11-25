#!/usr/bin/env python3
"""Assemble a pinned unpublished candidate from committed, compressed evidence.

No acquisition, refresh, install or publish path. Existing candidates are immutable.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orrery_data.storage import writer_lock, rename_new

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'research/comet-expansion'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _build(output):
    output = Path(output).absolute()
    namespace = ROOT / '.context/comet-candidates'
    if namespace.resolve() != namespace:
        raise ValueError('Candidate namespace must not contain symlinks')
    namespace.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or output.parent.resolve() != namespace:
        raise ValueError('Candidate must be a direct directory under .context/comet-candidates')
    fixture = json.loads((FIXTURE / 'fixture.json').read_bytes())
    files = {}
    legacy = fixture['legacy']
    raw = (ROOT / 'data/comets' / legacy['url']).read_bytes()
    if digest(raw) != legacy['sha256'] or len(raw) != legacy['bytes']:
        raise ValueError('Production baseline changed; reassess explicitly')
    files[legacy['url']] = raw
    for obj in json.loads(raw)['objects']:
        for segment in obj['segments']:
            ref = segment['data']
            raw = (ROOT / 'data/comets' / ref['url']).read_bytes()
            if digest(raw) != ref['sha256'] or len(raw) != ref['bytes']:
                raise ValueError('Corrupt legacy payload')
            files[ref['url']] = raw
    for name, item in fixture['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or Path(item['file']).name != item['file']:
            raise ValueError('Invalid fixture path')
        compressed = (FIXTURE / 'payloads' / item['file']).read_bytes()
        if digest(compressed) != item['gzip_sha256']:
            raise ValueError('Corrupt compressed fixture')
        raw = gzip.decompress(compressed)
        if digest(raw) != item['sha256'] or len(raw) != item['bytes']:
            raise ValueError('Corrupt decoded fixture')
        if name in files:
            raise ValueError('Fixture cannot replace legacy assets')
        files[name] = raw
    descriptor = json.loads(files['latest.json'])
    if descriptor != {'comet_contract_version': 4, 'index': fixture['candidate']}:
        raise ValueError('Candidate pin differs from descriptor')
    candidate = fixture['candidate']
    if candidate['url'] not in files or digest(files[candidate['url']]) != candidate['sha256'] or len(files[candidate['url']]) != candidate['bytes']:
        raise ValueError('Invalid candidate index pin')
    with tempfile.TemporaryDirectory(prefix='.directory-', dir=namespace) as temporary:
        stage = Path(temporary) / 'candidate'
        stage.mkdir()
        for name, raw in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        subprocess.run(['node', str(ROOT / 'scripts/verify_comet_directory.mjs'), str(stage)], check=True, cwd=ROOT)
        if namespace.resolve() != namespace or output.parent.resolve() != namespace or output.is_symlink():
            raise ValueError('Candidate path changed during preparation')
        if output.exists() and not output.is_dir():
            raise ValueError('Candidate output is not a directory')
        if output.exists():
            actual = {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob('*') if p.is_file()}
            if any(p.is_symlink() for p in output.rglob('*')) or actual != files:
                raise ValueError('Existing candidate differs; immutable output retained')
        else:
            rename_new(stage, output)
    return {'directory': str(output), 'index': fixture['candidate'], 'files': len(files)}


def build(output):
    namespace = ROOT / '.context/comet-candidates'
    if namespace.resolve() != namespace:
        raise ValueError('Candidate namespace must not contain symlinks')
    namespace.mkdir(parents=True, exist_ok=True)
    with writer_lock(namespace):
        return _build(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.context/comet-candidates' / ('directory-v4-' + json.loads((FIXTURE / 'fixture.json').read_bytes())['candidate']['sha256'][:12]))
    args = parser.parse_args()
    print(json.dumps(build(args.output)))
