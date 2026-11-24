"""Shared parser and Hermite boundaries used by public and research producers."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from orrery_data.formats import DataError
from orrery_data import horizons
from orrery_data.comet_product import interpolate, interpolate_state

ROOT = Path(__file__).resolve().parents[1]


class HistoryScienceHelpersTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / 'tests/fixtures/horizons-1p.json').read_bytes()
        self.options = dict(name='1P/Halley', solution='JPL#75', start=2444239.5,
                            step=4, count=3, start_date='1980-Jan-01', stop_date='2100-Jan-01')

    def test_generic_parser_rejects_inconsistent_header_endpoints(self):
        rows, source = horizons.parse_vector_response(self.raw, **self.options)
        self.assertEqual(len(rows), 3)
        self.assertEqual(source['solution'], 'JPL#75')
        for field, wrong in [('start_date', '1980-Jan-02'), ('stop_date', '2099-Dec-30')]:
            with self.subTest(field=field), self.assertRaisesRegex(DataError, 'requested dates'):
                horizons.parse_vector_response(self.raw, **{**self.options, field: wrong})

    def test_production_wrapper_uses_same_grid_and_date_validator(self):
        with patch.object(horizons, 'parse_vector_response', return_value='parsed') as shared:
            self.assertEqual(horizons.parse_response(self.raw, '1P', targets=horizons.TARGETS), 'parsed')
            self.assertEqual(shared.call_args.kwargs, dict(name='1P/Halley', start=2444239.5,
                step=4, count=10958, start_date='1980-Jan-01', stop_date='2100-Jan-01'))
            horizons.parse_response(self.raw, '1P', midpoint=True, targets=horizons.TARGETS)
            self.assertEqual(shared.call_args.kwargs, dict(name='1P/Halley', start=2444241.5,
                step=4, count=10957, start_date='1980-Jan-03', stop_date='2099-Dec-30'))

    def test_malformed_response_types_and_numeric_cells_raise_data_error(self):
        for raw in [None, b'{', b'\xff']:
            with self.subTest(raw=raw), self.assertRaises(DataError):
                horizons.parse_vector_response(raw, **self.options)
        original = json.loads(self.raw)
        for envelope in [None, [], {'signature': original['signature'], 'result': []},
                         {'signature': original['signature'], 'result': None}]:
            with self.subTest(envelope=envelope), self.assertRaises(DataError):
                horizons.parse_vector_response(json.dumps(envelope).encode(), **self.options)
        envelope = json.loads(self.raw)
        head, table = envelope['result'].split('$$SOE')
        table = table.replace('2444239.500000000', 'not-a-number', 1)
        envelope['result'] = head + '$$SOE' + table
        with self.assertRaisesRegex(DataError, 'numeric state'):
            horizons.parse_vector_response(json.dumps(envelope).encode(), **self.options)

    def test_shared_position_and_velocity_reproduce_independent_polynomials(self):
        # x=t^3, y=2t^2+3t+1, z=-t+4 on [0,4].
        series = {'start': 100, 'step': 4,
                  'states': [[0,1,4,0,3,-1], [64,45,0,48,19,-1]]}
        for t in [0, .5, 1, 2, 3, 4]:
            with self.subTest(t=t):
                position, velocity = interpolate_state(series, 100+t)
                self.assertEqual(position, [t**3, 2*t*t+3*t+1, -t+4])
                self.assertEqual(velocity, [3*t*t, 4*t+3, -1])
                self.assertEqual(interpolate(series, 100+t), position)
        for jd in [99, 105, float('nan'), float('inf'), None, '100']:
            with self.subTest(jd=jd), self.assertRaises(DataError):
                interpolate_state(series, jd)


if __name__ == '__main__':
    unittest.main()
