"""Optional Claude passes (opt-in via --llm). Two jobs, both strictly
advisory — the deterministic rules always win on numbers:

  Pass A (extraction): itemize receipt emails the rules couldn't fully parse.
    Fills merchant/order_id/total/line_items on receipts with
    extraction_method == "none". If Claude's total disagrees with a total the
    rules DID find, the rules total is kept and the disagreement is noted.

  Pass B (flag review): second opinion on open warn/high flags, given a
    compact history table. Sets llm_agrees/llm_reason; never deletes a flag.

Degrades gracefully: no `anthropic` package or no ANTHROPIC_API_KEY logs a
notice and returns — the rules-only pipeline is always complete on its own.

Privacy note: --llm sends receipt email text and merchant/amount/date rows to
the Anthropic API. Don't enable it if that's not acceptable to you.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional

import expense_config as config
from .logging_setup import get_logger
from .models import Receipt, Flag, cents_to_dollars
from .normalize import parse_cents
from . import store

log = get_logger()


# Copied from grid_archive/judge.py — tolerant JSON-array extraction.
def _extract_json(text: str):
    """Pull the JSON array out of the model's reply, tolerating stray prose."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None


def _client():
    """Anthropic client, or None (with a logged reason) when unavailable."""
    try:
        import anthropic
    except ImportError:
        log.info("--llm skipped: `anthropic` not installed "
                 "(pip install .[llm]); continuing rules-only")
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("--llm skipped: ANTHROPIC_API_KEY not set; "
                 "continuing rules-only")
        return None
    return anthropic.Anthropic()


def _response_text(resp) -> str:
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", "") == "text")


def _read_body(receipt: Receipt) -> str:
    """Receipt body text from the .eml cache, flattened and capped."""
    path = receipt.body_cache_path
    if not path or not os.path.exists(path):
        return ""
    from email import policy
    from email.parser import BytesParser
    from .email_parse import body_texts
    with open(path, "rb") as fh:
        msg = BytesParser(policy=policy.default).parsebytes(fh.read())
    candidates = body_texts(msg)
    text = max(candidates, key=len) if candidates else ""
    return text[:config.LLM_BODY_CHAR_CAP]


_EXTRACT_INSTRUCTION = """\
You are extracting purchase data from receipt/order emails. Below are {n}
emails, each labelled EMAIL <k> with sender, subject, and body text.

For EACH email return one JSON object. Use null for anything not present.
Amounts are decimal strings like "54.99" (never cents, never currency signs).

Respond with ONLY a JSON array, no prose:
[{{"email": <k>, "merchant": "...", "order_id": "..." or null,
   "total": "54.99" or null, "tax": ... or null, "tip": ... or null,
   "line_items": [{{"description": "...", "quantity": 1, "amount": "12.34"}}]}}]
"""


def _extract_batch(client, batch: List[Receipt]) -> None:
    lines = []
    for i, r in enumerate(batch, 1):
        body = _read_body(r)
        lines.append(f"EMAIL {i}\nFrom: {r.from_addr}\nSubject: {r.subject}\n"
                     f"Body:\n{body}\n")
    prompt = "\n".join(lines) + "\n" + _EXTRACT_INSTRUCTION.format(n=len(batch))

    resp = client.messages.create(
        model=config.LLM_MODEL,
        max_tokens=config.LLM_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    results = _extract_json(_response_text(resp))
    if not isinstance(results, list):
        log.warning("llm extraction: unparseable response for batch of %d",
                    len(batch))
        return

    by_index = {int(v["email"]): v for v in results
                if isinstance(v, dict) and "email" in v}
    for idx, receipt in enumerate(batch, 1):
        v = by_index.get(idx)
        if not v:
            continue
        llm_total = parse_cents(v.get("total"))
        if receipt.total_cents is None:
            receipt.total_cents = llm_total
        elif llm_total is not None and llm_total != receipt.total_cents:
            # Rules found a number; Claude disagrees. Rules win, but say so.
            receipt.extraction_note = (
                f"llm total {cents_to_dollars(llm_total)} disagreed with "
                f"rules total {cents_to_dollars(receipt.total_cents)}; "
                f"rules kept")
        if v.get("merchant") and not receipt.merchant:
            from .normalize import normalize_merchant
            receipt.merchant = normalize_merchant(str(v["merchant"]))
        if v.get("order_id") and not receipt.order_id:
            receipt.order_id = str(v["order_id"])[:40]
        if receipt.tax_cents is None:
            receipt.tax_cents = parse_cents(v.get("tax"))
        if receipt.tip_cents is None:
            receipt.tip_cents = parse_cents(v.get("tip"))
        if not receipt.line_items and isinstance(v.get("line_items"), list):
            items = []
            for li in v["line_items"][:50]:
                if not isinstance(li, dict):
                    continue
                amount = parse_cents(li.get("amount"))
                qty = li.get("quantity")
                items.append({"description": str(li.get("description", ""))[:120],
                              "quantity": float(qty) if qty else None,
                              "unit_price_cents": None,
                              "amount_cents": amount, "category": ""})
            receipt.line_items = items
        if receipt.extraction_method == "none":
            receipt.extraction_method = "llm"


def run_llm_extraction() -> None:
    client = _client()
    if client is None:
        return
    ledger = store.load_ledger()
    todo = [r for r in ledger.receipts
            if r.extraction_method == "none" and r.body_cache_path]
    if not todo:
        log.info("llm extraction: nothing to do")
        return
    log.info("llm extraction: %d receipt(s)", len(todo))
    size = config.LLM_RECEIPTS_PER_REQUEST
    for start in range(0, len(todo), size):
        batch = todo[start:start + size]
        try:
            _extract_batch(client, batch)
        except Exception as exc:  # noqa: BLE001 - one batch must not sink the pass
            log.warning("llm extraction batch %d failed: %s", start // size, exc)
        store.save_ledger(ledger)   # checkpoint per batch


_REVIEW_INSTRUCTION = """\
You are reviewing automated flags on personal credit-card activity. Below are
{n} flags, each labelled FLAG <k> with the rule, details, and the cardholder's
history with that merchant. For EACH flag say whether it genuinely deserves
the cardholder's attention (concerning=true) or is probably benign
(concerning=false), with one short line of reasoning.

Respond with ONLY a JSON array, no prose:
[{{"flag": <k>, "concerning": true|false, "reason": "<one line>"}}]
"""


def _flag_context(flag: Flag, ledger: store.Ledger) -> str:
    txn_by_id = ledger.txn_by_id()
    lines = [f"Rule: {flag.rule} ({flag.kind}, {flag.severity})",
             f"Detail: {flag.detail}"]
    merchants = set()
    for tid in flag.txn_ids:
        t = txn_by_id.get(tid)
        if t:
            merchants.add(t.merchant)
    for merchant in sorted(merchants):
        history = [t for t in ledger.active_transactions()
                   if t.merchant == merchant]
        history.sort(key=lambda t: t.date)
        rows = ", ".join(f"{t.date}: ${cents_to_dollars(t.amount_cents)}"
                         for t in history[-8:])
        lines.append(f"History with {merchant}: {rows}")
    return "\n".join(lines)


def _review_batch(client, batch: List[Flag], ledger: store.Ledger) -> None:
    lines = [f"FLAG {i}\n{_flag_context(f, ledger)}\n"
             for i, f in enumerate(batch, 1)]
    prompt = "\n".join(lines) + "\n" + _REVIEW_INSTRUCTION.format(n=len(batch))
    resp = client.messages.create(
        model=config.LLM_MODEL,
        max_tokens=config.LLM_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    results = _extract_json(_response_text(resp))
    if not isinstance(results, list):
        log.warning("llm review: unparseable response for batch of %d", len(batch))
        return
    by_index = {int(v["flag"]): v for v in results
                if isinstance(v, dict) and "flag" in v}
    for idx, flag in enumerate(batch, 1):
        v = by_index.get(idx)
        if not v:
            continue
        flag.llm_reviewed = True
        flag.llm_agrees = bool(v.get("concerning"))
        flag.llm_reason = str(v.get("reason", ""))[:300]


def run_llm_flag_review() -> None:
    client = _client()
    if client is None:
        return
    ledger = store.load_ledger()
    todo = [f for f in ledger.flags
            if f.status == "open" and not f.llm_reviewed
            and f.severity in ("warn", "high")]
    if not todo:
        log.info("llm review: nothing to do")
        return
    log.info("llm review: %d flag(s)", len(todo))
    size = config.LLM_FLAGS_PER_REQUEST
    for start in range(0, len(todo), size):
        batch = todo[start:start + size]
        try:
            _review_batch(client, batch, ledger)
        except Exception as exc:  # noqa: BLE001
            log.warning("llm review batch %d failed: %s", start // size, exc)
        store.save_ledger(ledger)
