"""Comet v2 entry points; shared mechanics live in comet_product."""

from . import comet_product as product
from .comet_product import (START, STEP, COUNT, TOLERANCE, CONVENTIONS, interpolate,
                            managed_files, query)
from .horizons import TARGETS as SEEDS, acquire

TARGETS = {**SEEDS, "9P": "9P/Tempel 1", "10P": "10P/Tempel 2", "67P": "67P/Churyumov-Gerasimenko"}

CONTRACT = product.CometContract(2)


def validate_object(value, object_id):
    return product.validate_object(value, object_id, contract=CONTRACT)


def verify_comets(directory, *, allow_orphans=False):
    return product.verify_comets(directory, contract=CONTRACT, allow_orphans=allow_orphans)


def build_candidate(records, stage, previous=None):
    return product.build_candidate(records, stage, previous, contract=CONTRACT, targets=TARGETS)


def refresh_comets(cache, output, *, refresh=False, offline=False):
    return product.refresh_comets(
        cache, output,
        acquire_records=lambda: acquire(cache, refresh=refresh, offline=offline, targets=TARGETS),
        build=build_candidate, verify=verify_comets)
