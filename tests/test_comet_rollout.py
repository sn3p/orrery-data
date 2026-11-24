"""Real release bytes through CLI, complete-site staging and rollback boundaries."""
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from orrery_data.comet_rollout import HISTORY_PIN, prepare_rollout, verify_history
from orrery_data.formats import DataError
from orrery_data.site import verify_site
from orrery_data.storage import site_lock_path, writer_lock

ROOT = Path(__file__).resolve().parents[1]


def inventory(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def history_fixture(directory):
    shutil.copytree(ROOT / 'tests/fixtures/comets-schema2', directory)
    for p in directory.glob('index-*.json'): p.unlink()
    raw = (ROOT / 'tests/fixtures/comet-history-index.json').read_bytes()
    index = json.loads(raw)
    name = f'index-{HISTORY_PIN}.json'
    (directory / name).write_bytes(raw)
    (directory / 'latest.json').write_text(json.dumps(dict(comet_contract_version=3,
        index=dict(url=name, sha256=HISTORY_PIN, bytes=len(raw)))))
    for entry in index['objects']:
        for segment in entry['segments']:
            if segment['key'] == 'historical':
                name = segment['data']['url']
                (directory / name).write_bytes(gzip.decompress(
                    (ROOT / 'tests/fixtures/comet-history-payloads' / (Path(name).name + '.gz')).read_bytes()))


class CometRollout(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.site = self.root / 'data'
        shutil.copytree(ROOT / 'tests/fixtures/browser-v1/ties', self.site)
        shutil.copytree(ROOT / 'tests/fixtures/comets-schema2', self.site / 'comets')
        self.candidate = self.root / 'candidate'
        history_fixture(self.candidate)
        self.pin = json.loads((self.site / 'comets/latest.json').read_bytes())['index']['sha256']
        self.output = self.root / 'release'

    def prepare(self):
        return prepare_rollout(self.site, self.candidate, self.output, self.pin)

    def test_real_cli_prepares_complete_site_and_exact_rollback_without_source_changes(self):
        before, candidate = inventory(self.site), inventory(self.candidate)
        run = subprocess.run([sys.executable, '-m', 'orrery_data', 'prepare-comet-rollout',
            '--site', str(self.site), '--candidate', str(self.candidate), '--output', str(self.output),
            '--expected-current-pin', self.pin], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['pin']['sha256'], HISTORY_PIN)
        self.assertEqual(inventory(self.site), before)
        self.assertEqual(inventory(self.candidate), candidate)
        self.assertEqual(inventory(self.output / 'rollback'), before)
        expected = {k:v for k,v in before.items() if not k.startswith('comets/')}
        expected.update({'comets/'+k:v for k,v in candidate.items()})
        self.assertEqual(inventory(self.output / 'site'), expected)
        self.assertEqual(verify_site(self.output / 'site')['comets']['pin']['sha256'], HISTORY_PIN)
        verify_site(self.output / 'rollback')
        # A normal asteroid publisher cannot prune/change the independent product.
        from orrery_data.browser import sync_candidate
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / 'asteroids'
            shutil.copytree(ROOT / 'tests/fixtures/browser-v1/ties', stage)
            sync_candidate(stage, self.output / 'site')
        self.assertEqual(inventory(self.output / 'site/comets'), candidate)
        # Ordinary refresh is not a route around science release approval.
        from orrery_data.comets import refresh_comets
        with self.assertRaisesRegex(DataError, 'immutable'):
            refresh_comets(self.root / 'cache', self.output / 'site/comets', offline=True)
        self.assertFalse((self.root / 'cache').exists())

    def test_publication_stages_schema3_and_reconciles_schema2_remote(self):
        from scripts import prepare_browser_site as publisher
        import io
        self.prepare()
        project = self.root / 'checkout'; project.mkdir()
        for name in ('orrery_data', 'scripts'):
            shutil.copytree(ROOT / name, project / name, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(self.output / 'site', project / 'data')
        staged = self.root / 'pages'
        with patch.object(publisher, 'ROOT', project), patch.object(publisher, 'urlopen', side_effect=lambda url, timeout:
                io.BytesIO((self.site / url.removeprefix('https://example.test/')).read_bytes())), \
             patch.object(sys, 'argv', ['prepare', '--output', str(staged), '--latest-url', 'https://example.test/latest.json']), \
             patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.assertEqual(publisher.main(), 0)
            self.assertEqual(json.loads(stdout.getvalue())['status'], 'prepared')
        self.assertEqual(inventory(staged), {**inventory(self.output / 'site'), '.nojekyll': b''})

    def test_stale_pin_existing_output_overlap_and_lock_leave_source_unchanged(self):
        before = inventory(self.site)
        with self.assertRaisesRegex(DataError, 'pin changed'):
            prepare_rollout(self.site, self.candidate, self.output, '0'*64)
        for out in (self.site, self.site / 'nested', self.candidate / 'nested', self.root,
                    self.root / '.context/comet-candidates/new/rollout'):
            with self.assertRaises(DataError):
                prepare_rollout(self.site, self.candidate, out, self.pin)
        with writer_lock(site_lock_path(self.site)):
            with self.assertRaisesRegex(DataError, 'Another writer'): self.prepare()
        self.assertEqual(inventory(self.site), before)
        self.assertFalse(self.output.exists())
        self.prepare()
        with self.assertRaises(DataError): self.prepare()

    def test_invalid_releases_fail_closed(self):
        original = inventory(self.candidate)
        for kind in ('unknown-pin', 'corrupt', 'missing', 'extra', 'symlink', 'nested'):
            with self.subTest(kind=kind):
                shutil.rmtree(self.candidate); self.candidate.mkdir()
                for name, raw in original.items():
                    p=self.candidate/name;p.parent.mkdir(exist_ok=True);p.write_bytes(raw)
                payload = next((self.candidate / 'objects').iterdir())
                if kind == 'unknown-pin':
                    latest=json.loads((self.candidate/'latest.json').read_bytes())
                    latest['index']['sha256']='0'*64
                    (self.candidate/'latest.json').write_text(json.dumps(latest))
                elif kind == 'corrupt': payload.write_bytes(b'{}')
                elif kind == 'missing': payload.unlink()
                elif kind == 'extra': (self.candidate/('index-'+'0'*64+'.json')).write_text('{}')
                elif kind == 'symlink':
                    target=self.root/'payload';target.write_bytes(payload.read_bytes());payload.unlink();payload.symlink_to(target)
                else: (self.candidate/'temporary-endpoint').mkdir()
                with self.assertRaises((DataError, OSError)): self.prepare()
                self.assertFalse(self.output.exists())

    def test_verification_cli_rejects_nonobject_and_unknown_version_as_json(self):
        for descriptor in ([], None, True, {}, {'comet_contract_version': 4}):
            with self.subTest(descriptor=descriptor):
                (self.candidate/'latest.json').write_text(json.dumps(descriptor))
                run = subprocess.run([sys.executable, '-m', 'orrery_data', 'verify-comets',
                    '--directory', str(self.candidate)], capture_output=True, text=True)
                self.assertEqual(run.returncode, 1)
                self.assertIn('error', json.loads(run.stderr))

    def test_redirected_candidate_namespace_is_not_a_rollout_destination(self):
        namespace = self.root / 'project/.context/comet-candidates'
        namespace.parent.mkdir(parents=True)
        external = self.root / 'external'; external.mkdir()
        namespace.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(DataError, 'namespace'):
            prepare_rollout(self.site, self.candidate, namespace/'new/rollout', self.pin)
        self.assertEqual(list(external.iterdir()), [])

    def test_new_immutable_output_ancestor_is_rechecked_before_writing(self):
        parent = self.root / 'new-candidate'
        self.output = parent / 'rollout'
        verify = verify_history
        def install_ancestor(directory):
            result = verify(directory)
            shutil.copytree(self.candidate, parent)
            return result
        with patch('orrery_data.comet_rollout.verify_history', side_effect=install_ancestor):
            with self.assertRaisesRegex(DataError, 'immutable'): self.prepare()
        self.assertEqual(inventory(parent), inventory(self.candidate))
        self.assertFalse(self.output.exists())

    def test_pages_source_option_stages_prepared_tree_without_changing_default(self):
        self.prepare()
        run = subprocess.run([sys.executable, str(ROOT/'scripts/prepare_browser_site.py'),
            '--source', str(self.output/'site'), '--output', str(self.root/'pages-source')],
            capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['comets']['pin']['sha256'], HISTORY_PIN)
        self.assertEqual(inventory(self.root/'pages-source'),
                         {**inventory(self.output/'site'), '.nojekyll': b''})

    def test_copy_failure_never_exposes_partial_release_and_retry_works(self):
        copy = shutil.copytree
        def failing(src, dst, *args, **kwargs):
            if Path(dst).name == 'comets' and Path(src) == self.candidate:
                raise OSError('interrupted copy')
            return copy(src, dst, *args, **kwargs)
        with patch('orrery_data.comet_rollout.shutil.copytree', side_effect=failing):
            with self.assertRaisesRegex(OSError, 'interrupted'): self.prepare()
        self.assertFalse(self.output.exists())
        self.prepare()
