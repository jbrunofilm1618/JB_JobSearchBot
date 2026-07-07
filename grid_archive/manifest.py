"""Manifest persistence: the full Item list as JSON (round-trippable, resumable)
and the reviewable slice as CSV."""

from __future__ import annotations

import csv
import json
import os
from typing import Dict, List

import config
from .models import Item, MANIFEST_COLUMNS


def manifest_json_path() -> str:
    return os.path.join(config.OUTPUT_DIR, config.MANIFEST_JSON)


def manifest_csv_path() -> str:
    return os.path.join(config.OUTPUT_DIR, config.MANIFEST_CSV)


def load_items() -> List[Item]:
    path = manifest_json_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [Item.from_dict(d) for d in data]


def save_items(items: List[Item]) -> None:
    # Full fidelity JSON (all fields, including local paths) for resumability.
    with open(manifest_json_path(), "w", encoding="utf-8") as fh:
        json.dump([it.to_dict() for it in items], fh, indent=2, default=str)

    # Reviewable CSV slice.
    with open(manifest_csv_path(), "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for it in items:
            writer.writerow(it.manifest_row())


def index_by_id(items: List[Item]) -> Dict[str, Item]:
    return {it.item_id: it for it in items}
