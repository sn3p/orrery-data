"""Repository-owned immutable inputs for replaying the first history release."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA2_BASELINE = ROOT / 'tests/fixtures/comets-schema2'
SCHEMA2_PIN = 'b1b10e6d1fba1cb94cd1e03c2e39ea7ec93b584a89f74ece193dcdf8ba379567'
