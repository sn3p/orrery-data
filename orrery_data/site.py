"""Complete-site verification at the publication boundary."""

from pathlib import Path

from .browser import verify_browser
from .comets import verify_comets


def verify_site(directory):
    directory = Path(directory)
    asteroids = verify_browser(directory)
    comets = directory / 'comets'
    return {**asteroids, 'comets': verify_comets(comets) if comets.exists() or comets.is_symlink() else None}
