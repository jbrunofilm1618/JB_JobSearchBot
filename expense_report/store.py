"""Ledger persistence: transactions + receipts + flags in one ledger.json
(round-trippable, resumable) and the reviewable transactions.csv slice.

Same contract as grid_archive/manifest.py: every write is atomic
(tmp + os.replace) so a kill mid-run never corrupts the ledger.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List

import expense_config as config
from .models import Transaction, Receipt, Flag, TRANSACTION_COLUMNS


@dataclass
class Ledger:
    transactions: List[Transaction] = field(default_factory=list)
    receipts: List[Receipt] = field(default_factory=list)
    flags: List[Flag] = field(default_factory=list)

    def txn_by_id(self) -> Dict[str, Transaction]:
        return {t.txn_id: t for t in self.transactions}

    def receipt_by_id(self) -> Dict[str, Receipt]:
        return {r.receipt_id: r for r in self.receipts}

    def flag_by_id(self) -> Dict[str, Flag]:
        return {f.flag_id: f for f in self.flags}

    def active_transactions(self) -> List[Transaction]:
        """Transactions that count: everything not superseded by a CSV import."""
        return [t for t in self.transactions if t.active]


def ledger_json_path() -> str:
    return config.data_path(config.LEDGER_JSON)


def transactions_csv_path() -> str:
    return config.data_path(config.TRANSACTIONS_CSV)


def load_ledger() -> Ledger:
    path = ledger_json_path()
    if not os.path.exists(path):
        return Ledger()
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return Ledger(
        transactions=[Transaction.from_dict(d) for d in data.get("transactions", [])],
        receipts=[Receipt.from_dict(d) for d in data.get("receipts", [])],
        flags=[Flag.from_dict(d) for d in data.get("flags", [])],
    )


def save_ledger(ledger: Ledger) -> None:
    os.makedirs(config.EXPENSE_DATA_DIR, exist_ok=True)

    json_path = ledger_json_path()
    tmp = json_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({
            "transactions": [t.to_dict() for t in ledger.transactions],
            "receipts": [r.to_dict() for r in ledger.receipts],
            "flags": [f.to_dict() for f in ledger.flags],
        }, fh, indent=2, default=str)
    os.replace(tmp, json_path)

    csv_path = transactions_csv_path()
    tmp = csv_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TRANSACTION_COLUMNS)
        writer.writeheader()
        for t in sorted(ledger.transactions, key=lambda t: t.date, reverse=True):
            writer.writerow(t.report_row())
    os.replace(tmp, csv_path)
