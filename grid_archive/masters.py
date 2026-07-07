"""`fetch-masters` subcommand: pull the highest-resolution asset for ONLY the
item ids you select (from --ids, an --ids-file, or the judged shortlist).

Kept separate from `preview` on purpose: masters are large (TIFFs, original
video) and you only want them for the handful of shots you actually chose.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import config
from .cache import safe_filename
from .fetchers.base import Fetcher
from .fetchers.internet_archive import InternetArchiveFetcher
from .fetchers.loc import LocFetcher
from .http import HttpClient
from .logging_setup import get_logger
from .models import Item
from . import manifest

log = get_logger()


def _selected_ids(ids: Optional[List[str]], ids_file: Optional[str],
                  items: List[Item]) -> List[str]:
    if ids:
        return ids
    if ids_file:
        with open(ids_file, "r", encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    # Default: the judged shortlist.
    shortlist = [it.item_id for it in items if it.judge_keep]
    if shortlist:
        log.info("no ids given; using %d shortlisted items", len(shortlist))
    return shortlist


def run_fetch_masters(ids: Optional[List[str]] = None,
                      ids_file: Optional[str] = None,
                      use_cache: bool = True) -> List[Item]:
    items = manifest.load_items()
    if not items:
        log.warning("no manifest found; run `search` first")
        return []
    index = manifest.index_by_id(items)

    wanted = _selected_ids(ids, ids_file, items)
    if not wanted:
        log.warning("no item ids selected (pass --ids / --ids-file, or run `judge` first)")
        return items

    client = HttpClient()
    fetchers: Dict[str, Fetcher] = {
        "LOC": LocFetcher(client, use_cache=use_cache),
        "IA": InternetArchiveFetcher(client, use_cache=use_cache),
    }
    os.makedirs(config.MASTER_DIR, exist_ok=True)

    for item_id in wanted:
        item = index.get(item_id)
        if item is None:
            log.warning("id not in manifest: %s", item_id)
            continue
        fetcher = fetchers.get(item.source)
        if fetcher is not None:
            try:
                fetcher.enrich_master(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("master resolve failed for %s: %s", item_id, exc)
        if not item.master_url:
            log.warning("no master url for %s", item_id)
            continue

        ext = os.path.splitext(item.master_url.split("?")[0])[1] or ".bin"
        dest = os.path.join(config.MASTER_DIR, safe_filename(item.item_id, ext))
        if os.path.exists(dest):
            item.master_path = dest
            continue
        try:
            written = client.download(item.master_url, dest)
            if written > 0:
                item.master_path = dest
                log.info("master saved: %s (%d bytes)", dest, written)
        except Exception as exc:  # noqa: BLE001
            log.warning("master download failed for %s: %s", item_id, exc)

    manifest.save_items(items)
    return items
