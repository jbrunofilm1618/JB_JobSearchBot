"""Receipt <-> transaction reconciliation.

Scoring (weights in expense_config, stdlib difflib only):
  amount   exact match 0.55; within max($1, 1%) 0.35; receipt total unknown 0.15
  date     0.15 scaled across a -3..+5 day window (charges post late,
           pre-auths occasionally land early)
  merchant 0.30 x max(SequenceMatcher ratio, token overlap, domain hint)

Pairs are linked greedily one-to-one by descending score above
MATCH_THRESHOLD; a runner-up within MATCH_AMBIGUITY_MARGIN of the winner is
recorded so flags.py can raise AMBIGUOUS_MATCH. A second pass reconstructs
split charges (one Amazon order shipping as several transactions that sum
exactly to the receipt total).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from itertools import combinations
from typing import Dict, List, Optional, Tuple

import expense_config as config
from .logging_setup import get_logger
from .models import Transaction, Receipt
from .normalize import days_between, merchant_tokens
from . import store

log = get_logger()


def merchant_similarity(receipt: Receipt, txn: Transaction) -> float:
    a, b = receipt.merchant, txn.merchant
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = merchant_tokens(a), merchant_tokens(b)
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb))) if ta and tb else 0.0
    # Domain hint: statement description contains the sender's domain root
    # ("HELP.UBER.COM" in the description vs from_domain uber.com).
    domain_root = receipt.from_domain.split(".")[0].upper() if receipt.from_domain else ""
    hint = 1.0 if domain_root and len(domain_root) > 2 and (
        domain_root in txn.description.upper() or domain_root in b) else 0.0
    return max(ratio, overlap, hint)


def _amount_score(receipt: Receipt, txn: Transaction) -> Optional[float]:
    """None means 'amounts actively disagree' -> pair is not a candidate."""
    if receipt.total_cents is None:
        return config.WEIGHT_AMOUNT_UNKNOWN
    if txn.amount_cents == receipt.total_cents:
        return config.WEIGHT_AMOUNT_EXACT
    tolerance = max(config.MATCH_AMOUNT_TOLERANCE_CENTS,
                    int(abs(receipt.total_cents) * config.MATCH_AMOUNT_TOLERANCE_PCT))
    if abs(txn.amount_cents - receipt.total_cents) <= tolerance:
        return config.WEIGHT_AMOUNT_NEAR
    return None


def _date_score(receipt: Receipt, txn: Transaction) -> Optional[float]:
    """None means 'outside the window' -> pair is not a candidate."""
    gap = days_between(receipt.email_date, txn.date)
    if gap is None:
        return 0.0
    if -config.MATCH_DATE_BEFORE_DAYS <= gap <= config.MATCH_DATE_AFTER_DAYS:
        span = max(config.MATCH_DATE_BEFORE_DAYS, config.MATCH_DATE_AFTER_DAYS)
        return config.WEIGHT_DATE * (1.0 - abs(gap) / (span + 1))
    return None


def score_pair(receipt: Receipt, txn: Transaction) -> Optional[float]:
    """Composite score, or None when the pair can't be a match at all.
    Refund pairing: a negative txn only matches a refund-looking receipt."""
    refund_receipt = _is_refund_receipt(receipt)
    if (txn.amount_cents < 0) != refund_receipt:
        return None
    if refund_receipt and receipt.total_cents is not None:
        # compare magnitudes; refund receipts state a positive amount
        receipt = _with_total(receipt, -abs(receipt.total_cents))
    amount = _amount_score(receipt, txn)
    if amount is None:
        return None
    date = _date_score(receipt, txn)
    if date is None:
        return None
    return amount + date + config.WEIGHT_MERCHANT * merchant_similarity(receipt, txn)


def _is_refund_receipt(receipt: Receipt) -> bool:
    subject = receipt.subject.lower()
    return any(w in subject for w in ("refund", "return process", "money back",
                                      "credit issued"))


def _with_total(receipt: Receipt, total: int) -> Receipt:
    clone = Receipt.from_dict(receipt.to_dict())
    clone.total_cents = total
    return clone


def run_match(rematch: bool = False) -> None:
    ledger = store.load_ledger()
    match_ledger(ledger, rematch=rematch)
    store.save_ledger(ledger)


def match_ledger(ledger: store.Ledger, rematch: bool = False) -> Dict[str, list]:
    """Link receipts and transactions in place. Returns diagnostics used by
    flags.py: {'ambiguous': [(receipt_id, txn_id, winner_txn_id), ...]}."""
    if rematch:
        for t in ledger.transactions:
            t.matched_receipt_id, t.match_score, t.match_method = "", None, ""
        for r in ledger.receipts:
            r.matched_txn_ids = []

    txns = [t for t in ledger.active_transactions() if not t.matched_receipt_id]
    receipts = [r for r in ledger.receipts if not r.matched_txn_ids]

    # Score all viable pairs.
    scored: List[Tuple[float, Receipt, Transaction]] = []
    for r in receipts:
        for t in txns:
            s = score_pair(r, t)
            if s is not None and s >= config.MATCH_THRESHOLD:
                scored.append((s, r, t))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Greedy one-to-one assignment; track near-ties for AMBIGUOUS_MATCH.
    ambiguous: List[Tuple[str, str, str]] = []
    used_r, used_t = set(), set()
    for s, r, t in scored:
        if r.receipt_id in used_r or t.txn_id in used_t:
            continue
        # Runner-up check: another still-unused txn scoring within the margin
        # means this link is a coin flip worth surfacing, not silent.
        runner = next(((s2, t2) for s2, r2, t2 in scored
                       if r2.receipt_id == r.receipt_id
                       and t2.txn_id != t.txn_id and t2.txn_id not in used_t),
                      None)
        if runner and s - runner[0] <= config.MATCH_AMBIGUITY_MARGIN:
            ambiguous.append((r.receipt_id, runner[1].txn_id, t.txn_id))
        used_r.add(r.receipt_id)
        used_t.add(t.txn_id)
        exact = (r.total_cents is not None
                 and abs(r.total_cents) == abs(t.amount_cents))
        t.matched_receipt_id = r.receipt_id
        t.match_score = round(s, 3)
        t.match_method = "exact" if exact else "fuzzy"
        r.matched_txn_ids = [t.txn_id]

    # Split-charge pass: an unmatched receipt whose total equals the exact sum
    # of 2..N unmatched same-merchant transactions in the window (Amazon ships
    # one order as several charges).
    split_matched = 0
    for r in ledger.receipts:
        if r.matched_txn_ids or r.total_cents is None or r.total_cents <= 0:
            continue
        candidates = [
            t for t in ledger.active_transactions()
            if not t.matched_receipt_id and t.amount_cents > 0
            and t.amount_cents < r.total_cents
            and merchant_similarity(r, t) >= 0.6
            and _date_score(r, t) is not None
        ]
        if len(candidates) < 2:
            continue
        found = None
        for k in range(2, min(config.MATCH_SPLIT_MAX_PARTS, len(candidates)) + 1):
            for combo in combinations(candidates, k):
                if sum(t.amount_cents for t in combo) == r.total_cents:
                    found = combo
                    break
            if found:
                break
        if found:
            r.matched_txn_ids = [t.txn_id for t in found]
            for t in found:
                t.matched_receipt_id = r.receipt_id
                t.match_method = "split"
                t.match_score = 1.0
            split_matched += 1

    matched = sum(1 for t in ledger.active_transactions() if t.matched_receipt_id)
    log.info("match: %d/%d active transactions linked (%d split receipts, "
             "%d ambiguous)", matched, len(ledger.active_transactions()),
             split_matched, len(ambiguous))
    return {"ambiguous": ambiguous}
