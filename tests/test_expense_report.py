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


if __name__ == "__main__":
    unittest.main()
