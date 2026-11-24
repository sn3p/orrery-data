"""Authenticate the frozen modern baseline before historical scientific replay."""
from orrery_data.comet_baseline import ROOT, SCHEMA2_BASELINE, SCHEMA2_PIN
from orrery_data.comet_product import CometContract, inspect_comets
from orrery_data.formats import DataError


def authenticate_baseline(report):
    if report['pin']['sha256'] != SCHEMA2_PIN:
        raise DataError('Frozen schema-2 baseline differs from approved index pin')


def verify_baseline(directory=SCHEMA2_BASELINE):
    report, index = inspect_comets(directory, contract=CometContract(2))
    authenticate_baseline(report)
    return report, index
