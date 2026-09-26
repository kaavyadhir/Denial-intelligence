"""Turn the PUF's text columns into numbers, or into an honest NULL.

Every numeric field in the source workbook is typed as text. Real values in
these files include "1,234", " 567 ", "", "N/A", "NA", "n/a", "--", "*" and
"Not Applicable". Three of those mean genuinely different things:

* a number            -> the count
* blank / N/A / "*"   -> the issuer did not report it, which is NOT zero
* a literal "0"       -> zero claims denied, which IS a fact

Collapsing missing into 0 would silently understate denial rates for every
issuer that failed to report - the exact population you most want to look at.
So unparseable values become None and the SQL layer excludes them rather than
averaging them in.
"""
from __future__ import annotations

import re

MISSING = {"", "na", "n/a", "n.a.", "none", "null", "not applicable", "--", "-", "*", "."}

_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def to_int(value: object) -> int | None:
    """Parse a PUF numeric cell. Returns None for anything not a clean number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # openpyxl hands back floats for integer cells; reject real fractions.
        return int(value) if value.is_integer() else None

    text = str(value).strip().replace(",", "").replace(" ", "")
    if text.lower() in MISSING:
        return None
    # Percentages and currency occasionally appear in otherwise-count columns.
    text = text.rstrip("%").replace("$", "").strip()
    if not _NUMERIC.match(text):
        return None
    number = float(text)
    return int(number) if number.is_integer() else None


def to_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in MISSING else text
