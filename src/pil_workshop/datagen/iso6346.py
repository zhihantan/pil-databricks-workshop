"""ISO 6346 container-number utilities (owner code + serial + check digit).

An ISO 6346 container number is 4 letters (3 owner + 1 category, usually 'U'
for freight containers) + 6 serial digits + 1 check digit. The check digit is a
weighted modulo-11 checksum over the first 10 characters, which makes the
synthetic ``container_no`` values realistic and validatable — a nice detail for
the workshop's data-quality talk track.
"""

from __future__ import annotations

# Letter → value map per ISO 6346 (10..38, skipping every multiple of 11).
_LETTER_VALUES: dict[str, int] = {}
_v = 10
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    while _v % 11 == 0:
        _v += 1
    _LETTER_VALUES[_c] = _v
    _v += 1


def check_digit(owner_category: str, serial: str) -> int:
    """Return the ISO 6346 check digit for a 4-letter code + 6-digit serial."""
    code = f"{owner_category}{serial}"
    if len(code) != 10:
        raise ValueError(f"Expected 4 letters + 6 digits, got {code!r}")
    total = 0
    for i, ch in enumerate(code):
        value = _LETTER_VALUES[ch] if ch.isalpha() else int(ch)
        total += value * (2**i)
    cd = total % 11
    return 0 if cd == 10 else cd


def container_number(owner: str, category: str, serial_int: int) -> str:
    """Build a full ISO 6346 container number from parts.

    ``owner`` is the 3-letter owner code, ``category`` a single letter
    (``U``/``J``/``Z``), ``serial_int`` an integer zero-padded to 6 digits.
    """
    serial = f"{serial_int % 1_000_000:06d}"
    oc = f"{owner}{category}"
    return f"{oc}{serial}{check_digit(oc, serial)}"


def is_valid(container_no: str) -> bool:
    """Validate a container number's format and check digit."""
    s = container_no.strip().upper()
    if len(s) != 11 or not s[:4].isalpha() or not s[4:].isdigit():
        return False
    return check_digit(s[:4], s[4:10]) == int(s[10])
