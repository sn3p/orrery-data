"""Complete-site verification at the publication boundary."""

from pathlib import Path

from .browser import verify_browser
from .comets import verify_comets
from .comets_v2 import verify_comets as verify_comets_v2


def verify_site(directory):
    directory = Path(directory)
    asteroids = verify_browser(directory)
    comets = directory / 'comets'
    v2 = directory / 'comets-v2'
    return {**asteroids, 'comets_v2': verify_comets_v2(v2) if v2.exists() or v2.is_symlink() else None, 'comets': verify_comets(comets) if comets.exists() or comets.is_symlink() else None}
