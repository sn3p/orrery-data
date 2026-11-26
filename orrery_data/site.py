"""Complete-site verification at the publication boundary."""

from pathlib import Path
import shutil

from .formats import DataError
from .storage import loads_json
from .browser import managed_files as asteroid_files
from .comet_product import managed_files as comet_files, descriptor_bytes

from .browser import verify_browser
from .comet_rollout import verify_current_comets


def verify_optional_product(directory, verifier):
    if directory.exists() or directory.is_symlink():
        return verifier(directory)
    return None


MAX_SITE_BYTES = 900_000_000  # headroom below the Pages 1 GB publication limit


def verify_site(directory):
    directory = Path(directory)
    asteroids = verify_browser(directory)
    from .paired import verify_paired
    paired = verify_paired(directory)
    comets = verify_optional_product(directory / 'comets', verify_current_comets)
    total = asteroids['bytes'] + (paired['bytes'] if paired else 0) + (comets['bytes'] if comets else 0)
    if total > MAX_SITE_BYTES:
        raise DataError(f'Complete site exceeds publication byte budget ({total} > {MAX_SITE_BYTES})')
    return {
        **asteroids,
        'files': asteroids['files'] + (paired['files'] if paired else 0),
        'bytes': asteroids['bytes'] + (paired['bytes'] if paired else 0),
        'paired': paired,
        'comets': comets,
    }


def site_descriptors(directory):
    """Capture the bounded roots of both integrity chains before copying."""
    directory = Path(directory)
    asteroid_files(directory)
    comet_files(directory / 'comets')
    descriptors = {name: descriptor_bytes(directory / name)
                   for name in ('latest.json', 'comets/latest.json', 'paired/latest.json', 'paired/retention.json')}
    if descriptors['latest.json'] is None:
        raise DataError('Missing asteroid descriptor')
    for value in descriptors.values():
        if value is not None:
            loads_json(value)
    return descriptors


def copy_site_snapshot(source, destination):
    """Copy under the caller's source lock, or validate an immutable input copy.

    Exact descriptor bytes bind the copied index and every referenced payload.
    The caller must verify the copied site after releasing its source lock.
    Preserve symlinks so verification rejects them rather than following them.
    """
    expected = site_descriptors(source)
    shutil.copytree(source, destination, symlinks=True)
    if site_descriptors(destination) != expected:
        raise DataError('Site descriptors changed during snapshot')
