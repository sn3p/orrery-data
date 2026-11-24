"""Boundary cases for the supported comet catalogue.

These mixins deliberately do not inherit TestCase: discovery runs each boundary
only through the explicit classes in test_comet*.py.
"""

from contextlib import chdir
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError

from orrery_data import comets, horizons
from orrery_data.browser import sync_candidate, verify_browser
from orrery_data.cli import main
from orrery_data.formats import DataError
from orrery_data.site import verify_site
from orrery_data.storage import read_json, write_json, writer_lock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/horizons-1p.json'
ONE_TARGET = {'1P': '1P/Halley'}


class CometFixture:
    @property
    def comets(self):
        return comets

    @property
    def product(self):
        return self.comets.PRODUCT

    @property
    def product_name(self):
        return 'comets'

    @property
    def refresh_command(self):
        return 'refresh-' + self.product_name

    @property
    def verify_command(self):
        return 'verify-' + self.product_name


class CometCases(CometFixture):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.output = self.root / 'data' / self.product_name
        self.output.parent.mkdir()
        shutil.copytree(ROOT / 'data' / self.product_name, self.output)

    def test_committed_projection_and_fresh_cli(self):
        result = self.comets.verify_comets(self.output)
        self.assertEqual(result['objects'], len(self.comets.TARGETS))
        run = subprocess.run([sys.executable, '-m', 'orrery_data', self.verify_command, '--directory', str(self.output)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), result)
        index = read_json(self.output / result['pin']['url'])
        for entry in index['objects']:
            series = read_json(self.output / entry['data']['url'])['series']
            for jd, expected in [(entry['coverage'][0], series['states'][0][:3]),
                                 (entry['coverage'][1], series['states'][-1][:3])]:
                self.assertEqual(self.comets.interpolate(series, jd), expected)
            for jd in [entry['coverage'][0] - 1, entry['coverage'][1] + 1, float('nan')]:
                with self.assertRaises(ValueError): self.comets.interpolate(series, jd)

    def test_corruption_unknown_files_symlinks_and_versions(self):
        latest = self.output / 'latest.json'
        original = latest.read_bytes()
        for value in [True, 1, 3, "2"]:
            pointer = json.loads(original); pointer['comet_contract_version'] = value
            write_json(latest, pointer)
            with self.assertRaises(DataError): self.comets.verify_comets(self.output)
        latest.write_bytes(original)
        extra = self.output / 'surprise'; extra.write_text('x')
        with self.assertRaises(DataError): self.comets.verify_comets(self.output)
        extra.unlink(); extra.symlink_to(latest)
        with self.assertRaises(DataError): self.comets.verify_comets(self.output)
        extra.unlink()
        asset = next((self.output / 'objects').iterdir()); asset.write_text('{}')
        with self.assertRaises(DataError): self.comets.verify_comets(self.output)

    def test_horizons_fixture_and_malformed_responses(self):
        envelope = read_json(FIXTURE)
        raw = json.dumps(envelope).encode()
        with patch.object(horizons, 'COUNT', 3):
            rows, source = horizons.parse_response(raw, '1P', targets=ONE_TARGET)
            self.assertEqual(len(rows), 3)
            self.assertEqual(source['solution'], 'JPL#75')
            for old, new in [('1P/Halley', '2P/Encke'), ('AU-D', 'KM-S'), ('JDUT', 'JDTDB'),
                             ('Ecliptic of J2000.0', 'ICRF'), ('GEOMETRIC', 'APPARENT'),
                             ('$$EOE', ''), ('2444239.500000000', '2444240.500000000')]:
                with self.subTest(old=old), self.assertRaises(ValueError):
                    horizons.parse_response(raw.replace(old.encode(), new.encode()), '1P', targets=ONE_TARGET)
            for malformed in [dict(envelope, error='No ephemeris'), dict(envelope, signature={'version': '9'}),
                              dict(envelope, result=envelope['result'].replace('$$EOE', '$$SOE$$EOE'))]:
                with self.assertRaises(ValueError): horizons.parse_response(json.dumps(malformed).encode(), '1P', targets=ONE_TARGET)
        with self.assertRaises(DataError): horizons.parse_response(raw, '1P', targets=ONE_TARGET)

    def test_bounded_serial_http_retry(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}
        calls = []
        def opening(request, timeout):
            calls.append(request)
            if len(calls) < 3: raise HTTPError(request.full_url, 429, 'rate limit', {}, None)
            return Response(b'{}')
        with patch.object(horizons, 'urlopen', side_effect=opening), patch.object(horizons.time, 'sleep') as sleep:
            self.assertEqual(horizons.download(horizons.query('1P')), b'{}')
            self.assertEqual(sleep.call_count, 2)
            self.assertIn('orrery-data/', calls[0].get_header('User-agent'))
            self.assertIn(horizons.CONTACT, calls[0].get_header('User-agent'))
        with patch.object(horizons, 'urlopen', return_value=Response(b'x' * (horizons.MAX_RESPONSE + 1))):
            with self.assertRaises(DataError): horizons.download(horizons.query('1P'))

    def test_failed_acquisition_and_competing_writer_preserve_product(self):
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        with patch.object(self.product, 'acquire', side_effect=DataError('second object failed')):
            with self.assertRaises(DataError): self.comets.refresh_comets(self.root / 'cache', self.output)
        def candidate(records, stage, previous):
            shutil.copytree(self.output, stage, dirs_exist_ok=True)
        with writer_lock(self.root / '.data-browser-lock'), patch.object(self.product, 'acquire') as acquire, \
             patch.object(self.product, 'build_candidate', side_effect=candidate) as build:
            with self.assertRaises(DataError): self.comets.refresh_comets(self.root / 'cache', self.output)
            acquire.assert_called_once()
            build.assert_called_once()
        after = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.comets.verify_comets(self.output)

    def test_cache_replay_corruption_and_offline_missing(self):
        cache = self.root / 'cache'
        with patch.object(horizons, 'download', return_value=b'raw-response') as download, \
             patch.object(horizons, 'parse_response', return_value=([], {})):
            horizons.acquire(cache, targets=ONE_TARGET)
            self.assertEqual(download.call_count, 2)
            before = {p.name: p.read_bytes() for p in cache.glob('*.json')}
            horizons.acquire(cache, offline=True, targets=ONE_TARGET)
            self.assertEqual(download.call_count, 2)
            self.assertEqual(before, {p.name: p.read_bytes() for p in cache.glob('*.json')})
            next(cache.glob('*.raw')).write_bytes(b'corrupt')
            with self.assertRaises(DataError): horizons.acquire(cache, offline=True, targets=ONE_TARGET)
            with self.assertRaises(DataError): horizons.acquire(self.root / 'missing', offline=True, targets=ONE_TARGET)
            self.assertEqual(download.call_count, 2)

    def test_candidate_determinism_and_scientific_provenance_changes(self):
        pin = read_json(self.output / 'latest.json')['index']
        index = read_json(self.output / pin['url'])
        records = {}
        for entry in index['objects']:
            series = read_json(self.output / entry['data']['url'])['series']
            rows = [[series['start'] + i * series['step'], *row] for i, row in enumerate(series['states'])]
            middle = [[series['start'] + i * series['step'] + 2,
                       *self.comets.interpolate(series, series['start'] + i * series['step'] + 2), 0, 0, 0]
                      for i in range(len(rows) - 1)]
            records[entry['id'] + '-vectors'] = (rows, copy.deepcopy(entry['provenance']['vectors']))
            records[entry['id'] + '-midpoints'] = (middle, copy.deepcopy(entry['provenance']['midpoints']))
        original_build = self.comets.build_candidate
        def checked_stage(records, stage, previous):
            self.assertFalse(stage.is_relative_to(self.output.parent))
            return original_build(records, stage, previous)
        with patch.object(self.product, 'acquire', return_value=records), patch.object(self.product, 'build_candidate', side_effect=checked_stage):
            # Reject a numerically invalid acquisition before changing any public bytes.
            before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
            sample = records[next(reversed(self.comets.TARGETS)) + '-midpoints'][0][0]
            original_x = sample[1]
            sample[1] += 1
            with self.assertRaisesRegex(DataError, 'interpolation exceeds'):
                self.comets.refresh_comets(self.root / 'cache', self.output)
            self.assertEqual(before, {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
            sample[1] = original_x
            first = self.comets.refresh_comets(self.root / 'cache', self.output)
            for _, source in records.values():
                source['retrieved_at'] = '2026-11-19T00:00:00Z'
                source['sha256'] = '0' * 64
            same = self.comets.refresh_comets(self.root / 'cache', self.output)
            self.assertEqual(same['status'], 'unchanged')
            self.assertEqual(first['pin'], same['pin'])
            old_header = records['1P-vectors'][1]['header']
            current_eop = re.search(r'(?m)^EOP file\s*:\s*(\S+)', old_header)[1]
            next_eop = 'eop.991231.p000401' if current_eop != 'eop.991231.p000401' else 'eop.000101.p000402'
            records['1P-vectors'][1]['header'] = old_header.replace(current_eop, next_eop)
            self.assertNotEqual(old_header, records['1P-vectors'][1]['header'])
            changed = self.comets.refresh_comets(self.root / 'cache', self.output)
            self.assertEqual(changed['status'], 'updated')
            self.assertNotEqual(changed['pin'], same['pin'])

    def test_resolved_cli_paths_short_site_lock_and_refresh_serialization(self):
        expected_site_lock = self.root / '.data-browser-lock'
        def acquiring(*args, **kwargs):
            # The site remains available during upstream acquisition.
            with writer_lock(expected_site_lock):
                pass
            return {}
        def candidate(records, stage, previous):
            self.assertFalse(stage.is_relative_to(self.output.parent))
            # Numerical candidate construction leaves the complete site available.
            with writer_lock(expected_site_lock):
                pass
            shutil.copytree(self.output, stage, dirs_exist_ok=True)
            return self.comets.verify_comets(stage)
        for spelling in (self.product_name, './' + self.product_name, str(self.output)):
            with self.subTest(spelling=spelling), chdir(self.output.parent), \
                 patch.object(self.product, 'acquire', side_effect=acquiring), \
                 patch.object(self.product, 'build_candidate', side_effect=candidate), \
                 patch('sys.stdout', new_callable=io.StringIO) as stdout:
                self.assertEqual(main([self.refresh_command, '--output', spelling, '--cache', str(self.root / 'cache'), '--offline']), 0)
                self.assertEqual(json.loads(stdout.getvalue())['status'], 'unchanged')
        # Another comet refresh must not overtake one acquiring from another cache.
        with writer_lock(self.root / ('.data-' + self.product_name + '-refresh-lock')), patch.object(self.product, 'acquire') as acquire:
            with self.assertRaises(DataError): self.comets.refresh_comets(self.root / 'other-cache', self.output)
            acquire.assert_not_called()

    def test_cache_symlink_rejected_without_mutating_target(self):
        target = self.root / 'retained'
        target.mkdir()
        cache = self.root / 'cache-link'
        cache.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(DataError, 'cache cannot be a symlink'):
            self.comets.refresh_comets(cache, self.output, offline=True)
        self.assertEqual(list(target.iterdir()), [])

    def test_pointer_last_and_interrupted_orphan_recovery(self):
        stage = self.root / 'candidate'; shutil.copytree(self.output, stage)
        orphan = self.output / 'objects' / ('0' * 64 + '.json'); orphan.write_text('{}')
        changes = sync_candidate(stage, self.output, inventory=self.comets.managed_files)
        self.assertEqual(changes['deleted'], ['objects/' + orphan.name])
        self.assertEqual(changes['changed'], [])
        self.comets.verify_comets(self.output)


class PreflightCases(CometFixture):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.site = self.root / 'data'
        self.output = self.site / self.product_name
        shutil.copytree(ROOT / 'tests/fixtures/browser-v1/ties', self.site)
        shutil.copytree(ROOT / 'data' / self.product_name, self.output)

    def snapshot(self):
        result = {}
        for path in self.root.rglob('*'):
            if path.is_symlink(): value = ('symlink', os.readlink(path))
            elif path.is_dir(): value = ('directory',)
            else: value = ('file', hashlib.sha256(path.read_bytes()).hexdigest())
            result[path.relative_to(self.root).as_posix()] = value
        return result

    def reject_cli(self, cache, expected):
        before = self.snapshot()
        with patch.object(self.product, 'acquire') as acquire, patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(main([self.refresh_command, '--cache', str(cache), '--output', str(self.output), '--offline']), 1)
            self.assertIn(expected, stderr.getvalue())
            acquire.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        verify_site(self.site)

    def test_cache_overlap_with_any_site_namespace_rejects_before_writes(self):
        alias = self.root / 'site-alias'
        alias.symlink_to(self.site, target_is_directory=True)
        for cache in (self.site, self.site / 'chunks', self.site / 'new-cache', self.output,
                      self.root, alias / 'chunks', self.root / 'DATA/chunks'):
            with self.subTest(cache=cache):
                self.reject_cli(cache, 'must not overlap')
        # Invocation-relative paths pass through the same public CLI preflight.
        with chdir(self.root):
            self.reject_cli(Path('data/chunks'), 'must not overlap')

    def test_cross_filesystem_site_and_nested_mounts_reject_before_acquisition(self):
        original_stat = Path.stat
        object_file = next((self.output / 'objects').iterdir())
        for mount in (self.site, self.output, self.output / 'objects', object_file):
            def mounted_stat(path, *args, **kwargs):
                info = original_stat(path, *args, **kwargs)
                if path == mount or mount in path.parents:
                    values = list(info)
                    values[2] += 1  # st_dev; emulate a distinct mounted volume.
                    return os.stat_result(values)
                return info
            with self.subTest(mount=mount), patch.object(Path, 'stat', mounted_stat):
                self.reject_cli(self.root / 'cache', 'same filesystem')

    def test_new_same_filesystem_output_can_reach_acquisition_without_site_pollution(self):
        new_output = self.site / 'new-comets'
        before = self.snapshot()
        with patch.object(self.product, 'acquire', side_effect=ValueError('acquisition sentinel')) as acquire, \
             patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(main([self.refresh_command, '--cache', str(self.root / 'cache'),
                                   '--output', str(new_output), '--offline']), 1)
            self.assertIn('acquisition sentinel', stderr.getvalue())
            acquire.assert_called_once()
        # Only the dedicated external refresh lock may be created, not public files.
        after = self.snapshot()
        self.assertEqual({k: v for k, v in after.items() if not k.startswith(('.data-new-comets-refresh-lock', '.data-horizons-lock'))}, before)
        self.assertFalse(new_output.exists())
        self.assertFalse((self.root / 'cache').exists())
        verify_site(self.site)


class PublicationCases(CometFixture):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / 'data' / self.product_name
        shutil.copytree(ROOT / 'data' / self.product_name, self.output)

    def test_orphan_comets_do_not_block_asteroid_update_but_block_publication(self):
        orphan = self.output / 'objects' / ('0' * 64 + '.json')
        orphan.write_text('{}')
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        # Exercise the real orchestration CLI, exporter and post-install verifier.
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/update_browser.py'),
                                 '--from-bundle', str(ROOT / 'tests/fixtures/consumer-v1/ties'),
                                 '--output', str(self.output.parent)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(json.loads(result.stdout)['records'], 0)
        verify_browser(self.output.parent)
        self.assertEqual(before, {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
        with self.assertRaises(DataError): verify_site(self.output.parent)
        for name in ('orrery_data', 'scripts'):
            shutil.copytree(ROOT / name, self.root / name, ignore=shutil.ignore_patterns('__pycache__'))
        staged = self.root / 'pages'
        failed = subprocess.run([sys.executable, str(self.root / 'scripts/prepare_browser_site.py'),
                                 '--output', str(staged)], cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Comet inventory differs', failed.stderr)
        self.assertFalse(staged.exists())
        # Site verification visits the comet product once, independently of asteroid files.
        orphan.unlink()
        from orrery_data.comet_rollout import verify_current_comets
        with patch('orrery_data.site.verify_current_comets', wraps=verify_current_comets) as verify:
            verify_site(self.output.parent)
            verify.assert_called_once_with(self.output)
        shutil.rmtree(self.output)
        self.output.symlink_to(ROOT / 'data' / self.product_name, target_is_directory=True)
        with self.assertRaises(DataError): verify_browser(self.output.parent)

    def test_asteroid_export_preserves_comets_and_site_reconciles_both(self):
        data = self.output.parent
        fixture = ROOT / 'tests/fixtures/browser-v1/ties'
        for path in fixture.iterdir():
            if path.is_dir(): shutil.copytree(path, data / path.name)
            else: shutil.copyfile(path, data / path.name)
        comet_before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        # An interrupted comet candidate remains outside the public site.
        abandoned = self.root / '.comets-interrupted'; abandoned.mkdir(); (abandoned / 'partial').write_text('x')
        stage = self.root / 'asteroids'; shutil.copytree(fixture, stage)
        sync_candidate(stage, data)
        self.assertEqual(comet_before, {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
        verify_browser(data)
        spec = importlib.util.spec_from_file_location('prepare_site', ROOT / 'scripts/prepare_browser_site.py')
        site = importlib.util.module_from_spec(spec); spec.loader.exec_module(site)
        base_url = 'https://example.test/sub/'
        product_url = base_url + self.product_name + '/latest.json'
        remote = {base_url + 'latest.json': (data / 'latest.json').read_bytes(),
                  product_url: (self.output / 'latest.json').read_bytes()}
        def open_remote(url, timeout):
            if url not in remote:
                raise HTTPError(url, 404, 'missing', {}, None)
            return io.BytesIO(remote[url])
        def invoke(name):
            out = self.root / name
            with patch.object(site, 'ROOT', self.root), patch.object(site, 'urlopen', side_effect=open_remote), \
                 patch.object(site.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(verify_browser(data)), '')), \
                 patch.object(sys, 'argv', ['prepare', '--output', str(out), '--latest-url', 'https://example.test/sub/latest.json']), \
                 patch('sys.stdout', new_callable=io.StringIO) as stdout:
                self.assertEqual(site.main(), 0)
                return json.loads(stdout.getvalue()), out
        self.assertEqual(invoke('same')[0]['status'], 'unchanged')
        remote[product_url] = b'{}'
        result, out = invoke('comet-stale'); self.assertEqual(result['status'], 'prepared')
        self.assertEqual({p.relative_to(data): p.read_bytes() for p in data.rglob('*') if p.is_file()},
                         {p.relative_to(out): p.read_bytes() for p in out.rglob('*') if p.is_file() and p.name != '.nojekyll'})
        self.assertEqual((out / self.product_name / 'latest.json').read_bytes(), (self.output / 'latest.json').read_bytes())
        del remote[product_url]
        self.assertEqual(invoke('missing-comet')[0]['status'], 'prepared')
        remote['https://example.test/sub/latest.json'] = b'{}'
        self.assertEqual(invoke('both-stale')[0]['status'], 'prepared')
        # Deliberate product removal is also a complete-site change.
        remote['https://example.test/sub/latest.json'] = (data / 'latest.json').read_bytes()
        remote[product_url] = (self.output / 'latest.json').read_bytes()
        shutil.rmtree(self.output)
        result, out = invoke('removed-local')
        self.assertEqual(result['status'], 'prepared')
        self.assertFalse((out / self.product_name).exists())

    def test_reconciliation_uses_exact_discovery_url_and_fails_closed(self):
        spec = importlib.util.spec_from_file_location('prepare_site', ROOT / 'scripts/prepare_browser_site.py')
        site = importlib.util.module_from_spec(spec); spec.loader.exec_module(site)
        data = self.output.parent
        (data / 'latest.json').write_text('{}')
        discovery = 'https://example.test/sub/custom-descriptor.json?pin=test'
        remote = {discovery: b'{}'}
        for name in ('comets',):
            descriptor = data / name / 'latest.json'
            if descriptor.exists():
                remote['https://example.test/sub/' + name + '/latest.json'] = descriptor.read_bytes()
        requests = []
        def opening(url, timeout):
            requests.append(url)
            if url not in remote:
                raise HTTPError(url, 404, 'missing', {}, None)
            return io.BytesIO(remote[url])
        with patch.object(site, 'urlopen', side_effect=opening):
            self.assertTrue(site.published_site_matches(data, discovery))
            self.assertEqual(requests[0], discovery)
            self.assertFalse(site.published_site_matches(data, discovery + '-missing'))
        with patch.object(site, 'urlopen', side_effect=HTTPError(discovery, 500, 'unavailable', {}, None)):
            self.assertFalse(site.published_site_matches(data, discovery))
        with patch.object(site, 'urlopen', return_value=io.BytesIO(b'{broken')):
            self.assertFalse(site.published_site_matches(data, discovery))
