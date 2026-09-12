"""Validate caller-supplied provenance before touching a snapshot store or source."""

from datetime import datetime
from email.utils import parsedate_to_datetime
import re

from .formats import DataError
from .storage import URLS


WEEKDAY = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
TIME = r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
HTTP_DATE = (
    rf"(?:{WEEKDAY}, [0-9]{{2}} {MONTH} [0-9]{{4}} {TIME} GMT"
    rf"|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [0-9]{{2}}-{MONTH}-[0-9]{{2}} {TIME} GMT"
    rf"|{WEEKDAY} {MONTH} (?:[0-9]{{2}}| [0-9]) {TIME} [0-9]{{4}})"
)
RETRIEVED_AT = r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-2][0-9]:[0-5][0-9])"


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def timestamp(value):
    if not matches(RETRIEVED_AT, value):
        return False
    try:
        datetime.fromisoformat(value.upper())
        return True
    except ValueError:
        return False


def http_date(value):
    if not matches(HTTP_DATE, value):
        return False
    try:
        parsedate_to_datetime(value)
        return True
    except ValueError:
        return False


def validate_source_metadata(metadata):
    if not isinstance(metadata, dict) or not all(k in URLS and isinstance(v, dict) for k, v in metadata.items()):
        raise DataError("Source metadata must contain mpcorb/numbered objects")
    rules = {
        "sha256": (lambda v: matches(r"[0-9a-f]{64}", v), "64 lowercase hexadecimal characters"),
        "decoded_sha256": (lambda v: matches(r"[0-9a-f]{64}", v), "64 lowercase hexadecimal characters"),
        "retrieved_at": (lambda v: v is None or timestamp(v), "null or a valid timestamp with seconds and a UTC offset"),
        "last_modified": (lambda v: v is None or http_date(v), "null or a valid HTTP date"),
        "etag": (lambda v: v is None or matches(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"', v), "null or a quoted HTTP entity tag"),
        "content_length": (lambda v: v is None or (type(v) is int and v >= 0) or matches(r"[0-9]+", v),
                           "null, a nonnegative integer, or an ASCII decimal digit string"),
    }
    for name, fields in metadata.items():
        unknown = fields.keys() - rules.keys()
        if unknown:
            raise DataError(f"{name}: unknown source metadata fields: {', '.join(sorted(unknown))}")
        for field, value in fields.items():
            valid, description = rules[field]
            if not valid(value):
                raise DataError(f"{name}.{field}: expected {description}")
