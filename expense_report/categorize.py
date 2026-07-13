"""Category assignment: keep Amex's own Category column as `category_amex`,
then let the CATEGORY_RULES regex table (matched against the normalized
merchant) override. First hit wins; fall back to Amex's, then to Uncategorized."""

from __future__ import annotations

import re

import expense_config as config
from .models import Transaction


def categorize(txn: Transaction) -> str:
    for pattern, category in config.CATEGORY_RULES:
        if re.search(pattern, txn.merchant, re.IGNORECASE):
            return category
    return txn.category_amex or config.DEFAULT_CATEGORY


def apply_categories(transactions) -> None:
    for txn in transactions:
        txn.category = categorize(txn)
