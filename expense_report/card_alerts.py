"""Parse card transaction ALERT emails (Amex, Capital One) into provisional
Transactions — the automatic day-to-day feed. A later CSV import supersedes
any alert the statement confirms (card_csv.supersede_alerts), so these never
double-count and the CSV stays the audit-grade record.

Alert wording varies by provider and over time, so several tolerant patterns
are tried; an alert none of them fit is logged and skipped (the CSV will pick
the transaction up at month end anyway).
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Optional

import expense_config as config
from .logging_setup import get_logger
from .models import Transaction
from .normalize import parse_cents, parse_iso_date, normalize_merchant
from .email_parse import body_texts

log = get_logger()

# "A charge of $54.99 at AMAZON.COM was approved on July 1, 2026"
# "Your card was used for $18.42 at UBER TRIP on 07/01/2026"
# "$54.99 transaction at AMAZON.COM"  /  "A purchase of $54.99 was made at ..."
_ALERT_PATTERNS = [
    re.compile(r"(?:charge|purchase|transaction)\s+of\s+\$([\d,]+\.\d{2})\s+"
               r"(?:was\s+(?:made|approved)\s+)?at\s+(.{2,60}?)"
               r"(?:\s+was\s+(?:made|approved))?(?:\s+on\s+([A-Za-z0-9 ,/]+?))?[.\n]",
               re.IGNORECASE | re.DOTALL),
    re.compile(r"card\s+(?:was\s+)?(?:just\s+)?used\s+for\s+\$([\d,]+\.\d{2})\s+"
               r"at\s+(.{2,60}?)(?:\s+on\s+([A-Za-z0-9 ,/]+?))?[.\n]",
               re.IGNORECASE | re.DOTALL),
    re.compile(r"\$([\d,]+\.\d{2})\s+(?:charge|purchase|transaction)\s+"
               r"(?:at|from)\s+(.{2,60}?)[.\n]", re.IGNORECASE | re.DOTALL),
]

_CARD_SUFFIX_RE = re.compile(r"(?:card\s+)?ending\s+(?:in\s+)?[* ]*(\d{4,5})",
                             re.IGNORECASE)


def provider_for_domain(domain: str) -> str:
    for name, provider in config.CARD_PROVIDERS.items():
        for d in provider["alert_sender_domains"]:
            if domain == d or domain.endswith("." + d):
                return name
    return ""


def parse_alert(msg: EmailMessage) -> Optional[Transaction]:
    """Alert email -> provisional Transaction, or None if unparseable."""
    _, addr = parseaddr(str(msg.get("From", "")))
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    provider = provider_for_domain(domain)
    if not provider:
        return None

    match, text = None, ""
    for cand in body_texts(msg):
        for rx in _ALERT_PATTERNS:
            match = rx.search(cand)
            if match:
                text = cand
                break
        if match:
            break
    if not match:
        log.info("alert email from %s didn't match any pattern (subject %r)",
                 domain, str(msg.get("Subject", ""))[:60])
        return None

    amount = parse_cents(match.group(1))
    merchant_raw = re.sub(r"\s+", " ", match.group(2)).strip(" .*\t")
    date_raw = match.group(3).strip() if match.lastindex >= 3 and match.group(3) else ""

    date_iso = parse_iso_date(date_raw)
    if not date_iso:                     # fall back to the email's own date
        try:
            dt = parsedate_to_datetime(str(msg.get("Date", "")))
            date_iso = dt.strftime("%Y-%m-%d") if dt else ""
        except (TypeError, ValueError):
            date_iso = ""
    if amount is None or not date_iso or not merchant_raw:
        return None

    suffix = _CARD_SUFFIX_RE.search(text)
    return Transaction(
        provider=provider,
        source="alert",
        date=date_iso,
        description=merchant_raw,
        merchant=normalize_merchant(merchant_raw),
        amount_cents=amount,
        account_suffix=suffix.group(1) if suffix else "",
        # message-id keeps re-scans idempotent even for same-day same-amount
        # repeat purchases at one merchant
        reference=str(msg.get("Message-ID", "")).strip(),
    )
