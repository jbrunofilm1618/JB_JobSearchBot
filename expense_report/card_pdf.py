"""PDF statement cross-verification — the official/final check.

The PDF is VERIFICATION ONLY: it never adds or edits CSV-sourced
transactions. parse_statement_text() is a pure function over extracted text
(testable without pdfplumber); pdfplumber itself is an optional extra
(`pip install .[pdf]`) used only to get that text out of the file.

Per-transaction verify: exact cents + date within 1 day + merchant similarity
>= 0.6 -> verified_in_pdf=True. Charges on only one side raise
R7 STATEMENT_MISMATCH flags; a parsed "Total New Charges" is compared against
the CSV sum for the same period. An unparseable PDF degrades to a logged
warning — it can never corrupt the ledger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

import expense_config as config
from .logging_setup import get_logger
from .models import Flag, cents_to_dollars
from .normalize import parse_cents, parse_iso_date, days_between, normalize_merchant
from . import store

log = get_logger()

# "07/03/26*  DELTA AIR LINES ATLANTA           1,041.60"
# "07/03      UBER *TRIP HELP.UBER.COM          18.42"
_TXN_LINE_RE = re.compile(
    r"^(\d{2}/\d{2}(?:/\d{2,4})?)\*?\s+(.+?)\s+(-?\$?[\d,]+\.\d{2})\s*$")

_PERIOD_RES = [
    re.compile(r"Closing Date\s+(\d{2}/\d{2}/\d{2,4})", re.IGNORECASE),
    re.compile(r"Statement Period[:\s]+(\d{2}/\d{2}/\d{2,4})\s*(?:-|to|through)\s*"
               r"(\d{2}/\d{2}/\d{2,4})", re.IGNORECASE),
]
_TOTAL_RES = [
    re.compile(r"Total New Charges\s+\$?\s*([\d,]+\.\d{2})", re.IGNORECASE),
    re.compile(r"New Charges\s+\$?\s*([\d,]+\.\d{2})", re.IGNORECASE),
    re.compile(r"Purchases\s+\$?\s*([\d,]+\.\d{2})", re.IGNORECASE),
]


@dataclass
class PdfStatement:
    lines: List[dict]                      # {date, description, amount_cents}
    period_end: str = ""                   # ISO, drives 2-digit-year completion
    total_new_charges: Optional[int] = None


def parse_statement_text(text: str) -> PdfStatement:
    """Pure text -> PdfStatement. Tolerant: keeps every line that looks like a
    transaction row, ignores the rest of the PDF's noise."""
    period_end = ""
    for rx in _PERIOD_RES:
        m = rx.search(text)
        if m:
            period_end = parse_iso_date(m.group(m.lastindex))
            if period_end:
                break

    total = None
    for rx in _TOTAL_RES:
        m = rx.search(text)
        if m:
            total = parse_cents(m.group(1))
            break

    year = int(period_end[:4]) if period_end else None
    lines = []
    for raw in text.splitlines():
        m = _TXN_LINE_RE.match(raw.strip())
        if not m:
            continue
        date = parse_iso_date(m.group(1), default_year=year)
        amount = parse_cents(m.group(3))
        if not date or amount is None:
            continue
        description = re.sub(r"\s{2,}", " ", m.group(2)).strip()
        # Statement pages repeat payments/credits sections; skip payment rows.
        if re.search(r"\bPAYMENT\b.*\bTHANK YOU\b", description, re.IGNORECASE):
            continue
        lines.append({"date": date, "description": description,
                      "amount_cents": amount})
    return PdfStatement(lines=lines, period_end=period_end,
                        total_new_charges=total)


def extract_pdf_text(path: str) -> str:
    try:
        import pdfplumber
    except ImportError:
        raise SystemExit(
            "import-pdf needs pdfplumber — install with: pip install .[pdf]")
    chunks = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def cross_verify(ledger: store.Ledger, statement: PdfStatement,
                 source_name: str) -> Tuple[int, List[Flag]]:
    """Mark CSV transactions found in the PDF; flag both kinds of mismatch.
    Returns (verified count, new flags)."""
    csv_txns = [t for t in ledger.transactions if t.source == "csv"]
    unclaimed = list(statement.lines)
    verified = 0
    flags: List[Flag] = []

    for txn in csv_txns:
        hit = None
        for line in unclaimed:
            if line["amount_cents"] != txn.amount_cents:
                continue
            gap = days_between(txn.date, line["date"])
            if gap is None or abs(gap) > 1:
                continue
            merchant = normalize_merchant(line["description"])
            ratio = SequenceMatcher(None, merchant, txn.merchant).ratio()
            if ratio >= 0.6 or merchant in txn.merchant or txn.merchant in merchant:
                hit = line
                break
        if hit:
            unclaimed.remove(hit)
            txn.verified_in_pdf = True
            verified += 1
        elif statement.period_end and txn.verified_in_pdf is None:
            # Only judge txns that fall in this statement's window (~31 days
            # before the closing date); others belong to a different PDF.
            gap = days_between(txn.date, statement.period_end)
            if gap is not None and 0 <= gap <= 31:
                txn.verified_in_pdf = False
                flags.append(Flag(
                    rule="STATEMENT_MISMATCH", kind="reconciliation",
                    severity="high", txn_ids=[txn.txn_id],
                    detail=(f"{txn.merchant}: ${cents_to_dollars(txn.amount_cents)} "
                            f"on {txn.date} is in the CSV but NOT on the "
                            f"official statement {source_name}")))

    for line in unclaimed:
        if line["amount_cents"] <= 0:
            continue
        flags.append(Flag(
            rule="STATEMENT_MISMATCH", kind="reconciliation", severity="high",
            receipt_ids=[],   # anchored by detail; no ledger object exists
            txn_ids=[f"pdf:{line['date']}:{line['amount_cents']}"],
            detail=(f"{normalize_merchant(line['description'])}: "
                    f"${cents_to_dollars(line['amount_cents'])} on "
                    f"{line['date']} is on the official statement "
                    f"{source_name} but NOT in the imported CSV — "
                    f"download a fresh CSV export covering this period")))

    if statement.total_new_charges is not None and statement.period_end:
        window = [t for t in csv_txns if t.amount_cents > 0
                  and (g := days_between(t.date, statement.period_end)) is not None
                  and 0 <= g <= 31]
        csv_sum = sum(t.amount_cents for t in window)
        if window and csv_sum != statement.total_new_charges:
            flags.append(Flag(
                rule="STATEMENT_MISMATCH", kind="reconciliation", severity="warn",
                txn_ids=[f"pdf-total:{statement.period_end}"],
                detail=(f"{source_name}: statement total new charges "
                        f"${cents_to_dollars(statement.total_new_charges)} vs "
                        f"${cents_to_dollars(csv_sum)} summed from the CSV for "
                        f"the same period — some transactions may be missing "
                        f"on one side")))
    return verified, flags


def run_import_pdf(paths: List[str]) -> None:
    from .card_csv import discover_statements, mark_statement_imported
    explicit = bool(paths)
    if not paths:
        paths = discover_statements("pdf")
        if not paths:
            log.info("import-pdf: no new PDF files in %s/", config.STATEMENTS_DIR)
            return

    ledger = store.load_ledger()
    existing = {f.flag_id for f in ledger.flags}
    for path in paths:
        try:
            text = extract_pdf_text(path)
            statement = parse_statement_text(text)
        except SystemExit:
            raise
        except Exception as exc:
            log.warning("could not parse %s (%s) — skipping, ledger untouched",
                        path, exc)
            continue
        if not statement.lines:
            log.warning("%s: no transaction lines recognized — the layout may "
                        "have changed; PDF verification skipped", path)
            continue
        verified, new_flags = cross_verify(ledger, statement, path)
        for f in new_flags:
            if f.flag_id not in existing:
                ledger.flags.append(f)
                existing.add(f.flag_id)
        log.info("%s: %d transaction(s) verified, %d mismatch flag(s)",
                 path, verified, len(new_flags))
        if not explicit:
            mark_statement_imported(path)
    store.save_ledger(ledger)
