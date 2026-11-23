"""Complete-site verification at the publication boundary."""

from pathlib import Path

from .browser import verify_browser
from .comets import verify_comets
from .comets_v2 import verify_comets as verify_comets_v2


def verify_optional_product(directory, verifier):
    if directory.exists() or directory.is_symlink():
        return verifier(directory)
    return None


def verify_site(directory):
    directory = Path(directory)
    asteroids = verify_browser(directory)
    return {
        **asteroids,
        'comets_v2': verify_optional_product(directory / 'comets-v2', verify_comets_v2),
        'comets': verify_optional_product(directory / 'comets', verify_comets),
    }
