"""Offline, release-pinned schema-3 publication and complete-site rollback.

The accepted index is the scientifically replayed PR16 candidate. Hash validation
of that exact index authenticates metadata, provenance, coverage and all eight
payload references; accepting a new index requires a new reviewed science release.
This deliberately does not turn self-reported numerical evidence into approval.
"""
from pathlib import Path
import shutil
import tempfile

from .comet_product import CometContract, managed_files, read_reference
from .formats import DataError
from .paths import validate_disjoint_paths, validate_writable_path, in_comet_candidate_namespace
from .storage import read_json, verify_file, writer_lock, site_lock_path, rename_new

HISTORY_PIN = 'a9de1f2f711dd65e880102de7c1f0c14bf20598a20d9cc4e001081406e40c2e2'


def read_comet_descriptor(directory):
    directory = Path(directory)
    names = managed_files(directory)
    if 'latest.json' not in names or (directory / 'latest.json').stat().st_size > 4096:
        raise DataError('Missing or oversized comet descriptor')
    latest = read_json(directory / 'latest.json')
    if not isinstance(latest, dict):
        raise DataError('Invalid comet descriptor')
    return names, latest


def verify_history(directory):
    directory = Path(directory)
    names, latest = read_comet_descriptor(directory)
    return inspect_history(directory, names, latest)


def inspect_history(directory, names, latest):
    CometContract(3).latest_schema(latest, 'descriptor')
    if latest['index']['sha256'] != HISTORY_PIN:
        raise DataError('Unapproved historical comet pin; scientific revalidation required')
    index = read_reference(directory, latest['index'], 'index-', 256 * 1024)
    expected = {'latest.json', latest['index']['url']}
    for entry in index['objects']:
        for segment in entry['segments']:
            ref = segment['data']
            # The trusted index fixes paths, sizes and hashes. Inventory validation
            # above excludes symlinks and unexpected directories before any read.
            verify_file(directory / ref['url'], ref)
            expected.add(ref['url'])
    if names != expected:
        raise DataError('Comet inventory differs from approved historical release')
    return dict(path=str(directory), pin=latest['index'], objects=len(index['objects']),
                files=len(names), bytes=sum((directory / name).stat().st_size for name in names))


def verify_current_comets(directory):
    directory = Path(directory)
    names, latest = read_comet_descriptor(directory)
    if latest.get('comet_contract_version') == 3:
        return inspect_history(directory, names, latest)
    from .comets import PRODUCT
    return PRODUCT.verify_comets(directory)


def prepare_rollout(site, candidate, output, expected_current_pin):
    """Create two new verified site trees; never mutate the source/public site.

    The outer output directory is installed atomically and contains site/ and
    rollback/. Keeping both in one transaction prevents a staged release without
    its verified rollback. `expected_current_pin` guards against stale preparation.
    """
    from .site import verify_site, copy_site_snapshot
    lexical_output = Path(output).absolute()
    site, candidate, output = Path(site), Path(candidate), lexical_output.resolve()
    def preflight():
        checked = validate_writable_path(output, label='Rollout output')
        if in_comet_candidate_namespace(lexical_output):
            raise DataError('Rollout output must be outside the candidate installation namespace')
        validate_disjoint_paths(checked, (site, candidate), label='Rollout output')
        if checked != output or output.exists() or output.is_symlink():
            raise DataError('Rollout output must not exist or change location')
        return checked
    output = preflight()
    verify_history(candidate)
    preflight()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.comet-rollout-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'release'
        stage.mkdir()
        # Only snapshot acquisition excludes source writers. Full validation and
        # replacement construction operate on private copies after releasing it.
        with writer_lock(site_lock_path(site.resolve())):
            copy_site_snapshot(site, stage / 'rollback')
        before = verify_site(stage / 'rollback')
        if before['comets'] is None or before['comets']['pin']['sha256'] != expected_current_pin:
            raise DataError('Current comet pin changed; reassess rollout')
        rollback = stage / 'rollback'
        shutil.copytree(rollback, stage / 'site', symlinks=True,
                        ignore=lambda directory, names: ['comets'] if Path(directory) == rollback else [])
        shutil.copytree(candidate, stage / 'site/comets', symlinks=True)
        after = verify_site(stage / 'site')
        if after['comets'] is None or after['comets']['pin']['sha256'] != HISTORY_PIN:
            raise DataError('Staged comet product differs from approved historical release')
        if after['pin'] != before['pin']:
            raise DataError('Staged asteroid snapshot changed during preparation')
        preflight()
        rename_new(stage, output)
    return dict(output=str(output), site=str(output / 'site'), rollback=str(output / 'rollback'),
                previous_pin=before['comets']['pin'], pin=after['comets']['pin'])
