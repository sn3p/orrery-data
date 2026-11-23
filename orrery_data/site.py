"""Complete-site verification at the publication boundary."""

from pathlib import Path

from .browser import verify_browser
from .comets import verify_comets


def verify_optional_product(directory, verifier):
    if directory.exists() or directory.is_symlink():
        return verifier(directory)
    return None


def verify_site(directory):
    directory = Path(directory)
    asteroids = verify_browser(directory)
    return {
        **asteroids,
        'comets': verify_optional_product(directory / 'comets', verify_comets),
    }
