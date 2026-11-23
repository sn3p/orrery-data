"""Comet boundary coverage using the shared cases."""
import unittest
from comet_cases import CometCases


class CometTests(CometCases, unittest.TestCase):
    pass


if __name__ == '__main__':
    unittest.main()
