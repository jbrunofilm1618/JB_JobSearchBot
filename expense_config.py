"""
expense-report — tunable configuration.

This is the ONE file you edit to tune the expense tool. IMAP settings, receipt
sender lists, Amex CSV column mapping, matching thresholds, and every flag
rule's knobs live here. Nothing below is magic; change a value, rerun, and the
ledger/report update.

All money in the ledger is INTEGER CENTS. Every threshold here that represents
money is therefore also in cents (suffix `_CENTS`).

Secrets (ICLOUD_EMAIL / ICLOUD_APP_PASSWORD / ANTHROPIC_API_KEY) never live in
this file — they come from the environment, the macOS Keychain, or the
gitignored .env. See .env.example.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Directories & files. EXPENSE_DATA_DIR is env-overridable so the test suite
# can redirect everything to a tempdir. All of these are gitignored — real
# financial data never leaves your machine or enters version control.
# --------------------------------------------------------------------------- #
EXPENSE_DATA_DIR = os.environ.get("EXPENSE_DATA_DIR", "expense_data")
LEDGER_JSON = "ledger.json"            # under EXPENSE_DATA_DIR
TRANSACTIONS_CSV = "transactions.csv"  # reviewable slice, under EXPENSE_DATA_DIR
RECEIPTS_CACHE_DIRNAME = "receipts_cache"
REPORTS_DIRNAME = "reports"
STATEMENTS_DIR = os.environ.get("EXPENSE_STATEMENTS_DIR", "statements")
LOG_FILE = "expense_report.log"

# --------------------------------------------------------------------------- #
# Email accounts scanned for receipts and Amex alert emails. Passwords are
# APP-SPECIFIC passwords, never your real account password:
#   iCloud: account.apple.com -> Sign-In & Security -> App-Specific Passwords
#   Gmail:  myaccount.google.com/apppasswords (needs 2-Step Verification on)
# Each password is looked up in this order:
#   exported env var (password_env) -> macOS Keychain -> .env file.
# Keychain setup (recommended, password never touches a plaintext file):
#   security add-generic-password -s expense-report -a you@icloud.com -w
#   security add-generic-password -s expense-report -a you@gmail.com -w
# The email address itself comes from email_env so this file stays shareable.
# --------------------------------------------------------------------------- #
EMAIL_ACCOUNTS = [
    {
        "label": "icloud",
        "host": "imap.mail.me.com",
        "port": 993,
        "email_env": "ICLOUD_EMAIL",
        "password_env": "ICLOUD_APP_PASSWORD",
        "folders": ["INBOX"],
    },
    {
        "label": "gmail",
        "host": "imap.gmail.com",
        "port": 993,
        "email_env": "GMAIL_EMAIL",
        "password_env": "GMAIL_APP_PASSWORD",
        "folders": ["INBOX"],
    },
]
KEYCHAIN_SERVICE = "expense-report"    # -s value for the Keychain lookup
IMAP_FETCH_CHUNK = 50                  # UIDs per FETCH round-trip (throttle-friendly)

# --------------------------------------------------------------------------- #
# Card providers. Transaction ALERT emails are the automatic day-to-day feed:
#   Amex:        Account Services -> Alerts -> purchase notifications,
#                threshold $0, delivery = email
#   Capital One: card -> Alerts & Notifications -> transaction alerts, email
# Alert emails parse into provisional transactions (source "alert"); a later
# CSV import reconciles and supersedes them, so the CSV download stays the
# audit-grade record and the PDF the official cross-check.
# --------------------------------------------------------------------------- #
CARD_PROVIDERS = {
    "amex": {
        "alert_sender_domains": ["americanexpress.com", "aexp.com"],
        "alert_subject_keywords": [
            r"\bpurchase (approved|alert)\b",
            r"\byou have a new transaction\b",
            r"\btransaction alert\b",
            r"\bcharge (was )?(approved|made)\b",
        ],
    },
    "capitalone": {
        "alert_sender_domains": ["capitalone.com", "notification.capitalone.com"],
        "alert_subject_keywords": [
            r"\b(a )?new transaction\b",
            r"\btransaction alert\b",
            r"\bpurchase (alert|notification)\b",
            r"\bcard (was|just) used\b",
        ],
    },
}

# When a CSV row matches an alert-sourced txn (exact cents, date within this
# window, merchant similarity >= the ratio), the alert txn is superseded.
ALERT_SUPERSEDE_WINDOW_DAYS = 3
ALERT_SUPERSEDE_MERCHANT_RATIO = 0.6

# Emails survive the receipt prefilter if the From domain matches one of these
# (subdomain-aware: "amazon.com" matches "shipment-tracking.amazon.com") OR the
# subject matches RECEIPT_SUBJECT_KEYWORDS.
RECEIPT_SENDER_DOMAINS = [
    "amazon.com",
    "apple.com", "itunes.com", "icloud.com",
    "uber.com", "lyft.com",
    "doordash.com", "grubhub.com", "seamless.com", "postmates.com",
    "paypal.com", "venmo.com", "squareup.com", "square.com", "stripe.com",
    "aa.com", "delta.com", "united.com", "southwest.com", "jetblue.com",
    "alaskaair.com",
    "airbnb.com", "vrbo.com", "hotels.com", "expedia.com", "booking.com",
    "marriott.com", "hilton.com", "hyatt.com",
    "netflix.com", "spotify.com", "hulu.com", "hbomax.com", "max.com",
    "adobe.com", "dropbox.com", "google.com", "github.com",
    "bhphotovideo.com", "adorama.com", "sweetwater.com",
    "ebay.com", "etsy.com", "target.com", "walmart.com", "bestbuy.com",
    "costco.com", "homedepot.com", "lowes.com",
    "instacart.com", "wholefood.com",
    "frame.io", "vimeo.com", "backblaze.com",
]

# Case-insensitive regexes matched against the Subject header.
RECEIPT_SUBJECT_KEYWORDS = [
    r"\breceipt\b",
    r"\binvoice\b",
    r"\byour (order|purchase|payment|trip|ride|stay|booking)\b",
    r"\border (confirmation|shipped|placed)\b",
    r"\bpayment (received|confirmation|processed)\b",
    r"\bconfirmation (number|#)\b",
    r"\bthanks? for your (order|purchase|payment)\b",
    r"\be-?ticket\b",
    r"\bitinerary\b",
    r"\bsubscription (renewal|receipt|confirmed)\b",
    r"\brenewal notice\b",
]

# Obvious marketing noise dropped even if the sender domain matches.
MARKETING_SUBJECT_EXCLUDES = [
    r"\bunsubscribe\b",
    r"\b\d+% off\b",
    r"\bsale ends\b",
    r"\bdeals? (of the|for)\b",
    r"\brecommended for you\b",
    r"\bdon'?t miss\b",
]

# --------------------------------------------------------------------------- #
# Statement CSV import. Canonical field -> accepted header spellings (matched
# case-insensitively, whitespace-collapsed). The provider is auto-detected
# from the headers: a file with separate Debit/Credit columns is Capital One
# style; a single signed Amount column is Amex style. If your export differs,
# add the header here or pass --column-map on import-csv. An unrecognized
# REQUIRED column fails loudly with a found-vs-expected diff.
# --------------------------------------------------------------------------- #
CSV_COLUMN_MAP = {
    "date":        ["date", "trans date", "transaction date"],
    "post_date":   ["post date", "posting date", "posted date"],
    "description": ["description"],
    "amount":      ["amount"],
    "debit":       ["debit"],            # Capital One: charge amount
    "credit":      ["credit"],           # Capital One: payment/refund amount
    "card_member": ["card member", "cardmember"],
    "account":     ["account #", "account number", "card no.", "card no"],
    "extended":    ["extended details"],
    "appears_as":  ["appears on your statement as"],
    "address":     ["address"],
    "city":        ["city/town", "city"],
    "state":       ["state/province", "state"],
    "zip":         ["zip code", "zip"],
    "country":     ["country"],
    "reference":   ["reference"],
    "category":    ["category"],
}
CSV_REQUIRED = ["date", "description"]   # plus amount OR debit/credit, checked in code

# Amex CSVs today list charges as POSITIVE and credits negative; they have
# flipped this convention in the past. Set to -1 if your export is inverted.
# (Capital One's Debit/Credit columns are unambiguous and ignore this.)
AMEX_AMOUNT_SIGN = 1

# Rows whose description matches any of these are card PAYMENTS, not spend —
# skipped on import (they'd otherwise pollute every report and flag rule).
PAYMENT_DESCRIPTION_PATTERNS = [
    r"\bAUTOPAY\b.*\bPAYMENT\b",
    r"\bONLINE PAYMENT\b.*\bTHANK YOU\b",
    r"\bPAYMENT RECEIVED\b.*\bTHANK YOU\b",
    r"\bMOBILE PAYMENT\b.*\bTHANK YOU\b",
]

# --------------------------------------------------------------------------- #
# Merchant normalization. Prefixes are stripped from the front of the raw
# description (processor tags), then aliases collapse known variants to one
# canonical name. Add your own regulars here as you spot them in reports.
# --------------------------------------------------------------------------- #
MERCHANT_STRIP_PREFIXES = [
    "TST* ", "TST *", "SQ *", "SQ* ", "SP * ", "SP*", "PAYPAL *", "PP*",
    "GOOGLE *", "APLPAY ", "IC* ",
]
MERCHANT_ALIASES = {
    # regex (case-insensitive, matched against the stripped description) -> canonical
    r"^AMAZON( MAR?K(ET)?P(LACE)?)?\b.*": "AMAZON",
    r"^AMZN( MKTP)?\b.*": "AMAZON",
    r"^AMAZON PRIME\b.*": "AMAZON PRIME",
    r"^APPLE\.COM/BILL\b.*": "APPLE",
    r"^APPLE ?(STORE|ONLINE)?\b.*": "APPLE",
    r"^UBER\s*(\*|EATS)?.*": "UBER",
    r"^LYFT\b.*": "LYFT",
    r"^NETFLIX\b.*": "NETFLIX",
    r"^SPOTIFY\b.*": "SPOTIFY",
    r"^GITHUB\b.*": "GITHUB",
    r"^DROPBOX\b.*": "DROPBOX",
    r"^ADOBE\b.*": "ADOBE",
    r"^B ?& ?H PHOTO\b.*": "B&H PHOTO",
    r"^WHOLEFDS\b.*": "WHOLE FOODS",
    r"^WM SUPERCENTER\b.*": "WALMART",
    r"^TARGET\b.*": "TARGET",
}

# --------------------------------------------------------------------------- #
# Categorization. Amex's own Category column is kept as `category_amex`; the
# report `category` starts from it, then these regex overrides win (matched
# against the normalized merchant, first hit wins, case-insensitive).
# --------------------------------------------------------------------------- #
CATEGORY_RULES = [
    (r"\b(AMAZON|TARGET|WALMART|BEST ?BUY|EBAY|ETSY)\b", "Shopping"),
    (r"\b(UBER|LYFT|TAXI|METRO|MTA|PARKING)\b", "Transport"),
    (r"\b(DELTA|UNITED|AMERICAN AIR|SOUTHWEST|JETBLUE|ALASKA AIR)\b", "Travel - Air"),
    (r"\b(AIRBNB|VRBO|HOTEL|MARRIOTT|HILTON|HYATT)\b", "Travel - Lodging"),
    (r"\b(NETFLIX|SPOTIFY|HULU|HBO|MAX|DISNEY)\b", "Subscriptions - Media"),
    (r"\b(ADOBE|DROPBOX|GITHUB|GOOGLE|BACKBLAZE|FRAME\.?IO|VIMEO)\b",
     "Subscriptions - Software"),
    (r"\b(APPLE)\b", "Apple"),
    (r"\b(DOORDASH|GRUBHUB|SEAMLESS|POSTMATES|RESTAURANT|PIZZA|CAFE|COFFEE)\b",
     "Food & Dining"),
    (r"\b(WHOLE FOODS|TRADER JOE|SAFEWAY|KROGER|INSTACART|GROCERY)\b", "Groceries"),
    (r"\b(B&H PHOTO|ADORAMA|SWEETWATER)\b", "Production Gear"),
]
DEFAULT_CATEGORY = "Uncategorized"

# --------------------------------------------------------------------------- #
# Receipt <-> transaction matching. Weights sum to 1.0; a pair must clear
# MATCH_THRESHOLD to link. Date window is asymmetric: charges usually POST a
# few days after the receipt email, pre-auths occasionally land early.
# --------------------------------------------------------------------------- #
MATCH_THRESHOLD = 0.75
MATCH_AMBIGUITY_MARGIN = 0.05        # runner-up within this of winner -> AMBIGUOUS_MATCH
MATCH_DATE_BEFORE_DAYS = 3           # txn may be up to this many days BEFORE the email
MATCH_DATE_AFTER_DAYS = 5            # ... or this many days after
MATCH_AMOUNT_TOLERANCE_CENTS = 100   # near-match band: max(this, 1% of amount)
MATCH_AMOUNT_TOLERANCE_PCT = 0.01
MATCH_SPLIT_MAX_PARTS = 4            # Amazon split-shipment reconstruction cap

WEIGHT_AMOUNT_EXACT = 0.55
WEIGHT_AMOUNT_NEAR = 0.35
WEIGHT_AMOUNT_UNKNOWN = 0.15         # receipt has no parseable total
WEIGHT_DATE = 0.15
WEIGHT_MERCHANT = 0.30

# --------------------------------------------------------------------------- #
# Flag rules. Every threshold for R1-R6 / F1-F6 lives here.
# --------------------------------------------------------------------------- #
# R1 DUPLICATE_CHARGE: same merchant + identical cents within this many days.
DUP_CHARGE_WINDOW_DAYS = 3
# Merchants where identical same-week charges are normal (coffee, transit...).
DUP_EXEMPT_MERCHANTS = ["UBER", "LYFT", "MTA", "STARBUCKS"]

# R2 DUPLICATE_RECEIPT: same merchant + same total within this many days
# (order-id or body-hash matches flag regardless of dates).
DUP_RECEIPT_WINDOW_DAYS = 1

# R3 SUBSCRIPTION_DOUBLE_BILL: recurring = >=3 charges, median cadence in
# [27, 33] days, amount variance <= 5%; flagged when billed twice in a month.
SUBSCRIPTION_MIN_CHARGES = 3
SUBSCRIPTION_CADENCE_DAYS = (27, 33)
SUBSCRIPTION_AMOUNT_VARIANCE_PCT = 0.05

# R4 RECEIPT_NO_CHARGE: receipt with a total, unmatched after this many days.
RECEIPT_NO_CHARGE_GRACE_DAYS = 7

# R5 CHARGE_NO_RECEIPT: unmatched charge inside the scanned email range.
# info below the warn threshold; merchants here never expect email receipts.
CHARGE_NO_RECEIPT_WARN_CENTS = 10000     # $100
NO_RECEIPT_EXPECTED = [
    "MTA", "PARKING", "TOLL", "USPS", "ATM", "STARBUCKS", "DUNKIN",
]

# F1 UNRECOGNIZED_MERCHANT: first-ever merchant, no receipt, at least this much.
UNRECOGNIZED_MERCHANT_MIN_CENTS = 5000   # $50

# F2 AMOUNT_OUTLIER: needs this much history; amount > median + 4*MAD AND
# > 2x median (robust to one-off spikes).
OUTLIER_MIN_HISTORY = 3
OUTLIER_MAD_MULTIPLIER = 4.0
OUTLIER_MEDIAN_MULTIPLIER = 2.0

# F3 FOREIGN_ANOMALY: lone foreign txn with no other foreign activity within
# this window on either side.
FOREIGN_LONE_WINDOW_DAYS = 14

# F4 ROUND_NUMBER: exact multiples of $100 at/above $100, no receipt.
ROUND_NUMBER_UNIT_CENTS = 10000
ROUND_NUMBER_ALLOWLIST = ["IRS", "US TREASURY", "DMV", "RENT"]

# F5 MICRO_CHARGE_PROBE: card-testing pattern — tiny charge, unknown merchant.
MICRO_CHARGE_MAX_CENTS = 200             # $2

# F6 RAPID_FIRE: >= this many identical-amount same-merchant charges in one day.
RAPID_FIRE_MIN_COUNT = 3

# --------------------------------------------------------------------------- #
# Optional Claude passes (opt-in via --llm; requires ANTHROPIC_API_KEY and
# `pip install .[llm]`). Missing either -> rules-only, never an error.
# --------------------------------------------------------------------------- #
LLM_MODEL = "claude-sonnet-4-6"
LLM_RECEIPTS_PER_REQUEST = 5      # receipts batched per extraction call
LLM_FLAGS_PER_REQUEST = 8         # flags batched per second-opinion call
LLM_MAX_TOKENS = 2000
LLM_BODY_CHAR_CAP = 6000          # receipt body text truncated to this per item


def data_path(*parts: str) -> str:
    """Join under EXPENSE_DATA_DIR (reads the module var live so tests can
    redirect it after import)."""
    return os.path.join(EXPENSE_DATA_DIR, *parts)
