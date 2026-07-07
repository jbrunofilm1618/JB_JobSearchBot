"""`search` subcommand: run the query matrix across photos + film on every
source, dedupe by item id, and write the manifest.

Merges with an existing manifest so a rerun (or a run with an expanded query
list) only adds new items and never discards judge scores or local preview
paths already recorded."""

from __future__ import annotations

import os
from typing import Dict, List

import config
from .fetchers.base import Fetcher
from .fetchers.dpla import DplaFetcher
from .fetchers.internet_archive import InternetArchiveFetcher
from .fetchers.loc import LocFetcher
from .fetchers.nara import NaraFetcher
from .fetchers.wikimedia import WikimediaFetcher
from .http import HttpClient
from .logging_setup import get_logger
from .models import Item
from . import manifest

log = get_logger()

MEDIA_TYPES = ("photo", "film")


def build_fetchers(client: HttpClient, use_cache: bool) -> List[Fetcher]:
    """The registry. Sources are added by appending one more Fetcher here.

    API keys are read from the environment (after any .env is loaded). A source
    that needs a key it doesn't have is skipped with a warning, not an error."""
    fetchers: List[Fetcher] = [
        LocFetcher(client, use_cache=use_cache),
        InternetArchiveFetcher(client, use_cache=use_cache),
    ]

    if getattr(config, "WIKIMEDIA_ENABLED", False):
        fetchers.append(WikimediaFetcher(client, use_cache=use_cache))

    if getattr(config, "NARA_ENABLED", False):
        nara_key = os.environ.get(config.NARA_API_KEY_ENV)  # optional
        fetchers.append(NaraFetcher(client, api_key=nara_key, use_cache=use_cache))

    if getattr(config, "DPLA_ENABLED", False):
        dpla_key = os.environ.get(config.DPLA_API_KEY_ENV)
        if dpla_key:
            fetchers.append(DplaFetcher(client, api_key=dpla_key, use_cache=use_cache))
        else:
            log.warning("DPLA enabled but %s is not set — skipping DPLA. "
                        "Get a free key at https://pro.dp.la/developers/policies",
                        config.DPLA_API_KEY_ENV)

    log.info("active sources: %s", ", ".join(f.source for f in fetchers))
    return fetchers


def fetcher_map(client: HttpClient, use_cache: bool) -> Dict[str, Fetcher]:
    """Source-tag -> Fetcher, for the preview and fetch-masters stages that
    resolve per-item detail. Built from the same registry as the search."""
    return {f.source: f for f in build_fetchers(client, use_cache)}


def run_search(use_cache: bool = True) -> List[Item]:
    client = HttpClient()
    fetchers = build_fetchers(client, use_cache)

    # Seed with the existing manifest so we merge rather than clobber.
    existing = manifest.load_items()
    by_id: Dict[str, Item] = manifest.index_by_id(existing)
    new_count = 0

    for fetcher in fetchers:
        for group, query in config.QUERIES:
            for media_type in MEDIA_TYPES:
                try:
                    for item in fetcher.search(query, media_type, group):
                        if item.item_id in by_id:
                            continue  # dedupe across queries and sources
                        by_id[item.item_id] = item
                        new_count += 1
                except Exception as exc:  # noqa: BLE001 - one query must not sink the run
                    log.warning("search failed [%s %s %r]: %s",
                                fetcher.source, media_type, query, exc)

    items = list(by_id.values())
    manifest.save_items(items)
    log.info("search complete: %d total items (%d new) written to %s",
             len(items), new_count, manifest.manifest_csv_path())
    return items
