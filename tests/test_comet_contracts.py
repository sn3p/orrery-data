"""Shared mechanics retain separate publication contracts."""
from pathlib import Path
import unittest
from orrery_data import comets, comets_v2
from orrery_data.formats import DataError

ROOT = Path(__file__).resolve().parents[1]

class ContractIsolation(unittest.TestCase):
    def test_versions_cannot_verify_each_others_product(self):
        for module, directory, count in [(comets,'comets',3), (comets_v2,'comets-v2',6)]:
            self.assertEqual(module.verify_comets(ROOT/'data'/directory)['objects'], count)
            other = 'comets-v2' if directory == 'comets' else 'comets'
            with self.assertRaises(DataError):
                module.verify_comets(ROOT/'data'/other)
