"""IMAP scanning for receipt and card-alert emails — stdlib imaplib only.

Safety properties (all load-bearing, don't loosen):
  * mailboxes are opened READ-ONLY and bodies fetched with BODY.PEEK, so the
    scan can never mark, move, or delete mail
  * passwords are app-specific, resolved env var -> macOS Keychain -> .env
    (load_dotenv runs before this), and never logged
  * every fetched message is cached as raw .eml under expense_data/ keyed by
    message-id hash; a rescan fetches only unseen UIDs (per-folder UID index,
    voided when the server's UIDVALIDITY changes)

iCloud quirks handled: folder names quoted + modified-UTF-7 encoded, SEARCH
dates use hardcoded English month names (locale strftime would break on
non-English systems), SINCE/BEFORE filter on INTERNALDATE so results are
re-filtered by the parsed Date header afterwards.
"""

from __future__ import annotations

import hashlib
import imaplib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from typing import Dict, List, Optional, Tuple

import expense_config as config
from .logging_setup import get_logger
from .models import Receipt
from .email_parse import classify_header, parse_receipt
from .card_alerts import parse_alert
from . import store

log = get_logger()

# Overridable factory so tests can inject a FakeIMAP (host, port) -> connection.
imap_factory = imaplib.IMAP4_SSL

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def imap_date(iso: str) -> str:
    """'2026-07-01' -> '01-Jul-2026' with English months, never locale %b."""
    dt = datetime.strptime(iso, "%Y-%m-%d")
    return f"{dt.day:02d}-{_MONTHS[dt.month - 1]}-{dt.year}"


def encode_folder(name: str) -> str:
    """Quote (+ modified-UTF-7 encode) a mailbox name for SELECT.
    iCloud allows spaces and non-ASCII in folder names."""
    try:
        encoded = name.encode("ascii")
    except UnicodeEncodeError:
        # RFC 3501 modified UTF-7: '&' escapes, base64 variant with ','.
        out, buf = [], []

        def flush():
            if buf:
                b64 = "".join(buf).encode("utf-16-be")
                import base64
                out.append("&" + base64.b64encode(b64).decode("ascii")
                           .rstrip("=").replace("/", ",") + "-")
                buf.clear()

        for ch in name:
            if 0x20 <= ord(ch) <= 0x7E:
                flush()
                out.append("&-" if ch == "&" else ch)
            else:
                buf.append(ch)
        flush()
        encoded = "".join(out).encode("ascii")
    return '"' + encoded.decode("ascii").replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #

def _keychain_password(email: str) -> str:
    """macOS Keychain lookup; silently empty anywhere it can't work."""
    if sys.platform != "darwin":
        return ""
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", config.KEYCHAIN_SERVICE, "-a", email, "-w"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def resolve_credentials(account: dict) -> Tuple[str, str]:
    """(email, password) for an EMAIL_ACCOUNTS entry, or ('', '') with a
    helpful log line when unconfigured."""
    email = os.environ.get(account["email_env"], "").strip()
    if not email:
        log.info("account %s skipped: %s not set (see .env.example)",
                 account["label"], account["email_env"])
        return "", ""
    password = os.environ.get(account["password_env"], "").strip()
    if not password:
        password = _keychain_password(email)
    if not password:
        log.warning(
            "account %s skipped: no app-specific password found. Set %s, or "
            "store one in the Keychain: security add-generic-password "
            "-s %s -a %s -w  (generate at account.apple.com for iCloud, "
            "myaccount.google.com/apppasswords for Gmail)",
            account["label"], account["password_env"],
            config.KEYCHAIN_SERVICE, email)
        return "", ""
    return email, password


# --------------------------------------------------------------------------- #
# Scan index + .eml cache
# --------------------------------------------------------------------------- #

def _index_path() -> str:
    return config.data_path("scan_index.json")


def _load_index() -> dict:
    if not os.path.exists(_index_path()):
        return {}
    with open(_index_path(), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_index(index: dict) -> None:
    os.makedirs(config.EXPENSE_DATA_DIR, exist_ok=True)
    tmp = _index_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
    os.replace(tmp, _index_path())


def _cache_dir() -> str:
    return config.data_path(config.RECEIPTS_CACHE_DIRNAME)


def cache_message(message_id: str, raw: bytes) -> str:
    os.makedirs(_cache_dir(), exist_ok=True)
    name = hashlib.sha1(message_id.encode("utf-8", "replace")).hexdigest() + ".eml"
    path = os.path.join(_cache_dir(), name)
    if not os.path.exists(path):
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# FETCH response parsing
# --------------------------------------------------------------------------- #

_UID_RE = re.compile(rb"UID\s+(\d+)")


def parse_fetch_response(data: list) -> Dict[str, bytes]:
    """imaplib FETCH payload -> {uid: raw bytes}. The payload interleaves
    (meta, literal) tuples with plain b')' frames; UID is read from meta."""
    out: Dict[str, bytes] = {}
    for item in data or []:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        m = _UID_RE.search(item[0] or b"")
        if m:
            out[m.group(1).decode()] = item[1] or b""
    return out


def _chunks(seq: List[str], n: int) -> List[List[str]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #

def _search_uids(conn, since: Optional[str], until: Optional[str]) -> List[str]:
    criteria: List[str] = []
    if since:
        criteria += ["SINCE", imap_date(since)]
    if until:
        # BEFORE is exclusive; add a day so --until is inclusive like --since.
        next_day = (datetime.strptime(until, "%Y-%m-%d")
                    + timedelta(days=1)).strftime("%Y-%m-%d")
        criteria += ["BEFORE", imap_date(next_day)]
    if not criteria:
        criteria = ["ALL"]
    status, data = conn.uid("SEARCH", None, *criteria)
    if status != "OK":
        return []
    return (data[0] or b"").decode().split()


def scan_folder(conn, account_label: str, folder: str,
                since: Optional[str], until: Optional[str],
                limit: Optional[int], use_cache: bool,
                ledger: store.Ledger) -> Tuple[int, int]:
    """Scan one folder into the ledger. Returns (new receipts, new alerts)."""
    status, data = conn.select(encode_folder(folder), readonly=True)
    if status != "OK":
        log.warning("%s: cannot open folder %r", account_label, folder)
        return 0, 0
    uidvalidity = ""
    status, vdata = conn.status(encode_folder(folder), "(UIDVALIDITY)")
    if status == "OK" and vdata and vdata[0]:
        m = re.search(rb"UIDVALIDITY\s+(\d+)", vdata[0])
        uidvalidity = m.group(1).decode() if m else ""

    index = _load_index()
    fkey = f"{account_label}/{folder}"
    entry = index.get(fkey, {})
    if entry.get("uidvalidity") != uidvalidity:
        entry = {"uidvalidity": uidvalidity, "scanned_uids": []}
    scanned = set(entry["scanned_uids"])

    uids = _search_uids(conn, since, until)
    fresh = [u for u in uids if u not in scanned] if use_cache else uids
    if limit:
        fresh = fresh[:limit]
    if not fresh:
        log.info("%s %s: nothing new (%d already scanned)",
                 account_label, folder, len(uids))
        return 0, 0
    log.info("%s %s: %d message(s) to inspect", account_label, folder, len(fresh))

    # Phase 1: headers only, chunked, PEEK so nothing gets marked \Seen.
    interesting: Dict[str, str] = {}   # uid -> 'receipt' | 'alert'
    parser = BytesParser(policy=policy.default)
    for chunk in _chunks(fresh, config.IMAP_FETCH_CHUNK):
        status, data = conn.uid(
            "FETCH", ",".join(chunk),
            "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
        if status != "OK":
            continue
        for uid, raw in parse_fetch_response(data).items():
            headers = parser.parsebytes(raw)
            kind = classify_header(str(headers.get("From", "")),
                                   str(headers.get("Subject", "")))
            if kind:
                interesting[uid] = kind

    # Phase 2: full bodies for the survivors only.
    receipts_by_id = ledger.receipt_by_id()
    txn_ids = set(t.txn_id for t in ledger.transactions)
    new_receipts = new_alerts = 0
    for chunk in _chunks(sorted(interesting), config.IMAP_FETCH_CHUNK):
        status, data = conn.uid("FETCH", ",".join(chunk), "(BODY.PEEK[])")
        if status != "OK":
            continue
        for uid, raw in parse_fetch_response(data).items():
            msg = parser.parsebytes(raw)
            message_id = str(msg.get("Message-ID", "")).strip()
            path = cache_message(message_id or f"{fkey}/{uid}", raw)
            if interesting[uid] == "alert":
                txn = parse_alert(msg)
                if txn and txn.txn_id not in txn_ids:
                    ledger.transactions.append(txn)
                    txn_ids.add(txn.txn_id)
                    new_alerts += 1
            else:
                receipt = parse_receipt(msg, account_label, folder, uid)
                receipt.body_cache_path = path
                if receipt.receipt_id not in receipts_by_id:
                    ledger.receipts.append(receipt)
                    receipts_by_id[receipt.receipt_id] = receipt
                    new_receipts += 1

    entry["scanned_uids"] = sorted(scanned | set(fresh), key=int)
    index[fkey] = entry
    _save_index(index)
    return new_receipts, new_alerts


def run_scan(account: Optional[str], folder: Optional[str],
             since: Optional[str], until: Optional[str],
             limit: Optional[int], use_cache: bool, use_llm: bool) -> None:
    accounts = [a for a in config.EMAIL_ACCOUNTS
                if account is None or a["label"] == account]
    if not accounts:
        valid = ", ".join(a["label"] for a in config.EMAIL_ACCOUNTS)
        raise SystemExit(f"unknown account {account!r} — configured: {valid}")

    ledger = store.load_ledger()
    total_r = total_a = 0
    for acct in accounts:
        email, password = resolve_credentials(acct)
        if not email:
            continue
        try:
            conn = imap_factory(acct["host"], acct.get("port", 993))
        except OSError as exc:
            log.warning("%s: cannot reach %s: %s", acct["label"], acct["host"], exc)
            continue
        try:
            try:
                conn.login(email, password)
            except imaplib.IMAP4.error as exc:
                log.warning(
                    "%s: login failed (%s). iCloud/Gmail require an "
                    "APP-SPECIFIC password, not your account password — see "
                    ".env.example for where to generate one.",
                    acct["label"], exc)
                continue
            folders = [folder] if folder else acct.get("folders", ["INBOX"])
            for fld in folders:
                r, a = scan_folder(conn, acct["label"], fld, since, until,
                                   limit, use_cache, ledger)
                total_r += r
                total_a += a
                store.save_ledger(ledger)   # checkpoint per folder
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    # Track the scanned range so CHARGE_NO_RECEIPT only fires inside it.
    if since or until:
        meta_path = config.data_path("scan_range.json")
        rng = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as fh:
                rng = json.load(fh)
        if since:
            rng["since"] = min(rng.get("since", since), since)
        if until:
            rng["until"] = max(rng.get("until", until), until)
        os.makedirs(config.EXPENSE_DATA_DIR, exist_ok=True)
        tmp = meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rng, fh)
        os.replace(tmp, meta_path)

    store.save_ledger(ledger)
    log.info("scan complete: %d new receipt(s), %d new alert transaction(s)",
             total_r, total_a)

    if use_llm and total_r:
        from .llm import run_llm_extraction
        run_llm_extraction()
