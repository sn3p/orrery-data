"""Bounded reads of the mutable roots of a published site."""
from urllib.error import HTTPError
from urllib.request import urlopen

from .formats import DataError
from .storage import loads_json

MAX_DESCRIPTOR_BYTES = 4096


def remote_descriptor(url, *, optional=False):
    """Only an optional product's 404 means absence; other failures propagate."""
    try:
        with urlopen(url, timeout=20) as response:
            raw = response.read(MAX_DESCRIPTOR_BYTES + 1)
        if len(raw) > MAX_DESCRIPTOR_BYTES:
            raise DataError('Oversized publication descriptor')
        return loads_json(raw)
    except HTTPError as exc:
        if not optional or exc.code != 404:
            raise
        exc.close()
        return None
