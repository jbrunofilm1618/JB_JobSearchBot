# expense-report

Itemized expense reports from your own email and card statements, with
**redundancy** (duplicate charges, double-billed subscriptions) and
**possible-fraud** flagging. Everything runs locally on your machine; no
financial data ever leaves it unless you explicitly opt into the `--llm` pass.

How data flows:

```
iCloud + Gmail (IMAP, read-only)      Amex / Capital One
  receipt emails                        alert emails  ->  provisional txns
  itemized line items                                          |
        \                                                      v
         \                            CSV download (audit)  supersedes alerts
          \                           PDF statement (official) cross-verifies
           v                                                   |
        match  <----------------------------------------------+
           |
           v
        flags (12 rules)  ->  expense_report.html + report.csv/json
```

## Install

```bash
pip install -e .              # core: 100% stdlib at runtime
pip install -e .[expense]     # + pdfplumber (PDF verify) + anthropic (--llm)
```

## One-time setup

### 1. Email app-specific passwords (never your real passwords)

- **iCloud**: [account.apple.com](https://account.apple.com) → Sign-In and
  Security → App-Specific Passwords → generate one named `expense-report`.
- **Gmail**: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
  (requires 2-Step Verification).

Store them in the **macOS Keychain** (recommended — nothing in plaintext):

```bash
security add-generic-password -s expense-report -a you@icloud.com -w
security add-generic-password -s expense-report -a you@gmail.com -w
```

Then set just the addresses in `.env` (see `.env.example`):

```
ICLOUD_EMAIL=you@icloud.com
GMAIL_EMAIL=you@gmail.com
```

(The passwords may also go in `.env` as `ICLOUD_APP_PASSWORD` /
`GMAIL_APP_PASSWORD` — it's gitignored — but Keychain is better.)

The IMAP connection is strictly **read-only**: folders open read-only and
bodies are fetched with `BODY.PEEK`, so scanning can never mark, move, or
delete your mail. Passwords are revocable anytime at the pages above.

### 2. Card transaction alerts — the automatic feed

- **Amex**: Account Services → Alerts → purchase notifications, threshold $0,
  delivery = email.
- **Capital One**: your card → Alerts & Notifications → transaction alerts,
  delivery = email.

Point them at the Gmail address to keep your main inbox clean. The scanner
parses each alert into a provisional transaction the moment it lands — no
statement download needed day-to-day.

### 3. Monthly audit downloads

Download the CSV export (and optionally the PDF statement) from amex.com /
capitalone.com into the `statements/` folder. `import-csv` auto-discovers new
files (content-hashed, so nothing imports twice), reconciles them against the
alert feed, and supersedes the provisional entries — the CSV is the
audit-grade record. `import-pdf` then cross-verifies the CSV against the
official statement and flags anything present on only one side.

## Usage

```bash
expense-report run --since 2026-06-01           # full pipeline
# or step by step:
expense-report scan-email --since 2026-06-01 [--account icloud|gmail]
expense-report import-csv [statements/june.csv]
expense-report import-pdf [statements/june.pdf]
expense-report match
expense-report flags
expense-report report                            # -> expense_data/reports/
expense-report dismiss DUPLICATE_CHARGE:ab12...  # silence a reviewed flag
```

Open `expense_data/reports/expense_report.html` — flags first (severity
badges), month × category matrix, then itemized transactions with filters.

## What gets flagged

| Rule | Kind | Meaning |
|---|---|---|
| DUPLICATE_CHARGE | redundancy | same merchant + amount within 3 days |
| DUPLICATE_RECEIPT | redundancy | same order emailed twice |
| SUBSCRIPTION_DOUBLE_BILL | redundancy | monthly subscription billed twice in a month |
| RECEIPT_NO_CHARGE | reconciliation | receipt never hit any statement |
| CHARGE_NO_RECEIPT | reconciliation | charge with no receipt (inside scanned range) |
| AMBIGUOUS_MATCH | reconciliation | receipt↔charge link was a coin flip |
| STATEMENT_MISMATCH | reconciliation | CSV and official PDF disagree |
| UNRECOGNIZED_MERCHANT | fraud | first-ever merchant, no receipt, ≥ $50 |
| AMOUNT_OUTLIER | fraud | far above your history with that merchant |
| FOREIGN_ANOMALY | fraud | lone foreign charge, no trip around it |
| ROUND_NUMBER | fraud | exact-$100-multiple charge, no receipt |
| MICRO_CHARGE_PROBE | fraud | ≤ $2 charge at a never-seen merchant (card testing) |
| RAPID_FIRE | fraud | 3+ identical charges in one day |

Every threshold lives in `expense_config.py`. Flags regenerate on each run,
but ids are stable, so dismissals stick.

## Optional Claude passes (`--llm`)

With `pip install .[llm]` and `ANTHROPIC_API_KEY` set, `--llm` adds:
itemization of receipt emails the rules couldn't parse, and a second opinion
on warn/high flags (recorded next to each flag, never replacing it). On any
numeric disagreement the deterministic rules win. **This sends receipt text
and merchant/amount/date rows to the Anthropic API** — leave it off if that's
not acceptable. Without the key everything runs rules-only.

## Privacy model

- All data lives under gitignored `expense_data/` and `statements/` —
  transactions, cached receipt emails (`.eml`), reports. Never commit them.
- No bank credentials anywhere, ever: email uses revocable app-specific
  passwords; card data arrives via alert emails you configured and files you
  downloaded yourself.
- Logs carry merchant/date/amount only — no account numbers, no credentials.
- Keep this repository **private** — the code holds no secrets, but private
  is the right default for anything finance-adjacent.

## Testing

```bash
python -m pytest tests/test_expense_report.py -q
```

Fully offline: a fake IMAP server, fake Anthropic client, and fixture
emails/CSVs/statement text. No real credentials or financial data are used
anywhere in the suite.
