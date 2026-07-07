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
    # Both files are written atomically (tmp + os.replace): the manifest is the
    # resumability state, and checkpoints mean a kill can land mid-write.
    json_path = manifest_json_path()
    tmp = json_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        # Full fidelity JSON (all fields, including local paths) for resumability.
        json.dump([it.to_dict() for it in items], fh, indent=2, default=str)
    os.replace(tmp, json_path)

    csv_path = manifest_csv_path()
    tmp = csv_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        # Reviewable CSV slice.
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for it in items:
            writer.writerow(it.manifest_row())
    os.replace(tmp, csv_path)


def index_by_id(items: List[Item]) -> Dict[str, Item]:
    return {it.item_id: it for it in items}
