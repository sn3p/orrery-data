"""Authenticate the frozen modern baseline before historical scientific replay."""
from pathlib import Path

from orrery_data.comet_product import CometContract, inspect_comets
from orrery_data.formats import DataError


ROOT = Path(__file__).resolve().parents[1]
SCHEMA2_PIN = 'b1b10e6d1fba1cb94cd1e03c2e39ea7ec93b584a89f74ece193dcdf8ba379567'
SCHEMA2_BASELINE = ROOT / 'tests/fixtures' / ('snapshot-v1-' + SCHEMA2_PIN)

def authenticate_baseline(report):
    if report['pin']['sha256'] != SCHEMA2_PIN:
        raise DataError('Frozen schema-2 baseline differs from approved index pin')


def verify_baseline(directory=SCHEMA2_BASELINE):
    report, index = inspect_comets(directory, contract=CometContract(2))
    authenticate_baseline(report)
    return report, index
