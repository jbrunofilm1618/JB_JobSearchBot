"""Common fetcher interface.

Both LOC and Internet Archive implement `Fetcher`. A third source (e.g. the
National Archives catalog API) can be added later by writing one more subclass
and registering it in `search.build_fetchers()` — nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from ..http import HttpClient
from ..models import Item


class Fetcher(ABC):
    #: short source tag stored on every item ("LOC" | "IA" | ...)
    source: str = "BASE"

    def __init__(self, client: HttpClient, use_cache: bool = True):
        self.client = client
        self.use_cache = use_cache

    @abstractmethod
    def search(self, query: str, media_type: str, group: str) -> Iterator[Item]:
        """Yield digitized items matching `query` for one `media_type`
        ("photo" | "film"), tagged with `group`. Non-digitized items (no image
        or resource) must be skipped. Date bounding uses config.START_DATE /
        END_DATE. Implementations should be defensive: archive metadata is
        irregular and missing fields must never crash a run."""
        raise NotImplementedError

    @abstractmethod
    def resolve_stream(self, item: Item) -> Optional[str]:
        """For a film item, return the best streamable MP4 derivative URL, or
        None if the item is not digitized for streaming. Called lazily by the
        `preview` stage so per-item detail requests only happen for films we
        actually preview. Photos never call this."""
        raise NotImplementedError

    @abstractmethod
    def enrich_master(self, item: Item) -> Item:
        """Populate `item.master_url` (and `master_path` target) with the
        highest-resolution asset for this item, hitting the per-item detail
        endpoint if needed. Used by `fetch-masters` only, so it stays lazy."""
        raise NotImplementedError
