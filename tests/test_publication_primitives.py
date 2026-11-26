"""Native installation semantics and the shared bounded remote read boundary."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from orrery_data.formats import DataError
from orrery_data import publication, paired
from orrery_data.storage import rename_new, exchange_directories
from scripts import prepare_browser_site


class PublicationPrimitives(unittest.TestCase):
    def test_exclusive_install_and_exchange_preserve_both_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); first=root/'first'; second=root/'second'
            first.mkdir(); (first/'value').write_text('first')
            second.mkdir(); (second/'value').write_text('second')
            with self.assertRaises(FileExistsError):rename_new(first,second)
            self.assertEqual((first/'value').read_text(),'first')
            self.assertEqual((second/'value').read_text(),'second')
            exchange_directories(first,second)
            self.assertEqual((first/'value').read_text(),'second')
            self.assertEqual((second/'value').read_text(),'first')
            with self.assertRaises(OSError):exchange_directories(first,root/'absent')
            rename_new(first,root/'third')
            self.assertFalse(first.exists())
            self.assertEqual((root/'third/value').read_text(),'second')

    def test_bounded_read_and_callers_keep_distinct_failure_policies(self):
        url='https://example.test/paired/latest.json'
        self.assertIs(prepare_browser_site.remote_descriptor,publication.remote_descriptor)
        raw=b'{}'+b' '*(publication.MAX_DESCRIPTOR_BYTES-2)
        with patch.object(publication,'urlopen',return_value=io.BytesIO(raw)):
            self.assertEqual(publication.remote_descriptor(url),{})
        for raw in (raw+b' ',b'{bad',b'{"x":1,"x":2}'):
            with self.subTest(raw=raw[:20]):
                with patch.object(publication,'urlopen',return_value=io.BytesIO(raw)):
                    with self.assertRaises(ValueError):publication.remote_descriptor(url)
                with patch.object(publication,'urlopen',return_value=io.BytesIO(raw)):
                    self.assertIsNone(paired.published_descriptor('https://example.test/'))
        for status,optional in ((404,False),(500,True),(404,True)):
            with patch.object(publication,'urlopen',side_effect=HTTPError(url,status,'test',{},None)):
                if status==404 and optional:
                    self.assertIsNone(publication.remote_descriptor(url,optional=optional))
                else:
                    with self.assertRaises(HTTPError):publication.remote_descriptor(url,optional=optional)
