"""Comet v1 entry points; shared mechanics live in comet_product."""

from .comet_product import CometContract, CometProduct
from .comet_product import (START, STEP, COUNT, TOLERANCE, CONVENTIONS, interpolate,
                            managed_files, query)
from .horizons import TARGETS

CONTRACT = CometContract(1, fixed_targets=TARGETS)
PRODUCT = CometProduct(CONTRACT, TARGETS)

validate_object = PRODUCT.validate_object
verify_comets = PRODUCT.verify_comets
build_candidate = PRODUCT.build_candidate
refresh_comets = PRODUCT.refresh_comets
