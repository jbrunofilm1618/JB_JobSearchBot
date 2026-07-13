"""Redundancy / reconciliation / fraud rules. Each rule is a pure function
(transactions, receipts, context) -> [Flag] so it can be tested in isolation;
run_flags composes them.

Flags are regenerated on every run, but flag ids are stable content hashes
(rule + the ids involved), so a flag the user dismissed stays dismissed when
the same finding reappears.

Rules:
  R1 DUPLICATE_CHARGE           same merchant + identical cents within window
  R2 DUPLICATE_RECEIPT          same order twice in the inbox
  R3 SUBSCRIPTION_DOUBLE_BILL   recurring merchant billed twice in one month
  R4 RECEIPT_NO_CHARGE          receipt never hit the statement
  R5 CHARGE_NO_RECEIPT          statement charge with no receipt (bounded to
                                the actually-scanned email date range)
  R6 AMBIGUOUS_MATCH            reconciliation was a coin flip
  F1 UNRECOGNIZED_MERCHANT      first-ever merchant, no receipt, over floor
  F2 AMOUNT_OUTLIER             way above this merchant's history (median+MAD)
  F3 FOREIGN_ANOMALY            lone foreign transaction
  F4 ROUND_NUMBER               suspicious exact-hundreds charge, no receipt
  F5 MICRO_CHARGE_PROBE         card-testing pattern: tiny charge, new merchant
  F6 RAPID_FIRE                 several identical charges on one day
(R6 replaces the STATEMENT_MISMATCH slot here; statement mismatches are
raised by card_pdf.py during PDF cross-verification as R7 STATEMENT_MISMATCH.)
"""

from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import expense_config as config
from .logging_setup import get_logger
from .models import Transaction, Receipt, Flag, cents_to_dollars
from .normalize import days_between
from . import store

log = get_logger()


def _dollars(cents: int) -> str:
    return "$" + cents_to_dollars(abs(cents))


# --------------------------------------------------------------------------- #
# Redundancy / reconciliation
# --------------------------------------------------------------------------- #

def rule_duplicate_charge(txns: List[Transaction]) -> List[Flag]:
    """R1: same merchant, identical amount, within DUP_CHARGE_WINDOW_DAYS."""
    flags = []
    by_key = defaultdict(list)
    for t in txns:
        if t.amount_cents > 0 and t.merchant not in config.DUP_EXEMPT_MERCHANTS:
            by_key[(t.merchant, t.amount_cents)].append(t)
    for (merchant, amount), group in by_key.items():
        group.sort(key=lambda t: t.date)
        for a, b in zip(group, group[1:]):
            gap = days_between(a.date, b.date)
            if gap is not None and gap <= config.DUP_CHARGE_WINDOW_DAYS:
                flags.append(Flag(
                    rule="DUPLICATE_CHARGE", kind="redundancy",
                    severity="high" if gap == 0 else "warn",
                    txn_ids=[a.txn_id, b.txn_id],
                    detail=(f"{merchant}: {_dollars(amount)} charged twice "
                            f"{gap} day(s) apart ({a.date} and {b.date})")))
    return flags


def rule_duplicate_receipt(receipts: List[Receipt]) -> List[Flag]:
    """R2: the same order/receipt email appearing more than once (forwarded
    copies, order+shipping pairs quoting the same total, true dupes)."""
    flags = []
    seen_order: Dict[Tuple[str, str], Receipt] = {}
    seen_body: Dict[str, Receipt] = {}
    seen_total: Dict[Tuple[str, Optional[int]], Receipt] = {}
    for r in sorted(receipts, key=lambda r: r.email_date):
        if r.order_id:
            key = (r.merchant, r.order_id)
            if key in seen_order and seen_order[key].receipt_id != r.receipt_id:
                flags.append(Flag(
                    rule="DUPLICATE_RECEIPT", kind="redundancy", severity="info",
                    receipt_ids=[seen_order[key].receipt_id, r.receipt_id],
                    detail=(f"{r.merchant}: two receipt emails for order "
                            f"{r.order_id}")))
                continue
            seen_order[key] = r
        if r.body_sha1:
            if r.body_sha1 in seen_body \
                    and seen_body[r.body_sha1].receipt_id != r.receipt_id:
                flags.append(Flag(
                    rule="DUPLICATE_RECEIPT", kind="redundancy", severity="info",
                    receipt_ids=[seen_body[r.body_sha1].receipt_id, r.receipt_id],
                    detail=f"{r.merchant}: identical receipt email received twice"))
                continue
            seen_body[r.body_sha1] = r
        if r.total_cents is not None and not r.order_id:
            key2 = (r.merchant, r.total_cents)
            if key2 in seen_total:
                prev = seen_total[key2]
                gap = days_between(prev.email_date, r.email_date)
                if gap is not None and abs(gap) <= config.DUP_RECEIPT_WINDOW_DAYS \
                        and prev.receipt_id != r.receipt_id:
                    flags.append(Flag(
                        rule="DUPLICATE_RECEIPT", kind="redundancy",
                        severity="info",
                        receipt_ids=[prev.receipt_id, r.receipt_id],
                        detail=(f"{r.merchant}: two receipts for "
                                f"{_dollars(r.total_cents)} within a day")))
                    continue
            seen_total[key2] = r
    return flags


def _recurring_merchants(txns: List[Transaction]) -> Dict[str, List[Transaction]]:
    """Merchants that look like subscriptions: >= SUBSCRIPTION_MIN_CHARGES,
    monthly-ish median cadence, near-constant amount."""
    lo, hi = config.SUBSCRIPTION_CADENCE_DAYS
    out = {}
    by_merchant = defaultdict(list)
    for t in txns:
        if t.amount_cents > 0:
            by_merchant[t.merchant].append(t)
    for merchant, group in by_merchant.items():
        if len(group) < config.SUBSCRIPTION_MIN_CHARGES:
            continue
        group.sort(key=lambda t: t.date)
        gaps = [days_between(a.date, b.date) for a, b in zip(group, group[1:])]
        gaps = [g for g in gaps if g is not None and g > 0]
        if not gaps or not lo <= statistics.median(gaps) <= hi:
            continue
        amounts = [t.amount_cents for t in group]
        med = statistics.median(amounts)
        if med and max(abs(a - med) for a in amounts) / med \
                <= config.SUBSCRIPTION_AMOUNT_VARIANCE_PCT:
            out[merchant] = group
    return out


def rule_subscription_double_bill(txns: List[Transaction]) -> List[Flag]:
    """R3: a recurring subscription charged twice in one calendar month."""
    flags = []
    for merchant, group in _recurring_merchants(txns).items():
        by_month = defaultdict(list)
        for t in group:
            by_month[t.month].append(t)
        for month, hits in by_month.items():
            if len(hits) >= 2:
                flags.append(Flag(
                    rule="SUBSCRIPTION_DOUBLE_BILL", kind="redundancy",
                    severity="high",
                    txn_ids=[t.txn_id for t in hits],
                    detail=(f"{merchant}: recurring subscription billed "
                            f"{len(hits)}x in {month} "
                            f"({', '.join(t.date for t in hits)})")))
    return flags


def rule_receipt_no_charge(receipts: List[Receipt], txns: List[Transaction],
                           today: str) -> List[Flag]:
    """R4: a receipt with a real total that never hit any statement."""
    flags = []
    for r in receipts:
        if r.matched_txn_ids or r.total_cents is None or r.total_cents <= 0:
            continue
        age = days_between(r.email_date, today)
        if age is None or age <= config.RECEIPT_NO_CHARGE_GRACE_DAYS:
            continue
        flags.append(Flag(
            rule="RECEIPT_NO_CHARGE", kind="reconciliation", severity="warn",
            receipt_ids=[r.receipt_id],
            detail=(f"{r.merchant}: {_dollars(r.total_cents)} receipt on "
                    f"{r.email_date} has no matching card charge — check which "
                    f"card was actually billed")))
    return flags


def rule_charge_no_receipt(txns: List[Transaction],
                           scan_range: Tuple[str, str]) -> List[Flag]:
    """R5: a charge with no receipt email. Only meaningful INSIDE the date
    range emails were actually scanned for — otherwise every charge before the
    first scan would scream."""
    since, until = scan_range
    if not since or not until:
        return []
    flags = []
    for t in txns:
        if t.matched_receipt_id or t.amount_cents <= 0:
            continue
        if not (since <= t.date <= until):
            continue
        if any(m in t.merchant for m in config.NO_RECEIPT_EXPECTED):
            continue
        severity = ("warn" if t.amount_cents >= config.CHARGE_NO_RECEIPT_WARN_CENTS
                    else "info")
        flags.append(Flag(
            rule="CHARGE_NO_RECEIPT", kind="reconciliation", severity=severity,
            txn_ids=[t.txn_id],
            detail=(f"{t.merchant}: {_dollars(t.amount_cents)} on {t.date} "
                    f"has no receipt email")))
    return flags


def rule_ambiguous_match(ambiguous: List[Tuple[str, str, str]]) -> List[Flag]:
    """R6: reconciliation coin flips recorded by match.py."""
    return [Flag(rule="AMBIGUOUS_MATCH", kind="reconciliation", severity="info",
                 receipt_ids=[rid], txn_ids=[winner, other],
                 detail="two transactions score nearly equally for this "
                        "receipt — verify the link")
            for rid, other, winner in ambiguous]


# --------------------------------------------------------------------------- #
# Fraud heuristics
# --------------------------------------------------------------------------- #

def rule_unrecognized_merchant(txns: List[Transaction]) -> List[Flag]:
    """F1: first-ever appearance of a merchant, no receipt, over the floor."""
    flags = []
    seen = set()
    for t in sorted(txns, key=lambda t: t.date):
        first = t.merchant not in seen
        seen.add(t.merchant)
        if (first and not t.matched_receipt_id and t.amount_cents
                >= config.UNRECOGNIZED_MERCHANT_MIN_CENTS):
            flags.append(Flag(
                rule="UNRECOGNIZED_MERCHANT", kind="fraud", severity="warn",
                txn_ids=[t.txn_id],
                detail=(f"{t.merchant}: first charge ever from this merchant "
                        f"({_dollars(t.amount_cents)} on {t.date}) and no "
                        f"receipt email")))
    return flags


def rule_amount_outlier(txns: List[Transaction]) -> List[Flag]:
    """F2: charge far above this merchant's own history — median + k*MAD and
    at least 2x the median, so one-off shops never trip it."""
    flags = []
    by_merchant = defaultdict(list)
    for t in sorted(txns, key=lambda t: t.date):
        history = by_merchant[t.merchant]
        if len(history) >= config.OUTLIER_MIN_HISTORY and t.amount_cents > 0:
            amounts = [h.amount_cents for h in history]
            med = statistics.median(amounts)
            mad = statistics.median([abs(a - med) for a in amounts]) or med * 0.1
            if (t.amount_cents > med + config.OUTLIER_MAD_MULTIPLIER * mad
                    and t.amount_cents > config.OUTLIER_MEDIAN_MULTIPLIER * med):
                flags.append(Flag(
                    rule="AMOUNT_OUTLIER", kind="fraud", severity="warn",
                    txn_ids=[t.txn_id],
                    detail=(f"{t.merchant}: {_dollars(t.amount_cents)} on "
                            f"{t.date} vs a typical {_dollars(int(med))} "
                            f"({len(history)} prior charges)")))
        if t.amount_cents > 0:
            history.append(t)
    return flags


def rule_foreign_anomaly(txns: List[Transaction]) -> List[Flag]:
    """F3: a lone foreign-currency charge with no other foreign activity
    nearby — a normal trip produces clusters, skimmers produce singletons."""
    foreign = [t for t in txns if t.foreign_currency]
    flags = []
    for t in foreign:
        neighbors = [o for o in foreign if o.txn_id != t.txn_id
                     and (d := days_between(t.date, o.date)) is not None
                     and abs(d) <= config.FOREIGN_LONE_WINDOW_DAYS]
        if not neighbors:
            flags.append(Flag(
                rule="FOREIGN_ANOMALY", kind="fraud", severity="warn",
                txn_ids=[t.txn_id],
                detail=(f"{t.merchant}: lone foreign transaction "
                        f"({t.foreign_amount} {t.foreign_currency} / "
                        f"{_dollars(t.amount_cents)}) on {t.date} with no "
                        f"other foreign activity within "
                        f"{config.FOREIGN_LONE_WINDOW_DAYS} days")))
    return flags


def rule_round_number(txns: List[Transaction]) -> List[Flag]:
    """F4: exact multiples of $100 with no receipt — uncommon in real retail,
    common in gift-card drains and manual fraud."""
    flags = []
    for t in txns:
        if (t.amount_cents >= config.ROUND_NUMBER_UNIT_CENTS
                and t.amount_cents % config.ROUND_NUMBER_UNIT_CENTS == 0
                and not t.matched_receipt_id
                and not any(a in t.merchant for a in config.ROUND_NUMBER_ALLOWLIST)):
            flags.append(Flag(
                rule="ROUND_NUMBER", kind="fraud", severity="info",
                txn_ids=[t.txn_id],
                detail=(f"{t.merchant}: suspiciously round "
                        f"{_dollars(t.amount_cents)} on {t.date}, no receipt")))
    return flags


def rule_micro_charge_probe(txns: List[Transaction]) -> List[Flag]:
    """F5: classic card-testing — a tiny charge at a merchant you've never
    used, no receipt. Thieves verify stolen numbers this way before spending."""
    flags = []
    seen = set()
    for t in sorted(txns, key=lambda t: t.date):
        first = t.merchant not in seen
        seen.add(t.merchant)
        if (first and 0 < t.amount_cents <= config.MICRO_CHARGE_MAX_CENTS
                and not t.matched_receipt_id):
            flags.append(Flag(
                rule="MICRO_CHARGE_PROBE", kind="fraud", severity="high",
                txn_ids=[t.txn_id],
                detail=(f"{t.merchant}: {_dollars(t.amount_cents)} micro-charge "
                        f"on {t.date} at a never-seen merchant — classic "
                        f"card-testing pattern")))
    return flags


def rule_rapid_fire(txns: List[Transaction]) -> List[Flag]:
    """F6: several identical charges at one merchant on one day (statement
    CSVs carry no timestamps, so same-day is the tightest window we have)."""
    flags = []
    by_key = defaultdict(list)
    for t in txns:
        if t.amount_cents > 0:
            by_key[(t.merchant, t.date, t.amount_cents)].append(t)
    for (merchant, date, amount), group in by_key.items():
        if len(group) >= config.RAPID_FIRE_MIN_COUNT:
            flags.append(Flag(
                rule="RAPID_FIRE", kind="fraud", severity="warn",
                txn_ids=[t.txn_id for t in group],
                detail=(f"{merchant}: {len(group)} identical "
                        f"{_dollars(amount)} charges on {date}")))
    return flags


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #

def _scan_range(receipts: List[Receipt]) -> Tuple[str, str]:
    """Explicitly-scanned range if recorded, else the span of receipt dates
    actually seen (both shrunk by nothing — R5 stays bounded either way)."""
    path = config.data_path("scan_range.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            rng = json.load(fh)
        if rng.get("since") and rng.get("until"):
            return rng["since"], rng["until"]
    dates = sorted(r.email_date for r in receipts if r.email_date)
    if not dates:
        return "", ""
    return dates[0], dates[-1]


def generate_flags(ledger: store.Ledger,
                   ambiguous: Optional[List[Tuple[str, str, str]]] = None,
                   today: Optional[str] = None) -> List[Flag]:
    txns = ledger.active_transactions()
    receipts = ledger.receipts
    today = today or datetime.now().strftime("%Y-%m-%d")

    flags: List[Flag] = []
    flags += rule_duplicate_charge(txns)
    flags += rule_duplicate_receipt(receipts)
    flags += rule_subscription_double_bill(txns)
    flags += rule_receipt_no_charge(receipts, txns, today)
    flags += rule_charge_no_receipt(txns, _scan_range(receipts))
    flags += rule_ambiguous_match(ambiguous or [])
    flags += rule_unrecognized_merchant(txns)
    flags += rule_amount_outlier(txns)
    flags += rule_foreign_anomaly(txns)
    flags += rule_round_number(txns)
    flags += rule_micro_charge_probe(txns)
    flags += rule_rapid_fire(txns)

    # Dedup by id (a pair can trip related rules with the same participants
    # only once per rule) and carry over dismissals from the previous run.
    dismissed = {f.flag_id for f in ledger.flags if f.status == "dismissed"}
    unique: Dict[str, Flag] = {}
    for f in flags:
        if f.flag_id in dismissed:
            f.status = "dismissed"
        unique.setdefault(f.flag_id, f)
    # PDF cross-verify flags (R7) are produced elsewhere and preserved.
    for f in ledger.flags:
        if f.rule == "STATEMENT_MISMATCH":
            unique.setdefault(f.flag_id, f)
    return list(unique.values())


def run_flags(use_llm: bool = False) -> None:
    ledger = store.load_ledger()
    from .match import match_ledger
    diag = match_ledger(ledger)          # ensure links are fresh
    ledger.flags = generate_flags(ledger, ambiguous=diag["ambiguous"])
    store.save_ledger(ledger)
    open_flags = [f for f in ledger.flags if f.status == "open"]
    by_kind = defaultdict(int)
    for f in open_flags:
        by_kind[f.kind] += 1
    log.info("flags: %d open (%s)", len(open_flags),
             ", ".join(f"{k}: {n}" for k, n in sorted(by_kind.items())) or "none")

    if use_llm and open_flags:
        from .llm import run_llm_flag_review
        run_llm_flag_review()


def run_dismiss(flag_ids: List[str]) -> None:
    ledger = store.load_ledger()
    by_id = ledger.flag_by_id()
    for fid in flag_ids:
        if fid in by_id:
            by_id[fid].status = "dismissed"
            log.info("dismissed %s", fid)
        else:
            log.warning("no such flag: %s", fid)
    store.save_ledger(ledger)
