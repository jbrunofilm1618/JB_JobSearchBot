"""Internet Archive fetcher (advancedsearch + metadata endpoints, no key).

  * Search:   https://archive.org/advancedsearch.php?q=...&output=json
  * Metadata: https://archive.org/metadata/{identifier}   (file list, MP4 derivatives)
  * Download: https://archive.org/download/{identifier}/{filename}
  * Thumb:    https://archive.org/services/img/{identifier}

Scoped to the prelinger and FedFlix film collections and date-bounded. "Power
and the Land" (1940, Joris Ivens, REA) is pulled in on every run regardless of
the query matrix. `licenseurl` is captured into the manifest for every item.
"""

from __future__ import annotations

from typing import Iterator, List, Optional
from urllib.parse import quote

import config
from ..cache import cached_json
from ..logging_setup import get_logger
from ..models import Item
from .base import Fetcher

log = get_logger()

_MPEG4_FORMAT_HINTS = ("mp4", "mpeg4", "h.264")


def _year_bounds() -> str:
    start_year = config.START_DATE[:4]
    end_year = config.END_DATE[:4]
    return f"year:[{start_year} TO {end_year}]"


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v)
    return str(value)


def _parse_length(raw) -> Optional[float]:
    """IA file `length` is either seconds ('123.4') or 'MM:SS' / 'HH:MM:SS'."""
    if raw is None:
        return None
    s = str(raw)
    try:
        if ":" in s:
            parts = [float(p) for p in s.split(":")]
            secs = 0.0
            for p in parts:
                secs = secs * 60 + p
            return secs
        return float(s)
    except ValueError:
        return None


class InternetArchiveFetcher(Fetcher):
    source = "IA"

    def __init__(self, client, use_cache: bool = True):
        super().__init__(client, use_cache)
        self._always_emitted = False

    # ---- search ---------------------------------------------------------- #

    def _collection_clause(self) -> str:
        cols = " OR ".join(f"collection:{c}" for c in config.IA_COLLECTIONS)
        return f"({cols})"

    def _run_query(self, q: str, group: str, query_label: str) -> Iterator[Item]:
        url = "https://archive.org/advancedsearch.php"
        for page in range(1, config.MAX_PAGES_PER_QUERY + 1):
            params = {
                "q": q,
                "output": "json",
                "rows": config.RESULTS_PER_PAGE,
                "page": page,
                "fl[]": ["identifier", "title", "date", "year", "creator",
                         "licenseurl", "collection", "mediatype"],
            }
            data = cached_json(url, params,
                               lambda p=params: self.client.get_json(url, p),
                               use_cache=self.use_cache)
            docs = (data.get("response") or {}).get("docs") or []
            if not docs:
                break
            for doc in docs:
                item = self._parse_doc(doc, group, query_label)
                if item is not None:
                    yield item
            num_found = (data.get("response") or {}).get("numFound", 0)
            if page * config.RESULTS_PER_PAGE >= num_found:
                break

    def search(self, query: str, media_type: str, group: str) -> Iterator[Item]:
        # IA is scoped to film collections in this tool; photos come from LOC.
        if media_type != "film":
            return

        # Pull the always-include targets once, on the first film query.
        if not self._always_emitted:
            self._always_emitted = True
            for title in config.IA_ALWAYS_INCLUDE_TITLES:
                q = f'{self._collection_clause()} AND title:("{title}")'
                yield from self._run_query(q, group, f"always-include:{title}")

        terms = f'"{query}"' if " " in query else query
        q = f"{self._collection_clause()} AND ({terms}) AND {_year_bounds()}"
        yield from self._run_query(q, group, query)

    # ---- parsing --------------------------------------------------------- #

    def _parse_doc(self, doc: dict, group: str, query_label: str) -> Optional[Item]:
        identifier = doc.get("identifier")
        if not identifier:
            return None
        item_id = f"ia:{identifier}"
        return Item(
            item_id=item_id,
            source=self.source,
            format="film",
            group=group,
            title=_as_text(doc.get("title")),
            date=_as_text(doc.get("date")) or _as_text(doc.get("year")),
            creator=_as_text(doc.get("creator")),
            collection=_as_text(doc.get("collection")),
            rights=_as_text(doc.get("licenseurl")) or "unspecified",
            item_page_url=f"https://archive.org/details/{identifier}",
            best_download_url="",  # resolved to an MP4 during preview
            thumbnail_url=f"https://archive.org/services/img/{identifier}",
            query=query_label,
        )

    # ---- lazy detail (stream + master) ----------------------------------- #

    def _metadata(self, identifier: str) -> dict:
        url = f"https://archive.org/metadata/{identifier}"
        return cached_json(url, None,
                           lambda: self.client.get_json(url, None),
                           use_cache=self.use_cache)

    def _mp4_files(self, meta: dict) -> List[dict]:
        files = meta.get("files") or []
        out = []
        for f in files:
            name = str(f.get("name", ""))
            fmt = str(f.get("format", "")).lower()
            if name.lower().endswith(".mp4") or any(h in fmt for h in _MPEG4_FORMAT_HINTS):
                out.append(f)
        return out

    def resolve_stream(self, item: Item) -> Optional[str]:
        identifier = item.item_id.split(":", 1)[1]
        try:
            meta = self._metadata(identifier)
        except Exception as exc:  # noqa: BLE001
            log.warning("IA metadata failed for %s: %s", item.item_id, exc)
            return None
        mp4s = self._mp4_files(meta)
        if not mp4s:
            return None

        # Prefer the smallest MP4 derivative — cheapest to preview, still fine
        # for frame sampling. The download stage enforces the hard size cap.
        def size_of(f):
            try:
                return int(f.get("size", 0))
            except (TypeError, ValueError):
                return 0

        mp4s.sort(key=size_of)
        chosen = mp4s[0]
        item.duration_seconds = _parse_length(chosen.get("length"))
        name = quote(chosen["name"])
        return f"https://archive.org/download/{identifier}/{name}"

    def enrich_master(self, item: Item) -> Item:
        identifier = item.item_id.split(":", 1)[1]
        try:
            meta = self._metadata(identifier)
        except Exception as exc:  # noqa: BLE001
            log.warning("IA metadata failed for %s: %s", item.item_id, exc)
            return item

        files = meta.get("files") or []

        def size_of(f):
            try:
                return int(f.get("size", 0))
            except (TypeError, ValueError):
                return 0

        # Highest-res master: the original video, else the largest MP4.
        originals = [f for f in files
                     if str(f.get("source", "")).lower() == "original"
                     and (str(f.get("name", "")).lower().endswith((".mp4", ".mpeg", ".mov", ".avi"))
                          or "mpeg" in str(f.get("format", "")).lower())]
        pool = originals or self._mp4_files(meta)
        if pool:
            best = max(pool, key=size_of)
            item.master_url = f"https://archive.org/download/{identifier}/{quote(best['name'])}"
        return item
