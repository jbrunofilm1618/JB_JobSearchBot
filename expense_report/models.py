"""Core data model: transactions, receipts, line items, and flags.

Conventions (shared by every module):
  * money is INTEGER CENTS everywhere (Decimal only at parse boundaries);
    charges are positive, credits/refunds negative
  * dates are ISO "YYYY-MM-DD" strings
  * ids are stable content hashes so re-imports and re-scans are idempotent
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict, fields
from typing import List, Optional


# Reviewable transactions.csv column order (the ledger JSON keeps every field).
TRANSACTION_COLUMNS = [
    "txn_id",
    "provider",          # amex | capitalone
    "source",            # csv | alert
    "date",
    "merchant",          # normalized
    "description",       # raw statement description
    "amount",            # dollars string for review; ledger stores cents
    "currency",
    "category",
    "month",
    "card_member",
    "verified_in_pdf",
    "matched_receipt_id",
    "match_method",
    "superseded_by",
]


def _short_hash(text: str, n: int = 16) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def cents_to_dollars(cents: Optional[int]) -> str:
    if cents is None:
        return ""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


@dataclass
class Transaction:
    """One card transaction, from a CSV statement export (audit-grade) or a
    provider alert email (provisional until a CSV import supersedes it)."""

    txn_id: str = ""                 # "amex:<sha1>" — filled by make_id() if empty
    provider: str = "amex"           # key into config.CARD_PROVIDERS
    source: str = "csv"              # csv | alert
    date: str = ""                   # transaction date, ISO
    post_date: str = ""
    description: str = ""            # raw Description column / alert text
    merchant: str = ""               # normalized (normalize.normalize_merchant)
    appears_as: str = ""
    amount_cents: int = 0            # charge +, credit -
    currency: str = "USD"
    foreign_amount: str = ""         # e.g. "123.45"
    foreign_currency: str = ""       # e.g. "EUR"
    card_member: str = ""
    account_suffix: str = ""         # last digits only, never the full number
    reference: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    category_amex: str = ""          # Amex's own Category column
    category: str = ""               # ours (categorize.py)
    month: str = ""                  # "YYYY-MM", derived from date
    statement_period: str = ""
    verified_in_pdf: Optional[bool] = None
    matched_receipt_id: str = ""
    match_score: Optional[float] = None
    match_method: str = ""           # exact | fuzzy | split
    superseded_by: str = ""          # alert txn replaced by this CSV txn_id

    def make_id(self) -> str:
        # Reference is the most stable dedup key when present; otherwise the
        # (date, amount, description) triple. Prefixed by source namespace so
        # alert and CSV rows never collide silently.
        if self.reference:
            basis = f"ref|{self.reference}"
        else:
            basis = f"{self.date}|{self.amount_cents}|{self.description}"
        ns = self.provider if self.source == "csv" else f"{self.provider}-alert"
        return f"{ns}:{_short_hash(basis)}"

    def __post_init__(self) -> None:
        if not self.txn_id:
            self.txn_id = self.make_id()
        if not self.month and len(self.date) >= 7:
            self.month = self.date[:7]

    @property
    def active(self) -> bool:
        """False once a CSV import has superseded this alert-sourced txn."""
        return not self.superseded_by

    def to_dict(self) -> dict:
        return asdict(self)

    def report_row(self) -> dict:
        d = self.to_dict()
        d["amount"] = cents_to_dollars(self.amount_cents)
        return {col: d.get(col, "") for col in TRANSACTION_COLUMNS}

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class LineItem:
    """One itemized line on a receipt."""

    description: str = ""
    quantity: Optional[float] = None
    unit_price_cents: Optional[int] = None
    amount_cents: Optional[int] = None
    category: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LineItem":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Receipt:
    """One receipt/order email pulled from an IMAP scan."""

    receipt_id: str = ""             # "email:<sha1(message_id)>"
    message_id: str = ""
    account: str = ""                # EMAIL_ACCOUNTS label (icloud / gmail)
    imap_uid: str = ""
    folder: str = ""
    email_date: str = ""             # ISO date from the Date header
    from_addr: str = ""
    from_domain: str = ""
    subject: str = ""
    merchant: str = ""               # normalized
    merchant_guess: str = ""         # raw guess before normalization
    order_id: str = ""
    total_cents: Optional[int] = None
    currency: str = "USD"
    tax_cents: Optional[int] = None
    tip_cents: Optional[int] = None
    shipping_cents: Optional[int] = None
    line_items: List[dict] = field(default_factory=list)   # LineItem dicts
    extraction_method: str = "none"  # rules | llm | none
    extraction_note: str = ""
    matched_txn_ids: List[str] = field(default_factory=list)
    body_cache_path: str = ""
    body_sha1: str = ""              # hash of the body text (dup-email detection)

    def make_id(self) -> str:
        basis = self.message_id or f"{self.account}|{self.folder}|{self.imap_uid}"
        return f"email:{_short_hash(basis)}"

    def __post_init__(self) -> None:
        if not self.receipt_id:
            self.receipt_id = self.make_id()

    def items(self) -> List[LineItem]:
        return [LineItem.from_dict(d) for d in self.line_items]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Receipt":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Flag:
    """One redundancy / reconciliation / fraud finding.

    flag_id is derived from the rule plus the sorted ids it points at, so the
    same finding gets the same id on every rerun and a user's
    status="dismissed" survives flag regeneration."""

    flag_id: str = ""
    rule: str = ""                   # e.g. DUPLICATE_CHARGE
    kind: str = ""                   # redundancy | reconciliation | fraud
    severity: str = "info"           # info | warn | high
    txn_ids: List[str] = field(default_factory=list)
    receipt_ids: List[str] = field(default_factory=list)
    detail: str = ""
    llm_reviewed: bool = False
    llm_agrees: Optional[bool] = None
    llm_reason: str = ""
    status: str = "open"             # open | dismissed

    def make_id(self) -> str:
        basis = "|".join(sorted(self.txn_ids) + sorted(self.receipt_ids))
        return f"{self.rule}:{_short_hash(basis, 12)}"

    def __post_init__(self) -> None:
        if not self.flag_id:
            self.flag_id = self.make_id()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Flag":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
