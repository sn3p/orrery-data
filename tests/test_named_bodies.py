import gzip
import hashlib
import json
import math
from pathlib import Path
import unittest
from orrery_data.formats import DataError
from orrery_data.named_bodies import IDS, START, END, position, validate_definitions

ROOT=Path(__file__).resolve().parents[1]

class NamedBodies(unittest.TestCase):
    def definitions(self):return json.loads((ROOT/'research/named-bodies/definitions.json').read_bytes())

    def test_all_thirteen_against_independent_reference_and_recorded_residuals(self):
        d=validate_definitions(self.definitions());raw=(ROOT/'research/named-bodies/evidence.json.gz').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),d['evidence_sha256'])
        evidence=json.loads(gzip.decompress(raw))
        self.assertEqual([r['id'] for r in evidence],list(IDS))
        for b,source in zip(d['bodies'],evidence):
            self.assertEqual(b['sources'],source['sources'])
            training={r[0] for r in source['fit'][1::2]}
            self.assertFalse(training & {r[0] for r in source['validation']})
            self.assertEqual(len(source['validation']),b['validation']['samples'])
            errors=[]; angles=[]
            for jd,x,y,z in source['validation']:
                actual=position(b,jd);self.assertEqual(actual['status'],'validated-interval')
                errors.append(math.dist(actual['position'],[x,y,z]))
                xyz=actual['position']; ref=[x,y,z]
                # atan2(cross, dot) stays well-conditioned at small angles;
                # acos(dot / norms) amplifies platform rounding near cosine=1.
                cross=[xyz[1]*ref[2]-xyz[2]*ref[1],
                       xyz[2]*ref[0]-xyz[0]*ref[2],
                       xyz[0]*ref[1]-xyz[1]*ref[0]]
                dot=math.fsum(a*b for a,b in zip(xyz,ref))
                angles.append(math.degrees(math.atan2(math.hypot(*cross),dot)))
            self.assertAlmostEqual(max(errors),b['validation']['max_position_error_au'],places=12)
            # Recorded evidence used acos. Allow 1e-9 degrees (3.6 microarcseconds)
            # for its rounding, independently of the model's scientific error.
            # Linux CI differed by 5.32e-11 degrees for Haumea with identical inputs.
            self.assertAlmostEqual(max(angles),b['validation']['max_direction_error_degrees'],
                                   delta=1e-9,msg=b['name'])

    def test_extrapolation_keeps_moving_and_is_continuous_without_invalid_shape(self):
        for b in self.definitions()['bodies']:
            for boundary in [START,END]:
                p=position(b,boundary)['position']
                for delta in [-1e-6,1e-6]:
                    self.assertLess(sum((a-c)**2 for a,c in zip(p,position(b,boundary+delta)['position'])),1e-10)
            for jd in [-1930999.5,5373484.5]:
                a=position(b,jd);self.assertEqual(a['status'],'approximate-extrapolation')
                self.assertNotEqual(a['position'],position(b,jd+20)['position'])
        for jd in [True,float('nan'),float('inf')]:
            with self.assertRaises(DataError):position(b,jd)

    def test_scientific_source_rejects_wrong_units_target_epoch_grid_and_query(self):
        from orrery_data.named_body_sources import parse_source
        d=self.definitions()['bodies'][8]
        for kind,suffix in [('elements','elements'),('fit','vectors')]:
            raw=gzip.decompress((ROOT/('research/named-bodies/ceres-'+suffix+'-raw.json.gz')).read_bytes())
            url=d['sources'][kind]['url']
            parse_source(raw,url,8,kind)
            for old,new in [('Output units    : AU-D','Output units    : KM-S'),
                            ('1 Ceres (A801 AA)','2 Ceres (A801 AA)'),
                            ('Ecliptic of J2000.0','Equator of J2000.0'),
                            ('GEOMETRIC','APPARENT')]:
                changed=json.loads(raw);self.assertIn(old,changed['result']);changed['result']=changed['result'].replace(old,new)
                with self.assertRaises(DataError):parse_source(json.dumps(changed).encode(),url,8,kind)
            changed=json.loads(raw);head,table=changed['result'].split('$$SOE');lines=table.splitlines()
            first=next(i for i,line in enumerate(lines) if line.strip())
            lines[first]='2300000.5,'+lines[first].split(',',1)[1]
            changed['result']=head+'$$SOE'+'\n'.join(lines)
            with self.assertRaises(DataError):parse_source(json.dumps(changed).encode(),url,8,kind)
            with self.assertRaises(DataError):parse_source(raw,url.replace('AU-D','KM-S'),8,kind)

    def test_malformed_source_failures_are_data_errors(self):
        from orrery_data.named_body_sources import parse_source
        url=self.definitions()['bodies'][8]['sources']['elements']['url']
        for raw in [b'{',b'[]',b'null',b'3',b'"text"',b'\xff']:
            with self.subTest(raw=raw), self.assertRaises(DataError):parse_source(raw,url,8,'elements')
        raw=gzip.decompress((ROOT/'research/named-bodies/ceres-elements-raw.json.gz').read_bytes())
        envelope=json.loads(raw);head,table=envelope['result'].split('$$SOE')
        rows=table.splitlines();n=next(i for i,line in enumerate(rows) if line.strip())
        cells=rows[n].split(',');cells[2]='not-a-number';rows[n]=','.join(cells)
        envelope['result']=head+'$$SOE'+'\n'.join(rows)
        with self.assertRaises(DataError):parse_source(json.dumps(envelope).encode(),url,8,'elements')

    def test_corrupt_definition_rejected(self):
        for change in [lambda d:d['bodies'].pop(),lambda d:d['bodies'][0].update(id=IDS[1]),
                       lambda d:d['bodies'][2].update(centre_kind='system-barycentre'),
                       lambda d:d['bodies'][0]['rates'].__setitem__(1,2),
                       lambda d:d.update(extra=1)]:
            d=self.definitions();change(d)
            with self.assertRaises(DataError):validate_definitions(d)
