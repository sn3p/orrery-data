"""Comet v2 boundary coverage using the shared cases."""
import unittest
from comet_cases import PreflightCases


class CometPreflight(PreflightCases, unittest.TestCase):
    pass


if __name__ == '__main__':
    unittest.main()
