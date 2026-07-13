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


# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #

from expense_report import flags as flagmod


class TestFlagRules(unittest.TestCase):
    def test_r1_duplicate_charge(self):
        a = T(date="2026-07-03", description="X1")
        b = T(date="2026-07-04", description="X2")
        out = flagmod.rule_duplicate_charge([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].rule, "DUPLICATE_CHARGE")
        self.assertEqual(out[0].severity, "warn")
        # same-day duplicates are high severity
        c = T(date="2026-07-03", description="X3")
        out2 = flagmod.rule_duplicate_charge([a, c])
        self.assertEqual(out2[0].severity, "high")

    def test_r1_negative_far_apart_or_exempt(self):
        a, b = T(date="2026-07-01"), T(date="2026-07-20", description="Y")
        self.assertEqual(flagmod.rule_duplicate_charge([a, b]), [])
        u1 = T(merchant="UBER", date="2026-07-01", amount_cents=1500)
        u2 = T(merchant="UBER", date="2026-07-01", amount_cents=1500,
               description="U2")
        self.assertEqual(flagmod.rule_duplicate_charge([u1, u2]), [])

    def test_r2_duplicate_receipt_by_order_id(self):
        a = R(message_id="<d1@x>", order_id="113-1", email_date="2026-07-01")
        b = R(message_id="<d2@x>", order_id="113-1", email_date="2026-07-02")
        out = flagmod.rule_duplicate_receipt([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].rule, "DUPLICATE_RECEIPT")

    def test_r2_negative_different_orders(self):
        a = R(message_id="<d3@x>", order_id="113-1")
        b = R(message_id="<d4@x>", order_id="113-2")
        self.assertEqual(flagmod.rule_duplicate_receipt([a, b]), [])

    def test_r3_subscription_double_bill(self):
        txns = [T(merchant="NETFLIX", amount_cents=1549, date=d,
                  description=f"N{i}")
                for i, d in enumerate(
                    ["2026-03-05", "2026-04-05", "2026-05-05",
                     "2026-06-05", "2026-06-20"])]
        out = flagmod.rule_subscription_double_bill(txns)
        self.assertEqual(len(out), 1)
        self.assertIn("2026-06", out[0].detail)
        self.assertEqual(out[0].severity, "high")

    def test_r3_negative_regular_monthly(self):
        txns = [T(merchant="NETFLIX", amount_cents=1549, date=d,
                  description=f"N{i}")
                for i, d in enumerate(
                    ["2026-04-05", "2026-05-05", "2026-06-05"])]
        self.assertEqual(flagmod.rule_subscription_double_bill(txns), [])

    def test_r4_receipt_no_charge(self):
        r = R(message_id="<n1@x>", email_date="2026-07-01")
        out = flagmod.rule_receipt_no_charge([r], [], today="2026-07-13")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].rule, "RECEIPT_NO_CHARGE")

    def test_r4_negative_within_grace_or_matched(self):
        fresh = R(message_id="<n2@x>", email_date="2026-07-10")
        self.assertEqual(
            flagmod.rule_receipt_no_charge([fresh], [], today="2026-07-13"), [])
        matched = R(message_id="<n3@x>", email_date="2026-07-01")
        matched.matched_txn_ids = ["amex:x"]
        self.assertEqual(
            flagmod.rule_receipt_no_charge([matched], [], today="2026-07-13"), [])

    def test_r5_charge_no_receipt_bounded_to_scan_range(self):
        inside = T(date="2026-07-05", amount_cents=15000)
        outside = T(date="2026-05-01", amount_cents=15000, description="OLD")
        out = flagmod.rule_charge_no_receipt(
            [inside, outside], ("2026-07-01", "2026-07-10"))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].txn_ids, [inside.txn_id])
        self.assertEqual(out[0].severity, "warn")   # >= $100
        # no scan range recorded -> rule stays silent (no false-positive storm)
        self.assertEqual(flagmod.rule_charge_no_receipt([inside], ("", "")), [])

    def test_r5_negative_expected_merchants(self):
        toll = T(merchant="PARKING GARAGE", date="2026-07-05")
        self.assertEqual(
            flagmod.rule_charge_no_receipt([toll], ("2026-07-01", "2026-07-10")),
            [])

    def test_f1_unrecognized_merchant(self):
        known = [T(merchant="AMAZON", date="2026-06-01", description=f"A{i}")
                 for i in range(2)]
        new = T(merchant="SHADY VENDOR LLC", date="2026-07-05",
                amount_cents=9900)
        out = flagmod.rule_unrecognized_merchant(known + [new])
        rules = [(f.rule, f.txn_ids) for f in out]
        self.assertIn(("UNRECOGNIZED_MERCHANT", [new.txn_id]), rules)

    def test_f1_negative_small_or_receipted(self):
        small = T(merchant="NEW SHOP", amount_cents=1200, date="2026-07-05")
        receipted = T(merchant="OTHER NEW", amount_cents=9900,
                      date="2026-07-05", matched_receipt_id="email:x")
        out = flagmod.rule_unrecognized_merchant([small, receipted])
        self.assertEqual(out, [])

    def test_f2_amount_outlier(self):
        history = [T(merchant="CAFE", amount_cents=c, date=f"2026-06-{d:02d}",
                     description=f"C{d}")
                   for d, c in [(1, 1200), (5, 1300), (10, 1250)]]
        spike = T(merchant="CAFE", amount_cents=48000, date="2026-07-01")
        out = flagmod.rule_amount_outlier(history + [spike])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].txn_ids, [spike.txn_id])

    def test_f2_negative_needs_history_and_magnitude(self):
        few = [T(merchant="CAFE", amount_cents=1200, date="2026-06-01"),
               T(merchant="CAFE", amount_cents=48000, date="2026-07-01",
                 description="C2")]
        self.assertEqual(flagmod.rule_amount_outlier(few), [])
        normal = [T(merchant="CAFE", amount_cents=c, date=f"2026-06-{d:02d}",
                    description=f"C{d}")
                  for d, c in [(1, 1200), (5, 1300), (10, 1250), (15, 1400)]]
        self.assertEqual(flagmod.rule_amount_outlier(normal), [])

    def test_f3_foreign_anomaly(self):
        lone = T(merchant="ROME SHOP", date="2026-07-01",
                 foreign_amount="95.00", foreign_currency="EUR")
        out = flagmod.rule_foreign_anomaly([lone, T(description="D")])
        self.assertEqual(len(out), 1)

    def test_f3_negative_travel_cluster(self):
        trip = [T(merchant=f"ROME {i}", date=f"2026-07-{d:02d}",
                  foreign_amount="10.00", foreign_currency="EUR",
                  description=f"R{i}")
                for i, d in enumerate([1, 3, 5])]
        self.assertEqual(flagmod.rule_foreign_anomaly(trip), [])

    def test_f4_round_number(self):
        t = T(merchant="GIFTCARDS4U", amount_cents=30000, date="2026-07-01")
        out = flagmod.rule_round_number([t])
        self.assertEqual(len(out), 1)

    def test_f4_negative_allowlisted_or_receipted(self):
        irs = T(merchant="IRS PAYMENT", amount_cents=30000, date="2026-07-01")
        receipted = T(merchant="SHOP", amount_cents=30000, date="2026-07-01",
                      matched_receipt_id="email:x", description="S")
        self.assertEqual(flagmod.rule_round_number([irs, receipted]), [])

    def test_f5_micro_charge_probe(self):
        probe = T(merchant="WEIRD WEB SVC", amount_cents=100, date="2026-07-01")
        out = flagmod.rule_micro_charge_probe([probe])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "high")

    def test_f5_negative_known_merchant(self):
        prior = T(merchant="APPLE", amount_cents=99, date="2026-06-01")
        again = T(merchant="APPLE", amount_cents=99, date="2026-07-01",
                  description="A2")
        out = flagmod.rule_micro_charge_probe([prior, again])
        self.assertEqual(len(out), 1)          # only the FIRST is flagged
        self.assertEqual(out[0].txn_ids, [prior.txn_id])

    def test_f6_rapid_fire(self):
        hits = [T(merchant="SHOP", amount_cents=4999, date="2026-07-01",
                  description=f"S{i}") for i in range(3)]
        out = flagmod.rule_rapid_fire(hits)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0].txn_ids), 3)

    def test_f6_negative_two_is_fine(self):
        hits = [T(merchant="SHOP", amount_cents=4999, date="2026-07-01",
                  description=f"S{i}") for i in range(2)]
        self.assertEqual(flagmod.rule_rapid_fire(hits), [])

    def test_generate_flags_preserves_dismissals(self):
        ledger = store.Ledger()
        a = T(date="2026-07-03", description="X1")
        b = T(date="2026-07-04", description="X2")
        ledger.transactions = [a, b]
        ledger.flags = flagmod.generate_flags(ledger, today="2026-07-13")
        dup = [f for f in ledger.flags if f.rule == "DUPLICATE_CHARGE"][0]
        dup.status = "dismissed"
        regenerated = flagmod.generate_flags(ledger, today="2026-07-13")
        dup2 = [f for f in regenerated if f.rule == "DUPLICATE_CHARGE"][0]
        self.assertEqual(dup2.flag_id, dup.flag_id)
        self.assertEqual(dup2.status, "dismissed")


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #

from expense_report.report import render_html, run_report


class TestReport(unittest.TestCase):
    def _ledger(self):
        ledger = store.Ledger()
        t = T()
        r = R(message_id="<rep1@x>",
              line_items=[{"description": "USB <cable>", "quantity": 2,
                           "amount_cents": 2997}])
        t.matched_receipt_id = r.receipt_id
        r.matched_txn_ids = [t.txn_id]
        ledger.transactions = [t, T(date="2026-07-04", merchant="NETFLIX",
                                    amount_cents=1549, description="N")]
        ledger.receipts = [r]
        ledger.flags = [Flag(rule="DUPLICATE_CHARGE", kind="redundancy",
                             severity="warn", txn_ids=[t.txn_id],
                             detail="test <detail>")]
        return ledger

    def test_render_html_content(self):
        page = render_html(self._ledger())
        self.assertIn("<!doctype html>", page)
        self.assertIn("DUPLICATE_CHARGE", page)
        self.assertIn("AMAZON", page)
        self.assertIn("$59.94", page)
        self.assertIn("USB &lt;cable&gt;", page)          # escaped
        self.assertIn("test &lt;detail&gt;", page)
        self.assertIn('data-month="2026-07"', page)
        self.assertIn("expense-report dismiss", page)     # dismissal hint
        self.assertNotIn("<cable>", page)                 # nothing unescaped

    def test_dismissed_flags_hidden(self):
        ledger = self._ledger()
        ledger.flags[0].status = "dismissed"
        page = render_html(ledger)
        self.assertNotIn("DUPLICATE_CHARGE", page)
        self.assertIn("Nothing flagged", page)

    def test_superseded_alerts_excluded_from_report(self):
        ledger = self._ledger()
        ledger.transactions.append(T(source="alert", superseded_by="amex:x",
                                     merchant="GHOST", description="G"))
        page = render_html(ledger)
        self.assertNotIn("GHOST", page)

    def test_run_report_writes_files(self):
        ledger = self._ledger()
        store.save_ledger(ledger)
        run_report()
        base = config.data_path(config.REPORTS_DIRNAME)
        for name in ("expense_report.html", "report.json", "report.csv"):
            self.assertTrue(os.path.exists(os.path.join(base, name)), name)
        with open(os.path.join(base, "report.csv")) as fh:
            content = fh.read()
        self.assertIn("DUPLICATE_CHARGE", content)
        self.assertIn("AMAZON", content)


# --------------------------------------------------------------------------- #
# PDF cross-verification (pure text parsing — no pdfplumber needed)
# --------------------------------------------------------------------------- #

from expense_report.card_pdf import parse_statement_text, cross_verify

STATEMENT_TEXT = """\
AMERICAN EXPRESS   Blue Cash Preferred
JONATHAN BRUNO                          Closing Date 07/10/26
Account Ending 7-71002

Payments and Credits
07/01/26   MOBILE PAYMENT - THANK YOU              -500.00

New Charges
07/01/26*  AMZN MKTP US*A12BC3 AMZN.COM/BILL        54.99
07/02/26   NETFLIX.COM LOS GATOS CA                 15.49
07/05/26   TST* THE LITTLE CAFE OAKLAND CA          23.10

Fees
Total New Charges                                  $93.58
"""


class TestPdfCrossVerify(unittest.TestCase):
    def test_parse_statement_text(self):
        st = parse_statement_text(STATEMENT_TEXT)
        self.assertEqual(st.period_end, "2026-07-10")
        self.assertEqual(st.total_new_charges, 9358)
        self.assertEqual(len(st.lines), 3)          # payment row skipped
        self.assertEqual(st.lines[0]["date"], "2026-07-01")
        self.assertEqual(st.lines[0]["amount_cents"], 5499)

    def test_cross_verify_marks_verified(self):
        ledger = store.Ledger()
        txns, _ = parse_fixture(AMEX_CSV)
        merge_transactions(ledger, txns)
        st = parse_statement_text(STATEMENT_TEXT)
        verified, new_flags = cross_verify(ledger, st, "july.pdf")
        self.assertEqual(verified, 3)
        by_m = {t.merchant: t for t in ledger.transactions}
        self.assertTrue(by_m["AMAZON"].verified_in_pdf)
        self.assertTrue(by_m["NETFLIX"].verified_in_pdf)
        # The CSV's ROME txn (07/06, inside the window) is not on this PDF
        mism = [f for f in new_flags if f.rule == "STATEMENT_MISMATCH"]
        self.assertTrue(any("ROME" in f.detail for f in mism))
        # ...and the CSV sum for the window disagrees with the PDF total
        self.assertTrue(any("total new charges" in f.detail.lower()
                            for f in mism))

    def test_pdf_only_charge_flagged(self):
        ledger = store.Ledger()      # empty CSV side
        st = parse_statement_text(STATEMENT_TEXT)
        verified, new_flags = cross_verify(ledger, st, "july.pdf")
        self.assertEqual(verified, 0)
        self.assertEqual(
            len([f for f in new_flags if "NOT in the imported CSV" in f.detail]),
            3)

    def test_unparseable_text_yields_empty_statement(self):
        st = parse_statement_text("nothing statement-like here\n1234\n")
        self.assertEqual(st.lines, [])
        self.assertIsNone(st.total_new_charges)


# --------------------------------------------------------------------------- #
# Optional LLM passes (FakeAnthropic — no network, no key)
# --------------------------------------------------------------------------- #

import json as jsonlib
from types import SimpleNamespace

from expense_report import llm


class FakeAnthropic:
    """Returns queued reply strings; records prompts."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        text = self._replies.pop(0) if self._replies else "[]"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class TestLlmPasses(unittest.TestCase):
    def _receipt_with_cached_body(self, subject="Your order",
                                  body="Thanks! We charged you."):
        msg = make_email("orders@somewhere.com", subject, body,
                         message_id=f"<llm-{subject}@x>")
        raw = msg.as_bytes()
        path = os.path.join(_TMP, f"llm-{abs(hash(subject))}.eml")
        with open(path, "wb") as fh:
            fh.write(raw)
        r = Receipt(message_id=str(msg["Message-ID"]), account="icloud",
                    email_date="2026-07-01", subject=subject,
                    from_addr="orders@somewhere.com",
                    extraction_method="none", body_cache_path=path)
        return r

    def test_extraction_fills_unparsed_receipt(self):
        r = self._receipt_with_cached_body()
        reply = jsonlib.dumps([{
            "email": 1, "merchant": "Somewhere Shop", "order_id": "SW-123",
            "total": "42.00", "tax": "3.50",
            "line_items": [{"description": "Widget", "quantity": 2,
                            "amount": "42.00"}],
        }])
        client = FakeAnthropic([reply])
        llm._extract_batch(client, [r])
        self.assertEqual(r.total_cents, 4200)
        self.assertEqual(r.order_id, "SW-123")
        self.assertEqual(r.tax_cents, 350)
        self.assertEqual(r.line_items[0]["description"], "Widget")
        self.assertEqual(r.extraction_method, "llm")

    def test_rules_total_wins_on_disagreement(self):
        r = self._receipt_with_cached_body(subject="rules-won")
        r.total_cents = 5000
        r.extraction_method = "rules"
        reply = jsonlib.dumps([{"email": 1, "merchant": "X", "total": "49.00"}])
        llm._extract_batch(FakeAnthropic([reply]), [r])
        self.assertEqual(r.total_cents, 5000)                 # unchanged
        self.assertIn("disagreed", r.extraction_note)

    def test_garbage_reply_is_survivable(self):
        r = self._receipt_with_cached_body(subject="garbage")
        llm._extract_batch(FakeAnthropic(["I cannot help with that."]), [r])
        self.assertIsNone(r.total_cents)
        self.assertEqual(r.extraction_method, "none")         # still LLM-eligible

    def test_flag_review_sets_verdict(self):
        ledger = store.Ledger()
        t = T()
        ledger.transactions = [t]
        f = Flag(rule="DUPLICATE_CHARGE", kind="redundancy", severity="warn",
                 txn_ids=[t.txn_id], detail="dup?")
        reply = jsonlib.dumps([{"flag": 1, "concerning": False,
                                "reason": "looks like a normal repurchase"}])
        client = FakeAnthropic([reply])
        llm._review_batch(client, [f], ledger)
        self.assertTrue(f.llm_reviewed)
        self.assertFalse(f.llm_agrees)
        self.assertIn("repurchase", f.llm_reason)
        # merchant history table made it into the prompt
        self.assertIn("History with AMAZON", client.prompts[0])

    def test_no_key_degrades_to_noop(self):
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertIsNone(llm._client())
            llm.run_llm_extraction()      # must not raise
            llm.run_llm_flag_review()     # must not raise
        finally:
            if old:
                os.environ["ANTHROPIC_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
