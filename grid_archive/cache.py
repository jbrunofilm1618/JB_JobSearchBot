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


# Credential params are excluded from cache keys so rotating a key never
# invalidates the response cache (and keys never appear in cache filenames).
_SECRET_PARAMS = {"api_key", "apikey", "key", "token"}


def _api_cache_path(url: str, params: Optional[dict]) -> str:
    if params:
        params = {k: v for k, v in params.items() if k.lower() not in _SECRET_PARAMS}
    full = requests.Request("GET", url, params=params).prepare().url
    digest = hashlib.sha1(full.encode("utf-8")).hexdigest()
    return os.path.join(config.CACHE_DIR, "api", f"{digest}.json")


def cached_json(url: str, params: Optional[dict], fetch: Callable[[], dict],
                use_cache: bool = True) -> dict:
    """Return cached JSON for (url, params) if present, else call `fetch`, store
    the result, and return it. A corrupt cache file (e.g. from a mid-write kill)
    is treated as a miss and deleted, never a crash."""
    path = _api_cache_path(url, params)
    if use_cache and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError:
            os.remove(path)  # truncated by an interrupted write — refetch

    data = fetch()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)  # atomic: a kill mid-write can't corrupt the cache
    return data


def safe_filename(item_id: str, ext: str) -> str:
    """Turn a namespaced item id into a filesystem-safe basename."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in item_id)
    return f"{safe}{ext}"
