"""Parse receipt emails into Receipt records — stdlib only.

MIME walking via email.message; HTML is flattened to text with a small
HTMLParser subclass (script/style dropped, block tags become newlines, table
cells become tabs so prices stay on the same line as their descriptions).
Extraction is rules-first: sender-domain merchant hints, an order-id regex
bank, and prioritized bottom-up total regexes. Receipts the rules can't fully
parse can be handed to the optional Claude pass (llm.py) later.
"""

from __future__ import annotations

import hashlib
import re
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple

import expense_config as config
from .models import Receipt, LineItem
from .normalize import parse_cents, normalize_merchant

_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table",
               "section", "article", "header", "footer"}


class _HtmlToText(HTMLParser):
    """Flatten HTML email bodies: drop script/style, newline on block tags,
    tab between table cells (keeps 'USB Cable ... $54.99' on one line)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: List[str] = []
        self._suppress = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._suppress += 1
        elif tag in ("td", "th"):
            self._chunks.append("\t")
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._suppress:
            self._suppress -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._suppress:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [re.sub(r"[ \t]{2,}", "\t", ln.strip()) for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def html_to_text(html: str) -> str:
    parser = _HtmlToText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:                      # malformed markup: fall back crudely
        return unescape(re.sub(r"<[^>]+>", " ", html))
    return parser.text()


def body_texts(msg: EmailMessage) -> List[str]:
    """Candidate text bodies, plain first then flattened HTML. Both are
    returned because multipart receipts often carry a stub text/plain
    ('view this email in your browser') while the numbers live in the HTML."""
    plain, html = None, None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        if part.get_content_disposition() == "attachment":
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = content
        elif ctype == "text/html" and html is None:
            html = content
    out = []
    if plain and plain.strip():
        out.append(plain)
    if html:
        out.append(html_to_text(html))
    return out


def body_text(msg: EmailMessage) -> str:
    """First non-empty body candidate (plain preferred)."""
    candidates = body_texts(msg)
    return candidates[0] if candidates else ""


# --------------------------------------------------------------------------- #
# Rules extraction
# --------------------------------------------------------------------------- #

_ORDER_ID_RES = [
    re.compile(r"\b(\d{3}-\d{7}-\d{7})\b"),                      # Amazon
    re.compile(r"\border\s*(?:number|no\.?|#|id)[:\s#]*([A-Z0-9][A-Z0-9-]{4,25})",
               re.IGNORECASE),
    re.compile(r"\bconfirmation\s*(?:number|no\.?|#|code)[:\s#]*([A-Z0-9][A-Z0-9-]{4,25})",
               re.IGNORECASE),
    re.compile(r"\binvoice\s*(?:number|no\.?|#)[:\s#]*([A-Z0-9][A-Z0-9-]{4,25})",
               re.IGNORECASE),
    re.compile(r"\breceipt\s*(?:number|no\.?|#)[:\s#]*([A-Z0-9][A-Z0-9-]{4,25})",
               re.IGNORECASE),
]

# Highest priority first; searched bottom-up because totals sit at the end of
# receipts and "Grand Total" must beat "Subtotal".
_TOTAL_LABELS = [
    r"grand total", r"order total", r"total charged", r"amount paid",
    r"amount charged", r"payment total", r"total due", r"total paid",
    r"you paid", r"total\b",
]
_TAX_RE = re.compile(r"\b(?:sales\s+)?tax[:\s\t]*\$?\s*([\d,]+\.\d{2})", re.IGNORECASE)
_TIP_RE = re.compile(r"\btip[:\s\t]*\$?\s*([\d,]+\.\d{2})", re.IGNORECASE)
_SHIPPING_RE = re.compile(r"\b(?:shipping|delivery)(?:\s*&\s*handling)?[:\s\t]*\$?\s*([\d,]+\.\d{2})",
                          re.IGNORECASE)

# A line-item line: description then amount at end of line, tab- or
# space-separated. Skips label lines (subtotal/tax/etc).
_LINE_ITEM_RE = re.compile(r"^(.{4,80}?)[\t ]+\$?\s*([\d,]+\.\d{2})$")
_LABEL_WORDS = re.compile(
    r"\b(sub)?total\b|\btax\b|\btip\b|\bshipping\b|\bbalance\b|\bpayment\b|"
    r"\bdiscount\b|\bfee\b|\brewards?\b|\bpoints?\b", re.IGNORECASE)
_QTY_RE = re.compile(r"^(?:qty\s*)?(\d{1,3})\s*[xX@]\s*(.+)$")


def extract_order_id(text: str) -> str:
    for rx in _ORDER_ID_RES:
        m = rx.search(text)
        if m:
            return m.group(1)
    return ""


def extract_total(text: str) -> Optional[int]:
    lines = text.splitlines()
    for label in _TOTAL_LABELS:
        rx = re.compile(label + r"[:\s\t]*\$?\s*([\d,]+\.\d{2})", re.IGNORECASE)
        for line in reversed(lines):
            m = rx.search(line)
            if m:
                return parse_cents(m.group(1))
    return None


def extract_line_items(text: str) -> List[LineItem]:
    items: List[LineItem] = []
    for line in text.splitlines():
        line = line.strip()
        m = _LINE_ITEM_RE.match(line)
        if not m:
            continue
        desc, amount = m.group(1).strip(" .:\t-"), parse_cents(m.group(2))
        if not desc or amount is None or _LABEL_WORDS.search(desc):
            continue
        quantity = None
        qm = _QTY_RE.match(desc)
        if qm:
            quantity, desc = float(qm.group(1)), qm.group(2).strip()
        items.append(LineItem(description=desc, quantity=quantity,
                              amount_cents=amount))
    return items[:50]


def merchant_from_sender(from_addr: str, subject: str) -> Tuple[str, str]:
    """(raw guess, normalized). The From display-name is the best raw guess
    ('Amazon.com <auto-confirm@amazon.com>'); fall back to the domain root."""
    name, addr = parseaddr(from_addr)
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    guess = name.strip() or domain.split(".")[0].title()
    return guess, normalize_merchant(guess)


def parse_receipt(msg: EmailMessage, account: str, folder: str,
                  imap_uid: str) -> Receipt:
    """EmailMessage -> Receipt, rules-only. extraction_method reflects how far
    the rules got: 'rules' when a total was found, else 'none' (LLM candidate)."""
    from_header = str(msg.get("From", ""))
    subject = str(msg.get("Subject", ""))
    _, addr = parseaddr(from_header)
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""

    date_iso = ""
    try:
        dt = parsedate_to_datetime(str(msg.get("Date", "")))
        if dt is not None:
            date_iso = dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        pass

    # Try each body candidate until one yields a total (a stub text/plain part
    # must not mask an HTML body that carries the actual numbers).
    candidates = body_texts(msg)
    text, total = (candidates[0] if candidates else ""), None
    for cand in candidates:
        total = extract_total(cand)
        if total is not None:
            text = cand
            break

    guess, merchant = merchant_from_sender(from_header, subject)
    items = extract_line_items(text)

    tax = _TAX_RE.search(text)
    tip = _TIP_RE.search(text)
    shipping = _SHIPPING_RE.search(text)

    return Receipt(
        message_id=str(msg.get("Message-ID", "")).strip(),
        account=account,
        imap_uid=imap_uid,
        folder=folder,
        email_date=date_iso,
        from_addr=addr,
        from_domain=domain,
        subject=subject,
        merchant=merchant,
        merchant_guess=guess,
        order_id=extract_order_id(text),
        total_cents=total,
        tax_cents=parse_cents(tax.group(1)) if tax else None,
        tip_cents=parse_cents(tip.group(1)) if tip else None,
        shipping_cents=parse_cents(shipping.group(1)) if shipping else None,
        line_items=[li.to_dict() for li in items],
        extraction_method="rules" if total is not None else "none",
        body_sha1=hashlib.sha1(text.encode("utf-8", "replace")).hexdigest(),
    )


# --------------------------------------------------------------------------- #
# Prefilter: which headers are worth a full body fetch?
# --------------------------------------------------------------------------- #

def _domain_matches(domain: str, allowed: List[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in allowed)


def classify_header(from_addr: str, subject: str) -> str:
    """'receipt' | 'alert' | '' from headers alone (no body fetched yet).
    Alert domains are checked first — a bank alert is never a receipt."""
    _, addr = parseaddr(from_addr or "")
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    subject = subject or ""

    for provider in config.CARD_PROVIDERS.values():
        if _domain_matches(domain, provider["alert_sender_domains"]):
            if any(re.search(p, subject, re.IGNORECASE)
                   for p in provider["alert_subject_keywords"]):
                return "alert"
            return ""    # bank mail that isn't a transaction alert (statements, promos)

    if any(re.search(p, subject, re.IGNORECASE)
           for p in config.MARKETING_SUBJECT_EXCLUDES):
        return ""
    if _domain_matches(domain, config.RECEIPT_SENDER_DOMAINS):
        return "receipt"
    if any(re.search(p, subject, re.IGNORECASE)
           for p in config.RECEIPT_SUBJECT_KEYWORDS):
        return "receipt"
    return ""
