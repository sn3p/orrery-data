"""MPC fixed-width parsing, derived from Orrery's MIT-licensed importer.

Copyright (c) 2016 Matthijs Kuiper. See LICENSE and NOTICE.txt.
"""

from datetime import date
import math
import re


FIELDS = ("disc", "epoch", "a", "e", "i", "W", "w", "M", "n")
BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class DataError(ValueError):
    """An input or stored artifact failed validation."""


def julian_day(day):
    return (day - date(2000, 1, 1)).days + 2451544.5


def packed_epoch(value):
    if not re.fullmatch(r"[I-L][0-9]{2}[1-9ABC][1-9A-V]", value):
        raise DataError(f"Invalid packed epoch: {value!r}")
    return julian_day(date(int(value[0], 36) * 100 + int(value[1:3]),
                           int(value[3], 36), int(value[4], 36)))


def identity(packed):
    """Use MPC authority keys, never row position, display name or elements."""
    number = None
    if re.fullmatch(r"[0-9A-Za-z][0-9]{4}", packed):
        number = BASE62.index(packed[0]) * 10000 + int(packed[1:])
    elif re.fullmatch(r"~[0-9A-Za-z]{4}", packed):
        number = 620000
        number += sum(BASE62.index(c) * 62 ** (3 - i) for i, c in enumerate(packed[1:]))
    if number is not None:
        if number < 1:
            raise DataError("MPC number must be positive")
        return f"mpc:number:{number}", number
    # Classic provisional, survey, and extended provisional designations.
    if not (re.fullmatch(r"[I-L][0-9]{2}[A-HJ-Y][0-9A-Za-z][0-9][A-HJ-Z]", packed)
            or re.fullmatch(r"(?:PLS|T[123]S)[0-9]{4}", packed)
            or re.fullmatch(r"_[0-9A-Za-z][A-HJ-Y][0-9A-Za-z]{4}", packed)):
        raise DataError(f"Unsupported packed identity: {packed!r}")
    return f"mpc:designation:{packed}", None


def discoveries(path):
    result = {}
    with path.open(encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                match = re.fullmatch(r"\s*\(([0-9]+)\)", line[:8])
                if not match or len(line.rstrip("\r\n")) < 51:
                    raise DataError("Invalid numbered discovery row")
                number = int(match[1])
                if number < 1 or number in result:
                    raise DataError(f"Invalid or duplicate MPC number {number}")
                stamp = line[41:51]
                if not re.fullmatch(r"[0-9]{4} [0-9]{2} [0-9]{2}", stamp):
                    raise DataError(f"Invalid discovery date {stamp!r}")
                result[number] = julian_day(date(int(stamp[:4]), int(stamp[5:7]), int(stamp[8:])))
            except ValueError as exc:
                raise DataError(f"NumberedMPs line {line_no}: {exc}") from exc
    if not result:
        raise DataError("Empty discovery source")
    return result


def master_rows(path, discovery, counts, header):
    """Stream complete MPCORB; invalid rows fail, supported exclusions count."""
    seen = set()
    matched = set()
    in_data = False
    counts.update(orbital_records=0, master_records=0, known_discovery=0,
                  missing_discovery=0, numbered_orbits=0, unnumbered_orbits=0,
                  unsupported_orbits=0, discovery_records=len(discovery))
    slices = {"M": (26, 35), "w": (37, 46), "W": (48, 57), "i": (59, 68),
              "e": (70, 79), "n": (80, 91), "a": (92, 103)}
    with path.open(encoding="utf-8") as source:
        for line_no, line in enumerate(source, 1):
            if not in_data:
                header.append(line)
                if line_no == 1 and not line.startswith("MINOR PLANET CENTER ORBIT DATABASE (MPCORB)"):
                    raise DataError("Missing MPCORB header")
                if line.startswith("----------"):
                    in_data = True
                if line_no > 200:
                    raise DataError("Missing MPCORB data separator")
                continue
            if not line.strip():
                continue
            try:
                if len(line.rstrip("\r\n")) < 160:
                    raise DataError("Truncated orbital row (requires columns 1–160)")
                packed = line[:7].strip()
                key, number = identity(packed)
                if key in seen:
                    raise DataError(f"Duplicate identity {key}")
                seen.add(key)
                epoch = packed_epoch(line[20:25])
                elements = {key: float(line[start:end]) for key, (start, end) in slices.items()}
                if not all(math.isfinite(value) for value in elements.values()):
                    raise DataError("Nonfinite orbital elements")
                if elements["e"] < 0 or not 0 <= elements["i"] <= 180:
                    raise DataError("Invalid eccentricity or inclination")
                # MPC's printed precision can round a near-360 angle to 360.00000.
                if not all(0 <= elements[key] <= 360 for key in ("M", "w", "W")):
                    raise DataError("Invalid orbital angle")
                if elements["e"] < 1 and (elements["a"] <= 0 or elements["n"] <= 0):
                    raise DataError("Elliptic orbit requires positive a and n")
                readable = line[166:194].strip() or None
                display_number = re.match(r"\(([0-9]+)\)", readable or "")
                if display_number and int(display_number[1]) != number:
                    raise DataError("Packed and readable MPC numbers disagree")
                counts["orbital_records"] += 1
                counts["numbered_orbits" if number else "unnumbered_orbits"] += 1
                if elements["e"] >= 1:
                    counts["unsupported_orbits"] += 1
                    continue
                disc = discovery.get(number)
                if disc is not None:
                    matched.add(number)
                counts["master_records"] += 1
                counts["known_discovery" if disc is not None else "missing_discovery"] += 1
                yield {"id": key, "number": number, "packed_designation": packed,
                       "readable_designation": readable, "disc": disc, "epoch": epoch,
                       **{key: elements[key] for key in FIELDS[2:]},
                       "orbit_reference": line[107:116].strip() or None,
                       "orbit_computer": line[150:160].strip() or None}
            except ValueError as exc:
                raise DataError(f"MPCORB line {line_no}: {exc}") from exc
    if not in_data or not counts["master_records"]:
        raise DataError("Empty or unsupported orbital source")
    counts["unmatched_discovery_records"] = len(discovery) - len(matched)
