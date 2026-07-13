"""Pure normalization helpers: money -> integer cents, dates -> ISO strings,
raw statement descriptions -> canonical merchant names. No I/O, no state —
everything here is directly unit-testable."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import expense_config as config

_AMOUNT_RE = re.compile(r"-?\$?\s*-?[\d,]+(?:\.\d{1,2})?")

# Statement descriptions often end in a "city ST" or phone-number tail:
# "BLUE BOTTLE COFFEE OAKLAND CA" / "DELTA AIR 0062339194077 ATLANTA".
# Only one city word is stripped (plus common two-word city prefixes), because
# a greedier match eats the merchant name itself.
_CITY_STATE_TAIL = re.compile(
    r"\s+(?:(?:SAN|LOS|LAS|NEW|FORT|SAINT|ST\.?|EL|DE)\s+)?[A-Z][A-Za-z.]*\s+[A-Z]{2}$")
_PHONE_TAIL = re.compile(r"\s+\(?\d{3}\)?[- .]?\d{3}[- .]?\d{4}$")
_STORE_NUMBER = re.compile(r"\s+#?\d{3,}$")
_MULTISPACE = re.compile(r"\s{2,}")

_DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m/%d",
    "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y",
]


def parse_cents(raw: object) -> Optional[int]:
    """'$1,234.56' / '1234.56' / '(12.34)' / -12.3 -> integer cents.
    Parenthesized amounts are negative (accounting convention).
    Returns None when nothing parseable is present."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            return int((Decimal(str(raw)) * 100).to_integral_value())
        except InvalidOperation:
            return None
    text = str(raw).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    num = m.group(0).replace("$", "").replace(",", "").replace(" ", "")
    try:
        value = Decimal(num)
    except InvalidOperation:
        return None
    if negative and value > 0:
        value = -value
    return int((value * 100).to_integral_value())


def parse_iso_date(raw: object, default_year: Optional[int] = None) -> str:
    """Best-effort date parse -> 'YYYY-MM-DD' ('' when unparseable).
    `default_year` completes formats like MM/DD from PDF statement lines."""
    if raw is None:
        return ""
    if isinstance(raw, (datetime, date)):
        return raw.strftime("%Y-%m-%d")
    text = str(raw).strip()
    if not text:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%m/%d":
            if default_year is None:
                continue
            dt = dt.replace(year=default_year)
        # strptime parses 2-digit years into 1969-2068; good enough here.
        return dt.strftime("%Y-%m-%d")
    return ""


def days_between(iso_a: str, iso_b: str) -> Optional[int]:
    """Signed day count b - a; None when either side is missing/unparseable."""
    try:
        a = datetime.strptime(iso_a, "%Y-%m-%d")
        b = datetime.strptime(iso_b, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return (b - a).days


def normalize_merchant(raw: str) -> str:
    """Raw statement description / sender guess -> canonical merchant name.

    Order matters: strip processor prefixes (SQ *, TST*, PAYPAL *...), then
    apply the alias table, then trim store numbers and city/state tails."""
    if not raw:
        return ""
    text = raw.strip()

    for prefix in config.MERCHANT_STRIP_PREFIXES:
        if text.upper().startswith(prefix.upper()):
            text = text[len(prefix):].strip()
            break

    for pattern, canonical in config.MERCHANT_ALIASES.items():
        if re.match(pattern, text, re.IGNORECASE):
            return canonical

    text = _PHONE_TAIL.sub("", text)
    text = _STORE_NUMBER.sub("", text)
    text = _CITY_STATE_TAIL.sub("", text)
    text = _MULTISPACE.sub(" ", text).strip(" -*")
    return text.upper()


def merchant_tokens(name: str) -> set:
    """Significant tokens for overlap scoring (drops 1-2 char noise)."""
    return {t for t in re.split(r"[^A-Z0-9]+", name.upper()) if len(t) > 2}
