"""Response cache so reruns are cheap and resumable.

API responses are cached under cache/api/<sha1(url)>.json. The cache is keyed by
the fully-resolved request URL (params included), so identical queries across a
rerun return instantly and never re-hit the rate-limited hosts.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Callable, Optional

import config
import requests


def _api_cache_path(url: str, params: Optional[dict]) -> str:
    full = requests.Request("GET", url, params=params).prepare().url
    digest = hashlib.sha1(full.encode("utf-8")).hexdigest()
    return os.path.join(config.CACHE_DIR, "api", f"{digest}.json")


def cached_json(url: str, params: Optional[dict], fetch: Callable[[], dict],
                use_cache: bool = True) -> dict:
    """Return cached JSON for (url, params) if present, else call `fetch`, store
    the result, and return it."""
    path = _api_cache_path(url, params)
    if use_cache and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    data = fetch()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


def safe_filename(item_id: str, ext: str) -> str:
    """Turn a namespaced item id into a filesystem-safe basename."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in item_id)
    return f"{safe}{ext}"
