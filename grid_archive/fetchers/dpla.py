"""Digital Public Library of America fetcher (api.dp.la v2, key required).

  * Endpoint: https://api.dp.la/v2/items?q=...&api_key=KEY
  * One API federating 4000+ US libraries, archives, and museums.

DPLA is a DISCOVERY layer: it returns rich metadata, a thumbnail, and a link to
the holding institution — but not the hi-res master, which lives at that
institution. So DPLA rows are leads to chase (item_page_url points at the
provider); `fetch-masters` can't pull their originals directly, and film rows are
flagged accordingly. The key is passed in by the registry; if it's missing the
source is skipped upstream.
"""

from __future__ import annotations

import re
from typing import Iterator, Optional

import config
from ..cache import cached_json
from ..logging_setup import get_logger
from ..models import Item
from .base import Fetcher

log = get_logger()

_YEAR_RE = re.compile(r"(1[89]\d\d|20\d\d)")


def _first(value) -> str:
    """DPLA fields are frequently either a scalar or a list."""
    if value is None:
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def _join(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v)
    return str(value) if value else ""


def _classify(type_value) -> Optional[str]:
    """Map DPLA sourceResource.type to our format. type may be scalar or list."""
    types = type_value if isinstance(type_value, list) else [type_value]
    types = [str(t).lower() for t in types if t]
    if any("moving image" in t or "video" in t for t in types):
        return "film"
    if any(t == "image" or "still image" in t for t in types):
        return "photo"
    return None


class DplaFetcher(Fetcher):
    source = "DPLA"

    def __init__(self, client, api_key: str, use_cache: bool = True):
        super().__init__(client, use_cache)
        self.api_key = api_key

    def _search_page(self, query: str, dpla_type: str, page: int) -> dict:
        url = f"{config.DPLA_API_BASE}/items"
        params = {
            "q": query,
            "api_key": self.api_key,
            "sourceResource.type": dpla_type,
            "sourceResource.date.after": config.START_DATE,
            "sourceResource.date.before": config.END_DATE,
            "page_size": config.RESULTS_PER_PAGE,
            "page": page,
        }
        return cached_json(url, params,
                           lambda: self.client.get_json(url, params),
                           use_cache=self.use_cache)

    def search(self, query: str, media_type: str, group: str) -> Iterator[Item]:
        dpla_type = "moving image" if media_type == "film" else "image"
        for page in range(1, config.MAX_PAGES_PER_QUERY + 1):
            data = self._search_page(query, dpla_type, page)
            docs = data.get("docs") or []
            if not docs:
                break
            for doc in docs:
                item = self._parse_doc(doc, media_type, group, query)
                if item is not None:
                    yield item
            count = data.get("count", 0)
            if page * config.RESULTS_PER_PAGE >= count:
                break

    def _parse_doc(self, doc: dict, media_type: str, group: str,
                   query: str) -> Optional[Item]:
        sr = doc.get("sourceResource") or {}

        # Trust the type when present; if absent, accept it under the requested
        # media_type rather than dropping a possibly-good lead.
        fmt = _classify(sr.get("type"))
        if fmt is not None and fmt != media_type:
            return None

        doc_id = doc.get("id")
        if not doc_id:
            return None

        date_field = sr.get("date") or {}
        if isinstance(date_field, list):
            date_field = date_field[0] if date_field else {}
        if isinstance(date_field, dict):
            date_str = date_field.get("displayDate") or date_field.get("begin") or ""
        else:
            date_str = str(date_field)

        page_url = _first(doc.get("isShownAt"))
        thumb = _first(doc.get("object"))

        return Item(
            item_id=f"dpla:{doc_id}",
            source=self.source,
            format=media_type,
            group=group,
            title=_join(sr.get("title")),
            date=str(date_str),
            creator=_join(sr.get("creator")),
            collection=_join(doc.get("dataProvider")) or _join(doc.get("provider")),
            rights=_join(sr.get("rights")) or "see provider",
            item_page_url=page_url,
            best_download_url=thumb if media_type == "photo" else "",
            thumbnail_url=thumb,
            master_url="",  # not directly downloadable; master is at the provider
            streaming_note=("lead only — master at provider" if media_type == "film" else ""),
            query=query,
        )

    def resolve_stream(self, item: Item) -> Optional[str]:
        # DPLA does not expose a streamable derivative; film rows fall back to
        # their thumbnail and are flagged for manual sourcing at the provider.
        return None

    def enrich_master(self, item: Item) -> Item:
        # No direct master download from DPLA; the provider page carries it.
        if not item.master_url:
            log.info("DPLA %s: master at provider %s", item.item_id, item.item_page_url)
        return item
