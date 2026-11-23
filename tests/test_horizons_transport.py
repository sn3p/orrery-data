"""Offline transport and atomic persistence regressions; no fixture servers."""

import http.client
import hashlib
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import call, patch
from urllib.error import HTTPError

from orrery_data import horizons, storage
from orrery_data.formats import DataError


class Response(io.BytesIO):
    def __init__(self, body=b'{}', *, headers=None, error=None, status=200):
        super().__init__(body)
        self.headers = headers or {}
        self.status = status
        self.error = error

    def read(self, size=-1):
        if self.error:
            raise self.error
        return super().read(size)


class HorizonsTransport(unittest.TestCase):
    def test_retained_raw_is_bounded_regular_and_hash_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            raw = b'retained response'
            digest = hashlib.sha256(raw).hexdigest()
            path = cache / (digest + '.raw')
            path.write_bytes(raw)
            self.assertEqual(horizons.read_raw(cache, digest), raw)
            with patch.object(horizons, 'MAX_RESPONSE', len(raw) - 1):
                with self.assertRaises(DataError): horizons.read_raw(cache, digest)
            path.write_bytes(b'corrupt')
            with self.assertRaises(DataError): horizons.read_raw(cache, digest)
            path.unlink()
            retained = cache / 'retained'; retained.write_bytes(raw)
            path.symlink_to(retained)
            with self.assertRaises(DataError): horizons.read_raw(cache, digest)
            path.unlink(); path.mkdir()
            with self.assertRaises(DataError): horizons.read_raw(cache, digest)
            for invalid in ('../retained', '', None):
                with self.assertRaises(DataError): horizons.read_raw(cache, invalid)

    def test_response_read_failures_retry_and_close_before_success(self):
        errors = (ConnectionResetError('reset'), ConnectionAbortedError('aborted'),
                  http.client.IncompleteRead(b'{', 1), http.client.RemoteDisconnected('closed'),
                  ssl.SSLEOFError('TLS stream closed'), TimeoutError('read timeout'))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                first, second = Response(error=error), Response()
                with patch.object(horizons, 'urlopen', side_effect=[first, second]) as opening, \
                     patch.object(horizons.time, 'sleep') as sleep:
                    self.assertEqual(horizons.download(horizons.query('1P')), b'{}')
                self.assertEqual(opening.call_count, 2)
                sleep.assert_called_once_with(2)
                self.assertTrue(first.closed)
                self.assertTrue(second.closed)

    def test_short_content_length_response_is_retried(self):
        short = Response(b'{', headers={'Content-Length': '2'})
        good = Response(b'{}', headers={'Content-Length': '2'})
        with patch.object(horizons, 'urlopen', side_effect=[short, good]) as opening, \
             patch.object(horizons.time, 'sleep') as sleep:
            self.assertEqual(horizons.download(horizons.query('1P')), b'{}')
        self.assertEqual(opening.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertTrue(short.closed)

    def test_repeated_truncation_stops_after_three_attempts(self):
        responses = [Response(b'{', headers={'Content-Length': '2'}) for _ in range(3)]
        with patch.object(horizons, 'urlopen', side_effect=responses) as opening, \
             patch.object(horizons.time, 'sleep') as sleep:
            with self.assertRaises(http.client.IncompleteRead):
                horizons.download(horizons.query('1P'))
        self.assertEqual(opening.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(2), call(4)])
        self.assertTrue(all(response.closed for response in responses))

    def test_permanent_response_errors_are_not_retried(self):
        responses = [Response(headers={'Content-Encoding': 'gzip'}), Response(status=201),
                     Response(headers={'Content-Length': 'not-a-length'}),
                     Response(headers={'Content-Length': '-1'}),
                     Response(headers={'Content-Length': str(horizons.MAX_RESPONSE + 1)}),
                     Response(b'{}', headers={'Content-Length': '1'}),
                     Response(b'x' * (horizons.MAX_RESPONSE + 1))]
        for response in responses:
            with self.subTest(headers=response.headers, status=response.status), \
                 patch.object(horizons, 'urlopen', return_value=response) as opening, \
                 patch.object(horizons.time, 'sleep') as sleep:
                with self.assertRaises(DataError):
                    horizons.download(horizons.query('1P'))
                opening.assert_called_once()
                sleep.assert_not_called()
                self.assertTrue(response.closed)

    def test_permanent_http_error_is_not_retried(self):
        with patch.object(horizons, 'urlopen', side_effect=HTTPError('https://example.test', 400, 'bad query', {}, None)) as opening, \
             patch.object(horizons.time, 'sleep') as sleep:
            with self.assertRaises(HTTPError): horizons.download(horizons.query('1P'))
            opening.assert_called_once()
            sleep.assert_not_called()

    def test_exhausted_transfer_does_not_replace_cached_record(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            record = cache / '1P-vectors.json'
            previous = b'{"previous":"accepted source"}\n'
            record.write_bytes(previous)
            responses = [Response(error=ConnectionResetError('reset')) for _ in range(3)]
            with patch.object(horizons, 'urlopen', side_effect=responses), \
                 patch.object(horizons.time, 'sleep'):
                with self.assertRaises(ConnectionResetError): horizons.acquire(cache, refresh=True, targets=horizons.TARGETS)
            self.assertEqual(record.read_bytes(), previous)
            self.assertEqual(list(cache.glob('*.raw')), [])
            self.assertEqual(list(cache.glob('.pointer-*')), [])


class AtomicBytes(unittest.TestCase):
    def test_raw_bytes_and_json_use_the_same_atomic_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / 'response.raw'
            storage.atomic_bytes(raw, b'\x00\xff\nraw')
            self.assertEqual(raw.read_bytes(), b'\x00\xff\nraw')
            value = {'text': 'com\u00e8te', 'number': 4}
            pointer = root / 'pointer.json'
            with patch.object(storage, 'atomic_bytes', wraps=storage.atomic_bytes) as write:
                storage.atomic_json(pointer, value)
                write.assert_called_once_with(pointer, (storage.encode(value) + '\n').encode('utf-8'))
            self.assertEqual(json.loads(pointer.read_bytes()), value)
            self.assertEqual(list(root.glob('.pointer-*')), [])

    def test_sync_and_replace_failures_preserve_previous_bytes_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pointer = root / 'accepted.raw'
            pointer.write_bytes(b'previous')
            for failure in ('fsync', 'replace'):
                with self.subTest(failure=failure), patch.object(storage.os, failure, side_effect=OSError('failed')):
                    with self.assertRaises(OSError): storage.atomic_bytes(pointer, b'candidate')
                self.assertEqual(pointer.read_bytes(), b'previous')
                self.assertEqual(list(root.glob('.pointer-*')), [])


if __name__ == '__main__': unittest.main()
