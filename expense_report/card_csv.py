"""Statement CSV import — the audit-grade transaction source.

Supports both provider layouts and auto-detects which one a file is:
  * Amex: one signed Amount column (charges positive, AMEX_AMOUNT_SIGN flips
    if your export inverts), Reference ids, Extended Details.
  * Capital One: separate Debit (charge) / Credit (payment or refund) columns.

Header mapping is table-driven (CSV_COLUMN_MAP) because banks have shipped
several export layouts over the years; a missing REQUIRED column fails loudly
with a found-vs-expected diff instead of importing garbage. Reimports are
idempotent (stable txn ids), and any provisional alert-sourced transaction the
CSV confirms is marked superseded so nothing double-counts.
"""

from __future__ import annotations

import csv
import glob
import hashlib
import json
import os
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import expense_config as config
from .logging_setup import get_logger
from .models import Transaction
from .normalize import parse_cents, parse_iso_date, days_between, normalize_merchant
from .categorize import apply_categories
from . import store

log = get_logger()

_FOREIGN_RE = re.compile(
    r"FOREIGN\s+SPEND\s+AMOUNT:?\s*([\d,]+\.\d{2})\s+([A-Z]{3})", re.IGNORECASE)


def _canon(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower())


def build_column_map(headers: List[str],
                     overrides: Optional[List[str]] = None) -> Dict[str, str]:
    """Map canonical field name -> actual CSV header. Overrides are
    'field=Header' strings from --column-map. Raises SystemExit listing what
    was found vs expected when a required field can't be located."""
    alias_map = {field: [_canon(a) for a in aliases]
                 for field, aliases in config.CSV_COLUMN_MAP.items()}
    for item in overrides or []:
        field, _, header = item.partition("=")
        field = field.strip()
        if field not in alias_map:
            valid = ", ".join(sorted(alias_map))
            raise SystemExit(f"unknown --column-map field {field!r} — valid: {valid}")
        alias_map[field] = [_canon(header)]

    by_canon = {_canon(h): h for h in headers}
    mapping: Dict[str, str] = {}
    for field, aliases in alias_map.items():
        for alias in aliases:
            if alias in by_canon:
                mapping[field] = by_canon[alias]
                break

    missing = [f for f in config.CSV_REQUIRED if f not in mapping]
    if "amount" not in mapping and "debit" not in mapping and "credit" not in mapping:
        missing.append("amount (or debit/credit)")
    if missing:
        raise SystemExit(
            "CSV import failed — required column(s) not found: "
            f"{', '.join(missing)}\n"
            f"  headers in file: {headers}\n"
            "  fix: pass --column-map, e.g. --column-map date=\"Trans Date\", "
            "or add the header to CSV_COLUMN_MAP in expense_config.py")
    return mapping


def detect_provider(mapping: Dict[str, str]) -> str:
    """Debit/Credit split columns -> capitalone; single Amount -> amex."""
    if "amount" not in mapping and ("debit" in mapping or "credit" in mapping):
        return "capitalone"
    return "amex"


def _is_payment(description: str) -> bool:
    return any(re.search(p, description, re.IGNORECASE)
               for p in config.PAYMENT_DESCRIPTION_PATTERNS)


def _row_amount(get, provider: str) -> Optional[int]:
    """Signed cents (charge +, credit -) under either layout."""
    if provider == "capitalone":
        debit = parse_cents(get("debit"))
        credit = parse_cents(get("credit"))
        if debit is not None and debit != 0:
            return abs(debit)
        if credit is not None and credit != 0:
            return -abs(credit)
        return None
    amount = parse_cents(get("amount"))
    return None if amount is None else amount * config.AMEX_AMOUNT_SIGN


def parse_csv_rows(rows: List[dict], mapping: Dict[str, str],
                   provider: str) -> List[Transaction]:
    """DictReader rows -> Transactions. Skips card payments and rows with no
    parseable date/amount (logged, never silently)."""
    txns: List[Transaction] = []
    for i, row in enumerate(rows, start=2):   # 1-based + header line
        get = lambda f: (row.get(mapping[f]) or "").strip() if f in mapping else ""
        description = get("description")
        if _is_payment(description):
            continue
        date = parse_iso_date(get("date"))
        amount = _row_amount(get, provider)
        if not date or amount is None:
            log.warning("csv line %d skipped (unparseable date/amount): %r",
                        i, description[:40])
            continue

        extended = get("extended")
        foreign_amount = foreign_currency = ""
        m = _FOREIGN_RE.search(extended)
        if m:
            foreign_amount, foreign_currency = m.group(1).replace(",", ""), m.group(2)

        account = get("account")
        txns.append(Transaction(
            provider=provider,
            source="csv",
            date=date,
            post_date=parse_iso_date(get("post_date")),
            description=description,
            merchant=normalize_merchant(get("appears_as") or description),
            appears_as=get("appears_as"),
            amount_cents=amount,
            foreign_amount=foreign_amount,
            foreign_currency=foreign_currency,
            card_member=get("card_member"),
            account_suffix=account[-5:] if account else "",
            reference=get("reference").strip("'"),
            address=get("address"),
            city=get("city"),
            state=get("state"),
            country=get("country"),
            category_amex=get("category"),
        ))
    return txns


def merge_transactions(ledger: store.Ledger, new: List[Transaction]) -> int:
    """Add unseen transactions by txn_id; return how many were new."""
    seen = ledger.txn_by_id()
    added = 0
    for txn in new:
        if txn.txn_id in seen:
            continue
        ledger.transactions.append(txn)
        seen[txn.txn_id] = txn
        added += 1
    return added


def supersede_alerts(ledger: store.Ledger) -> int:
    """Mark alert-sourced transactions superseded when a same-provider CSV
    transaction confirms them (exact cents, date within the window, merchant
    similar). The alert txn's receipt link transfers to the CSV txn so
    matches survive."""
    csv_txns = [t for t in ledger.transactions if t.source == "csv"]
    count = 0
    for alert in ledger.transactions:
        if alert.source != "alert" or alert.superseded_by:
            continue
        for txn in csv_txns:
            if txn.provider != alert.provider:
                continue
            if txn.amount_cents != alert.amount_cents:
                continue
            gap = days_between(alert.date, txn.date)
            if gap is None or abs(gap) > config.ALERT_SUPERSEDE_WINDOW_DAYS:
                continue
            ratio = SequenceMatcher(None, alert.merchant, txn.merchant).ratio()
            if ratio < config.ALERT_SUPERSEDE_MERCHANT_RATIO:
                continue
            alert.superseded_by = txn.txn_id
            if alert.matched_receipt_id and not txn.matched_receipt_id:
                txn.matched_receipt_id = alert.matched_receipt_id
                txn.match_score = alert.match_score
                txn.match_method = alert.match_method
            count += 1
            break
    return count


# ---------------------------------------------------------------------------
# statements/ auto-discovery: files are keyed by content hash in an index so
# the same download never imports twice, and renamed files stay recognized.
# ---------------------------------------------------------------------------

def _statements_index_path() -> str:
    return config.data_path("statements_index.json")


def _file_sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_statements_index() -> dict:
    path = _statements_index_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_statements_index(index: dict) -> None:
    os.makedirs(config.EXPENSE_DATA_DIR, exist_ok=True)
    tmp = _statements_index_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
    os.replace(tmp, _statements_index_path())


def discover_statements(extension: str) -> List[str]:
    """New (never-imported) files of the given extension under statements/."""
    index = _load_statements_index()
    fresh = []
    for path in sorted(glob.glob(os.path.join(config.STATEMENTS_DIR, f"*.{extension}"))):
        if _file_sha1(path) not in index:
            fresh.append(path)
    return fresh


def mark_statement_imported(path: str) -> None:
    index = _load_statements_index()
    index[_file_sha1(path)] = {"path": path}
    _save_statements_index(index)


def run_import_csv(paths: List[str], column_overrides: List[str]) -> None:
    explicit = bool(paths)
    if not paths:
        paths = discover_statements("csv")
        if not paths:
            log.info("import-csv: no new CSV files in %s/", config.STATEMENTS_DIR)
            return

    ledger = store.load_ledger()
    total_new = 0
    for path in paths:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames or []
            mapping = build_column_map(headers, column_overrides)
            provider = detect_provider(mapping)
            txns = parse_csv_rows(list(reader), mapping, provider)
        added = merge_transactions(ledger, txns)
        total_new += added
        log.info("imported %s (%s): %d rows, %d new transactions",
                 path, provider, len(txns), added)
        if not explicit:
            mark_statement_imported(path)

    superseded = supersede_alerts(ledger)
    if superseded:
        log.info("%d alert transaction(s) confirmed and superseded by CSV rows",
                 superseded)
    apply_categories(ledger.transactions)
    store.save_ledger(ledger)
    log.info("ledger: %d transactions (%d new)", len(ledger.transactions), total_new)
