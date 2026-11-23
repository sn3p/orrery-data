"""Comet v2 entry points; shared mechanics live in comet_product."""

from .comet_product import CometContract, CometProduct
from .comet_product import (START, STEP, COUNT, TOLERANCE, CONVENTIONS, interpolate,
                            managed_files, query)
from .horizons import TARGETS as SEEDS

TARGETS = {**SEEDS, "9P": "9P/Tempel 1", "10P": "10P/Tempel 2", "67P": "67P/Churyumov-Gerasimenko"}

CONTRACT = CometContract(2)
PRODUCT = CometProduct(CONTRACT, TARGETS)

validate_object = PRODUCT.validate_object
verify_comets = PRODUCT.verify_comets
build_candidate = PRODUCT.build_candidate
refresh_comets = PRODUCT.refresh_comets
