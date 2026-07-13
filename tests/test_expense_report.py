"""Offline tests for the expense_report package. No network, no real
credentials, no real financial data — fixtures only, in the style of
test_grid_archive.py (fakes + captured shapes).

Run: python -m pytest tests/test_expense_report.py -q
"""

from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import expense_config as config

# Redirect all data writes to a tempdir BEFORE importing the package modules,
# mirroring the cache redirect in the grid tests.
_TMP = tempfile.mkdtemp(prefix="expense-test-")
config.EXPENSE_DATA_DIR = _TMP
config.STATEMENTS_DIR = os.path.join(_TMP, "statements")

from expense_report.models import Transaction, Receipt, Flag, cents_to_dollars
from expense_report import store
from expense_report.normalize import (
    parse_cents, parse_iso_date, days_between, normalize_merchant,
)
from expense_report.card_csv import (
    build_column_map, detect_provider, parse_csv_rows, merge_transactions,
    supersede_alerts,
)
from expense_report.categorize import categorize


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

AMEX_CSV = """\
Date,Description,Card Member,Account #,Amount,Extended Details,Appears On Your Statement As,Address,City/Town,State/Province,Zip Code,Country,Reference,Category
07/01/2026,AMZN MKTP US*A12BC3,JONATHAN BRUNO,-71002,54.99,AMZN.COM/BILL,AMZN MKTP US*A12BC3,,SEATTLE,WA,98109,UNITED STATES,'320261830000001',Merchandise & Supplies-Internet Purchase
07/02/2026,NETFLIX.COM,JONATHAN BRUNO,-71002,15.49,NETFLIX.COM 866-579-7172,NETFLIX.COM,,LOS GATOS,CA,95032,UNITED STATES,'320261830000002',Entertainment-Media
07/03/2026,AUTOPAY PAYMENT - THANK YOU,JONATHAN BRUNO,-71002,-500.00,,,,,,,UNITED STATES,'320261830000003',
07/05/2026,TST* THE LITTLE CAFE OAKLAND CA,JONATHAN BRUNO,-71002,23.10,,TST* THE LITTLE CAFE,,OAKLAND,CA,94607,UNITED STATES,'320261830000004',Restaurant-Restaurant
07/06/2026,ROME MERCHANT SRL,JONATHAN BRUNO,-71002,110.25,FOREIGN SPEND AMOUNT: 95.00 EUR COMMISSION AMOUNT: 0.00,ROME MERCHANT SRL,,ROMA,,,ITALY,'320261830000005',Merchandise & Supplies
"""

MINIMAL_CSV = """\
Date,Description,Amount
07/01/2026,COFFEE SHOP,4.50
07/02/2026,BOOKSTORE,(12.00)
"""

CAPITALONE_CSV = """\
Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2026-07-02,2026-07-03,1234,UBER *TRIP HELP.UBER.COM,Other Travel,18.42,
2026-07-04,2026-07-05,1234,REFUND - TARGET,Merchandise,,25.00
"""


def parse_fixture(text, overrides=None):
    reader = csv.DictReader(io.StringIO(text))
    mapping = build_column_map(reader.fieldnames, overrides)
    provider = detect_provider(mapping)
    return parse_csv_rows(list(reader), mapping, provider), provider


# --------------------------------------------------------------------------- #
# normalize
# --------------------------------------------------------------------------- #

class TestNormalize(unittest.TestCase):
    def test_parse_cents(self):
        self.assertEqual(parse_cents("$1,234.56"), 123456)
        self.assertEqual(parse_cents("54.99"), 5499)
        self.assertEqual(parse_cents("-12.30"), -1230)
        self.assertEqual(parse_cents("(12.00)"), -1200)   # accounting negative
        self.assertEqual(parse_cents(15.49), 1549)
        self.assertEqual(parse_cents("Total: $89.10 due"), 8910)
        self.assertIsNone(parse_cents(""))
        self.assertIsNone(parse_cents("no numbers here"))
        self.assertIsNone(parse_cents(None))

    def test_parse_cents_no_float_drift(self):
        # The classic float trap: 19.99 * 100 = 1998.9999...
        self.assertEqual(parse_cents("19.99"), 1999)
        self.assertEqual(parse_cents(19.99), 1999)

    def test_parse_iso_date(self):
        self.assertEqual(parse_iso_date("07/01/2026"), "2026-07-01")
        self.assertEqual(parse_iso_date("2026-07-01"), "2026-07-01")
        self.assertEqual(parse_iso_date("Jul 1, 2026"), "2026-07-01")
        self.assertEqual(parse_iso_date("07/03", default_year=2026), "2026-07-03")
        self.assertEqual(parse_iso_date("07/03"), "")   # no year, no default
        self.assertEqual(parse_iso_date(""), "")

    def test_days_between(self):
        self.assertEqual(days_between("2026-07-01", "2026-07-04"), 3)
        self.assertEqual(days_between("2026-07-04", "2026-07-01"), -3)
        self.assertIsNone(days_between("", "2026-07-01"))

    def test_normalize_merchant(self):
        self.assertEqual(normalize_merchant("AMZN MKTP US*A12BC3"), "AMAZON")
        self.assertEqual(normalize_merchant("TST* THE LITTLE CAFE OAKLAND CA"),
                         "THE LITTLE CAFE")
        self.assertEqual(normalize_merchant("SQ *BLUE BOTTLE #1234"), "BLUE BOTTLE")
        self.assertEqual(normalize_merchant("NETFLIX.COM"), "NETFLIX")
        self.assertEqual(normalize_merchant("UBER *TRIP HELP.UBER.COM"), "UBER")
        self.assertEqual(normalize_merchant(""), "")


# --------------------------------------------------------------------------- #
# CSV import
# --------------------------------------------------------------------------- #

class TestCsvImport(unittest.TestCase):
    def test_amex_full_layout(self):
        txns, provider = parse_fixture(AMEX_CSV)
        self.assertEqual(provider, "amex")
        # 5 rows minus the AUTOPAY payment
        self.assertEqual(len(txns), 4)
        amazon = txns[0]
        self.assertEqual(amazon.date, "2026-07-01")
        self.assertEqual(amazon.amount_cents, 5499)
        self.assertEqual(amazon.merchant, "AMAZON")
        self.assertEqual(amazon.reference, "320261830000001")
        self.assertEqual(amazon.account_suffix, "71002")
        self.assertEqual(amazon.month, "2026-07")

    def test_payment_rows_skipped(self):
        txns, _ = parse_fixture(AMEX_CSV)
        self.assertFalse(any("AUTOPAY" in t.description for t in txns))

    def test_foreign_spend_extracted(self):
        txns, _ = parse_fixture(AMEX_CSV)
        rome = [t for t in txns if "ROME" in t.description][0]
        self.assertEqual(rome.foreign_amount, "95.00")
        self.assertEqual(rome.foreign_currency, "EUR")

    def test_minimal_layout_and_negative_parens(self):
        txns, provider = parse_fixture(MINIMAL_CSV)
        self.assertEqual(provider, "amex")
        self.assertEqual(len(txns), 2)
        self.assertEqual(txns[1].amount_cents, -1200)

    def test_capitalone_layout(self):
        txns, provider = parse_fixture(CAPITALONE_CSV)
        self.assertEqual(provider, "capitalone")
        self.assertEqual(len(txns), 2)
        uber, refund = txns
        self.assertEqual(uber.amount_cents, 1842)          # Debit -> charge
        self.assertEqual(uber.merchant, "UBER")
        self.assertEqual(refund.amount_cents, -2500)       # Credit -> negative
        self.assertEqual(uber.post_date, "2026-07-03")

    def test_missing_required_header_fails_loud(self):
        bad = "Foo,Bar\n1,2\n"
        reader = csv.DictReader(io.StringIO(bad))
        with self.assertRaises(SystemExit) as ctx:
            build_column_map(reader.fieldnames)
        msg = str(ctx.exception)
        self.assertIn("date", msg)
        self.assertIn("headers in file", msg)

    def test_column_map_override(self):
        odd = "Trans Date,Description,Amount\n07/01/2026,X,1.00\n"
        txns, _ = parse_fixture(odd)   # "trans date" is a built-in alias
        self.assertEqual(txns[0].date, "2026-07-01")
        odd2 = "When,Description,Amount\n07/01/2026,X,1.00\n"
        txns2, _ = parse_fixture(odd2, overrides=["date=When"])
        self.assertEqual(txns2[0].date, "2026-07-01")

    def test_reimport_is_idempotent(self):
        ledger = store.Ledger()
        txns, _ = parse_fixture(AMEX_CSV)
        self.assertEqual(merge_transactions(ledger, txns), 4)
        txns_again, _ = parse_fixture(AMEX_CSV)
        self.assertEqual(merge_transactions(ledger, txns_again), 0)
        self.assertEqual(len(ledger.transactions), 4)

    def test_amount_sign_flip(self):
        old = config.AMEX_AMOUNT_SIGN
        try:
            config.AMEX_AMOUNT_SIGN = -1
            txns, _ = parse_fixture(MINIMAL_CSV)
            self.assertEqual(txns[0].amount_cents, -450)
        finally:
            config.AMEX_AMOUNT_SIGN = old


class TestAlertSupersede(unittest.TestCase):
    def test_csv_supersedes_matching_alert(self):
        ledger = store.Ledger()
        alert = Transaction(provider="amex", source="alert", date="2026-06-30",
                            description="AMAZON.COM", merchant="AMAZON",
                            amount_cents=5499, matched_receipt_id="email:abc",
                            match_score=0.9, match_method="exact")
        ledger.transactions.append(alert)
        txns, _ = parse_fixture(AMEX_CSV)
        merge_transactions(ledger, txns)
        self.assertEqual(supersede_alerts(ledger), 1)
        self.assertTrue(alert.superseded_by.startswith("amex:"))
        self.assertFalse(alert.active)
        # receipt link transferred to the CSV txn
        csv_txn = ledger.txn_by_id()[alert.superseded_by]
        self.assertEqual(csv_txn.matched_receipt_id, "email:abc")
        # active_transactions excludes the superseded alert
        self.assertNotIn(alert, ledger.active_transactions())

    def test_different_provider_never_supersedes(self):
        ledger = store.Ledger()
        alert = Transaction(provider="capitalone", source="alert",
                            date="2026-07-01", merchant="AMAZON",
                            description="AMAZON", amount_cents=5499)
        ledger.transactions.append(alert)
        txns, _ = parse_fixture(AMEX_CSV)
        merge_transactions(ledger, txns)
        self.assertEqual(supersede_alerts(ledger), 0)
        self.assertTrue(alert.active)


# --------------------------------------------------------------------------- #
# categorize
# --------------------------------------------------------------------------- #

class TestCategorize(unittest.TestCase):
    def test_rule_override_wins(self):
        t = Transaction(merchant="NETFLIX", category_amex="Entertainment-Media",
                        date="2026-07-01", amount_cents=1549)
        self.assertEqual(categorize(t), "Subscriptions - Media")

    def test_falls_back_to_amex_category(self):
        t = Transaction(merchant="SOME ODD SHOP", category_amex="Merchandise",
                        date="2026-07-01", amount_cents=100)
        self.assertEqual(categorize(t), "Merchandise")

    def test_default_when_nothing_known(self):
        t = Transaction(merchant="???", date="2026-07-01", amount_cents=100)
        self.assertEqual(categorize(t), config.DEFAULT_CATEGORY)


# --------------------------------------------------------------------------- #
# store round-trip
# --------------------------------------------------------------------------- #

class TestStore(unittest.TestCase):
    def test_ledger_round_trip(self):
        ledger = store.Ledger()
        txns, _ = parse_fixture(AMEX_CSV)
        merge_transactions(ledger, txns)
        ledger.receipts.append(Receipt(message_id="<m1@amazon.com>",
                                       account="icloud", merchant="AMAZON",
                                       total_cents=5499,
                                       line_items=[{"description": "USB cable",
                                                    "amount_cents": 5499}]))
        ledger.flags.append(Flag(rule="DUPLICATE_CHARGE", kind="redundancy",
                                 severity="warn", txn_ids=["amex:x", "amex:y"]))
        store.save_ledger(ledger)

        loaded = store.load_ledger()
        self.assertEqual(len(loaded.transactions), 4)
        self.assertEqual(loaded.transactions[0].amount_cents, 5499)
        self.assertEqual(loaded.receipts[0].receipt_id,
                         ledger.receipts[0].receipt_id)
        self.assertEqual(loaded.receipts[0].items()[0].description, "USB cable")
        self.assertEqual(loaded.flags[0].flag_id, ledger.flags[0].flag_id)
        # CSV slice exists and has the header
        with open(store.transactions_csv_path()) as fh:
            header = fh.readline()
        self.assertIn("txn_id", header)

    def test_flag_id_stability(self):
        a = Flag(rule="DUPLICATE_CHARGE", txn_ids=["t2", "t1"])
        b = Flag(rule="DUPLICATE_CHARGE", txn_ids=["t1", "t2"])
        self.assertEqual(a.flag_id, b.flag_id)   # order-independent

    def test_cents_to_dollars(self):
        self.assertEqual(cents_to_dollars(5499), "54.99")
        self.assertEqual(cents_to_dollars(-1200), "-12.00")
        self.assertEqual(cents_to_dollars(5), "0.05")
        self.assertEqual(cents_to_dollars(None), "")


# --------------------------------------------------------------------------- #
# Email parsing
# --------------------------------------------------------------------------- #

from email.message import EmailMessage

from expense_report.email_parse import (
    html_to_text, body_text, extract_total, extract_order_id,
    extract_line_items, classify_header, parse_receipt,
)
from expense_report.card_alerts import parse_alert
from expense_report import imap_client


def make_email(from_addr, subject, body, html=False, message_id="<m1@x>",
               date="Wed, 01 Jul 2026 10:00:00 -0700"):
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["Subject"] = subject
    msg["Date"] = date
    msg["Message-ID"] = message_id
    if html:
        msg.set_content("see html")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    return msg


AMAZON_HTML = """
<html><head><style>.x{color:red}</style><script>evil()</script></head><body>
<h1>Order Confirmation</h1>
<p>Order #113-7654321-1234567</p>
<table>
<tr><td>Anker USB-C Cable 6ft</td><td>$15.99</td></tr>
<tr><td>2 x SanDisk 128GB SD Card</td><td>$39.00</td></tr>
<tr><td>Shipping &amp; Handling</td><td>$0.00</td></tr>
<tr><td>Tax</td><td>$4.95</td></tr>
<tr><td>Grand Total:</td><td>$59.94</td></tr>
</table></body></html>
"""

UBER_TEXT = """\
Thanks for riding, Jonathan.
Trip fare\t$14.50
Tip\t$3.00
Total\t$17.50
Payment: Amex ****1002
"""

AMEX_ALERT_TEXT = """\
A charge of $54.99 at AMZN MKTP US was approved on July 1, 2026.
Card ending in 71002.
If you don't recognize this charge, call the number on the back of your card.
"""

CAPONE_ALERT_TEXT = """\
Your card was used for $18.42 at UBER TRIP on 07/02/2026.
Card ending in 1234.
"""


class TestEmailParse(unittest.TestCase):
    def test_html_to_text_drops_script_and_keeps_cells_adjacent(self):
        text = html_to_text(AMAZON_HTML)
        self.assertNotIn("evil", text)
        self.assertNotIn("color:red", text)
        self.assertIn("Anker USB-C Cable 6ft\t$15.99", text)

    def test_total_priority_grand_total_beats_subtotal(self):
        text = "Subtotal\t$10.00\nTax\t$1.00\nGrand Total\t$11.00\n"
        self.assertEqual(extract_total(text), 1100)

    def test_order_ids(self):
        self.assertEqual(extract_order_id("Order #113-7654321-1234567"),
                         "113-7654321-1234567")
        self.assertEqual(extract_order_id("Confirmation number: ABC123XYZ"),
                         "ABC123XYZ")
        self.assertEqual(extract_order_id("no ids here"), "")

    def test_line_items_skip_labels_and_parse_qty(self):
        text = html_to_text(AMAZON_HTML)
        items = extract_line_items(text)
        descs = [i.description for i in items]
        self.assertIn("Anker USB-C Cable 6ft", descs)
        self.assertNotIn("Tax", " ".join(descs))
        self.assertNotIn("Grand Total", " ".join(descs))
        sd = [i for i in items if "SanDisk" in i.description][0]
        self.assertEqual(sd.quantity, 2)

    def test_parse_receipt_amazon_html(self):
        msg = make_email("Amazon.com <auto-confirm@amazon.com>",
                         "Your Amazon.com order", AMAZON_HTML, html=True)
        r = parse_receipt(msg, "icloud", "INBOX", "7")
        self.assertEqual(r.merchant, "AMAZON")
        self.assertEqual(r.total_cents, 5994)
        self.assertEqual(r.tax_cents, 495)
        self.assertEqual(r.order_id, "113-7654321-1234567")
        self.assertEqual(r.email_date, "2026-07-01")
        self.assertEqual(r.extraction_method, "rules")
        self.assertTrue(r.body_sha1)

    def test_parse_receipt_uber_plaintext(self):
        msg = make_email("Uber Receipts <noreply@uber.com>",
                         "Your Tuesday trip with Uber", UBER_TEXT)
        r = parse_receipt(msg, "gmail", "INBOX", "9")
        self.assertEqual(r.merchant, "UBER")
        self.assertEqual(r.total_cents, 1750)
        self.assertEqual(r.tip_cents, 300)

    def test_classify_header(self):
        self.assertEqual(classify_header("a@shipment-tracking.amazon.com",
                                         "Your package"), "receipt")
        self.assertEqual(classify_header("x@unknownshop.com",
                                         "Receipt for your purchase"), "receipt")
        self.assertEqual(classify_header(
            "American Express <alerts@americanexpress.com>",
            "Transaction alert: purchase approved"), "alert")
        # bank mail that is not an alert is dropped, not treated as a receipt
        self.assertEqual(classify_header("news@americanexpress.com",
                                         "Your statement is ready"), "")
        # marketing from a receipt domain is dropped
        self.assertEqual(classify_header("deals@amazon.com",
                                         "50% off — sale ends tonight"), "")
        self.assertEqual(classify_header("friend@example.com", "hey"), "")


class TestAlertParse(unittest.TestCase):
    def test_amex_alert(self):
        msg = make_email("American Express <alerts@americanexpress.com>",
                         "Purchase approved", AMEX_ALERT_TEXT)
        txn = parse_alert(msg)
        self.assertIsNotNone(txn)
        self.assertEqual(txn.provider, "amex")
        self.assertEqual(txn.source, "alert")
        self.assertEqual(txn.amount_cents, 5499)
        self.assertEqual(txn.merchant, "AMAZON")
        self.assertEqual(txn.date, "2026-07-01")
        self.assertEqual(txn.account_suffix, "71002")
        self.assertTrue(txn.txn_id.startswith("amex-alert:"))

    def test_capitalone_alert(self):
        msg = make_email("Capital One <no-reply@notification.capitalone.com>",
                         "New transaction alert", CAPONE_ALERT_TEXT)
        txn = parse_alert(msg)
        self.assertIsNotNone(txn)
        self.assertEqual(txn.provider, "capitalone")
        self.assertEqual(txn.amount_cents, 1842)
        self.assertEqual(txn.merchant, "UBER")
        self.assertEqual(txn.date, "2026-07-02")

    def test_unparseable_alert_returns_none(self):
        msg = make_email("alerts@americanexpress.com", "Transaction alert",
                         "Something changed on your account.")
        self.assertIsNone(parse_alert(msg))

    def test_alert_rescan_idempotent_via_message_id(self):
        msg = make_email("alerts@americanexpress.com", "Purchase approved",
                         AMEX_ALERT_TEXT, message_id="<alert1@amex>")
        a, b = parse_alert(msg), parse_alert(msg)
        self.assertEqual(a.txn_id, b.txn_id)


# --------------------------------------------------------------------------- #
# IMAP scan with a fake server
# --------------------------------------------------------------------------- #

class FakeIMAP:
    """Dynamic imaplib stand-in: holds {uid: EmailMessage}, answers SELECT/
    STATUS/UID SEARCH/UID FETCH, and records every command for assertions."""

    def __init__(self, messages, uidvalidity="7"):
        self.messages = {uid: msg.as_bytes() for uid, msg in messages.items()}
        self.uidvalidity = uidvalidity
        self.calls = []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def status(self, folder, what):
        self.calls.append(("status", folder, what))
        return "OK", [f'"{folder}" (UIDVALIDITY {self.uidvalidity})'.encode()]

    def uid(self, command, *args):
        self.calls.append(("uid", command) + args)
        if command == "SEARCH":
            uids = " ".join(sorted(self.messages, key=int))
            return "OK", [uids.encode()]
        if command == "FETCH":
            uid_list, spec = args[0].split(","), args[1]
            out = []
            for uid in uid_list:
                raw = self.messages.get(uid)
                if raw is None:
                    continue
                if "HEADER.FIELDS" in spec:
                    msg = raw.split(b"\n\n", 1)[0] + b"\n\n"
                else:
                    msg = raw
                out.append((f"{uid} (UID {uid} BODY[] {{{len(msg)}}}".encode(), msg))
                out.append(b")")
            return "OK", out
        return "NO", []

    def login(self, user, password):
        self.calls.append(("login", user, "<redacted>"))
        return "OK", [b"Logged in"]

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", []


class TestImapScan(unittest.TestCase):
    def setUp(self):
        # The scan index persists on disk by design; isolate each test.
        for name in ("scan_index.json", "scan_range.json"):
            path = config.data_path(name)
            if os.path.exists(path):
                os.remove(path)

    def _messages(self):
        return {
            "1": make_email("Amazon.com <auto-confirm@amazon.com>",
                            "Your Amazon.com order", AMAZON_HTML, html=True,
                            message_id="<r1@amazon>"),
            "2": make_email("alerts@americanexpress.com", "Purchase approved",
                            AMEX_ALERT_TEXT, message_id="<a1@amex>"),
            "3": make_email("friend@example.com", "lunch?", "hi!",
                            message_id="<p1@x>"),
        }

    def test_scan_folder_end_to_end(self):
        fake = FakeIMAP(self._messages())
        ledger = store.Ledger()
        r, a = imap_client.scan_folder(fake, "icloud", "INBOX", "2026-07-01",
                                       "2026-07-10", None, True, ledger)
        self.assertEqual((r, a), (1, 1))
        self.assertEqual(ledger.receipts[0].merchant, "AMAZON")
        self.assertEqual(ledger.transactions[0].source, "alert")
        # personal mail never got a body fetch: exactly 2 FETCH calls
        # (headers for all, bodies for the 2 interesting ones)
        fetches = [c for c in fake.calls if c[1] == "FETCH"]
        self.assertEqual(len(fetches), 2)
        self.assertNotIn("3", fetches[1][2].split(","))
        # cached .eml exists
        self.assertTrue(os.path.exists(ledger.receipts[0].body_cache_path))

    def test_scan_is_readonly_and_peek_only(self):
        fake = FakeIMAP(self._messages())
        imap_client.scan_folder(fake, "icloud", "INBOX", None, None, None,
                                True, store.Ledger())
        select = [c for c in fake.calls if c[0] == "select"][0]
        self.assertTrue(select[2])                      # readonly=True
        for call in fake.calls:
            if call[1] == "FETCH":
                self.assertIn("PEEK", call[3])          # never plain BODY[]

    def test_search_dates_use_english_months(self):
        fake = FakeIMAP(self._messages())
        imap_client.scan_folder(fake, "icloud", "INBOX", "2026-07-01",
                                "2026-07-10", None, True, store.Ledger())
        search = [c for c in fake.calls if c[1] == "SEARCH"][0]
        self.assertIn("01-Jul-2026", search)
        self.assertIn("11-Jul-2026", search)            # until+1: BEFORE is exclusive

    def test_rescan_fetches_nothing(self):
        msgs = self._messages()
        ledger = store.Ledger()
        imap_client.scan_folder(FakeIMAP(msgs), "icloud", "INBOX",
                                None, None, None, True, ledger)
        fake2 = FakeIMAP(msgs)
        r, a = imap_client.scan_folder(fake2, "icloud", "INBOX",
                                       None, None, None, True, ledger)
        self.assertEqual((r, a), (0, 0))
        self.assertEqual([c for c in fake2.calls if c[1] == "FETCH"], [])

    def test_uidvalidity_change_voids_index_but_stays_idempotent(self):
        msgs = self._messages()
        ledger = store.Ledger()
        imap_client.scan_folder(FakeIMAP(msgs), "icloud", "INBOX",
                                None, None, None, True, ledger)
        fake2 = FakeIMAP(msgs, uidvalidity="99")        # server renumbered
        r, a = imap_client.scan_folder(fake2, "icloud", "INBOX",
                                       None, None, None, True, ledger)
        self.assertEqual((r, a), (0, 0))                # dedup by message-id
        self.assertEqual(len(ledger.receipts), 1)

    def test_imap_date_and_folder_quoting(self):
        self.assertEqual(imap_client.imap_date("2026-01-05"), "05-Jan-2026")
        self.assertEqual(imap_client.encode_folder("INBOX"), '"INBOX"')
        self.assertEqual(imap_client.encode_folder("My Receipts"),
                         '"My Receipts"')


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

from expense_report.match import match_ledger, score_pair, merchant_similarity


def T(**kw):
    base = dict(provider="amex", source="csv", date="2026-07-03",
                merchant="AMAZON", description="AMZN MKTP US*A12BC3",
                amount_cents=5994)
    base.update(kw)
    return Transaction(**base)


def R(**kw):
    base = dict(message_id=f"<{kw.get('subject', 'r')}-{len(kw)}@x>",
                account="icloud", email_date="2026-07-01", merchant="AMAZON",
                from_domain="amazon.com", subject="Your order",
                total_cents=5994)
    base.update(kw)
    return Receipt(**base)


class TestMatching(unittest.TestCase):
    def test_exact_match_links(self):
        ledger = store.Ledger()
        t, r = T(), R(message_id="<m1@x>")
        ledger.transactions, ledger.receipts = [t], [r]
        match_ledger(ledger)
        self.assertEqual(t.matched_receipt_id, r.receipt_id)
        self.assertEqual(t.match_method, "exact")
        self.assertEqual(r.matched_txn_ids, [t.txn_id])

    def test_amount_mismatch_never_matches(self):
        ledger = store.Ledger()
        t, r = T(amount_cents=9999), R(message_id="<m2@x>")
        ledger.transactions, ledger.receipts = [t], [r]
        match_ledger(ledger)
        self.assertEqual(t.matched_receipt_id, "")

    def test_near_amount_fuzzy_match(self):
        # $0.87 off on a $59.94 receipt: within max($1, 1%)
        t, r = T(amount_cents=6081), R(message_id="<m3@x>")
        s = score_pair(r, t)
        self.assertIsNotNone(s)
        self.assertGreaterEqual(s, config.MATCH_THRESHOLD)
        ledger = store.Ledger()
        ledger.transactions, ledger.receipts = [t], [r]
        match_ledger(ledger)
        self.assertEqual(t.match_method, "fuzzy")

    def test_date_window_edges(self):
        r = R(message_id="<m4@x>", email_date="2026-07-10")
        inside_late = T(date="2026-07-15")     # +5 days: allowed
        outside = T(date="2026-07-16")         # +6 days: not
        inside_early = T(date="2026-07-07")    # -3 days: allowed (pre-auth)
        too_early = T(date="2026-07-06")
        self.assertIsNotNone(score_pair(r, inside_late))
        self.assertIsNone(score_pair(r, outside))
        self.assertIsNotNone(score_pair(r, inside_early))
        self.assertIsNone(score_pair(r, too_early))

    def test_greedy_prefers_best_and_flags_ambiguity(self):
        # Two same-amount same-merchant txns a day apart -> whichever wins,
        # the runner-up is within the margin -> ambiguous reported.
        ledger = store.Ledger()
        t1, t2 = T(date="2026-07-02"), T(date="2026-07-03",
                                         description="AMZN MKTP US*ZZZ")
        r = R(message_id="<m5@x>")
        ledger.transactions, ledger.receipts = [t1, t2], [r]
        diag = match_ledger(ledger)
        self.assertEqual(len(diag["ambiguous"]), 1)
        self.assertEqual(t1.matched_receipt_id, r.receipt_id)   # closer date wins
        self.assertEqual(t2.matched_receipt_id, "")

    def test_split_shipment_reconstruction(self):
        ledger = store.Ledger()
        parts = [T(amount_cents=1999, description="AMZN A"),
                 T(amount_cents=3995, description="AMZN B", date="2026-07-04")]
        r = R(message_id="<m6@x>", total_cents=5994)
        ledger.transactions, ledger.receipts = parts, [r]
        match_ledger(ledger)
        self.assertEqual(r.matched_txn_ids,
                         [parts[0].txn_id, parts[1].txn_id])
        self.assertTrue(all(t.match_method == "split" for t in parts))

    def test_refund_pairs_with_refund_receipt_only(self):
        refund_txn = T(amount_cents=-2500, description="REFUND TARGET",
                       merchant="TARGET")
        normal_receipt = R(message_id="<m7@x>", merchant="TARGET",
                           total_cents=2500, from_domain="target.com")
        refund_receipt = R(message_id="<m8@x>", merchant="TARGET",
                           total_cents=2500, from_domain="target.com",
                           subject="Your refund is on its way")
        self.assertIsNone(score_pair(normal_receipt, refund_txn))
        self.assertIsNotNone(score_pair(refund_receipt, refund_txn))

    def test_superseded_alerts_excluded(self):
        ledger = store.Ledger()
        alert = T(source="alert", superseded_by="amex:zzz")
        r = R(message_id="<m9@x>")
        ledger.transactions, ledger.receipts = [alert], [r]
        match_ledger(ledger)
        self.assertEqual(alert.matched_receipt_id, "")

    def test_rematch_clears_links(self):
        ledger = store.Ledger()
        t, r = T(), R(message_id="<m10@x>")
        ledger.transactions, ledger.receipts = [t], [r]
        match_ledger(ledger)
        t.merchant = "SOMETHING ELSE ENTIRELY"
        r2 = Receipt.from_dict(r.to_dict())
        match_ledger(ledger, rematch=True)
        # still matches on amount+date even with weak merchant? score drops:
        # amount 0.55 + date ~0.11 + merchant ~0 = 0.66 < 0.75 -> unlinked
        self.assertEqual(t.matched_receipt_id, "")
        self.assertEqual(r.matched_txn_ids, [])
        del r2

    def test_merchant_similarity_domain_hint(self):
        r = R(message_id="<m11@x>", merchant="UBER RECEIPTS",
              from_domain="uber.com")
        t = T(merchant="UBER", description="UBER *TRIP HELP.UBER.COM")
        self.assertEqual(merchant_similarity(r, t), 1.0)


if __name__ == "__main__":
    unittest.main()
