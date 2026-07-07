"""National Archives (NARA) Catalog fetcher (catalog.archives.gov API v2).

  * Search: {NARA_API_BASE}/records/search?q=...&limit=...&offset=...
  * Auth:   optional `x-api-key` header (free from api.data.gov). Runs anonymously
            if no key is set; if the host answers 403, set NARA_API_KEY and rerun.

NARA holds US-government moving images and photographs (TVA, REA, WPA, newsreels),
almost all public domain, and its digital objects ARE the hi-res masters.

The v2 response nests each record under one of a few container keys depending on
deployment, so parsing here is deliberately defensive: it looks through
`_source` / `fields` / `record`, walks the structure to find digital objects and
dates, and never lets one malformed record sink the run. Because this sandbox
cannot reach catalog.archives.gov, the parser is written to the documented shape
and should be smoke-tested against a live response.
"""

from __future__ import annotations

import re
from typing import Iterator, List, Optional, Tuple

import config
from ..cache import cached_json
from ..logging_setup import get_logger
from ..models import Item
from .base import Fetcher

log = get_logger()

_YEAR_RE = re.compile(r"(1[89]\d\d|20\d\d)")
_VIDEO_EXT = (".mp4", ".mov", ".avi", ".mpeg", ".mpg", ".m4v", ".webm")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif")


def _record_of(hit: dict) -> dict:
    """Pull the record body out of a search hit, tolerating v2 shape vari/drift."""
    for key in ("_source", "fields", "record", "description"):
        val = hit.get(key)
        if isinstance(val, dict) and val:
            # Some shapes nest one more level under 'record'.
            inner = val.get("record")
            return inner if isinstance(inner, dict) and inner else val
    return hit


def _walk(node) -> Iterator[dict]:
    """Yield every dict in a nested structure (for finding digital objects)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _digital_objects(record: dict) -> List[dict]:
    """Collect dicts that look like a digital object (carry a downloadable URL)."""
    objs = []
    seen = set()
    for node in _walk(record):
        url = node.get("objectUrl") or node.get("url") or node.get("objectFileUrl")
        if isinstance(url, str) and url.startswith("http") and url not in seen:
            seen.add(url)
            objs.append({"url": url,
                         "type": str(node.get("objectType", "")),
                         "size": node.get("objectFileSize") or node.get("size") or 0})
    return objs


def _year_in_record(record: dict) -> Optional[int]:
    for key in ("productionDates", "coverageStartDate", "coverageEndDate",
                "copyrightDates", "broadcastDates", "productionDateNote"):
        found = _extract_year(record.get(key))
        if found is not None:
            return found
    return None


def _extract_year(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        text = str(value)
    else:
        text = str(value)
    m = _YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def _classify(record: dict, objects: List[dict]) -> Optional[str]:
    """photo | film | None, from generalRecordsTypes then object extensions."""
    types = record.get("generalRecordsTypes") or record.get("objectType") or []
    if isinstance(types, str):
        types = [types]
    blob = " ".join(str(t).lower() for t in types)
    if "moving image" in blob or "video" in blob:
        return "film"
    if "photograph" in blob or "graphic" in blob or "still" in blob:
        return "photo"
    # Fall back to what the digital objects look like.
    exts = " ".join(o["url"].lower().split("?")[0] for o in objects)
    if any(e in exts for e in _VIDEO_EXT):
        return "film"
    if any(e in exts for e in _IMAGE_EXT):
        return "photo"
    return None


class NaraFetcher(Fetcher):
    source = "NARA"

    def __init__(self, client, api_key: Optional[str] = None, use_cache: bool = True):
        super().__init__(client, use_cache)
        self.api_key = api_key
        self._warned_403 = False
        self._disabled = False
        # The key is sent per-request to NARA endpoints only — NEVER installed on
        # the shared session, which would transmit it to every other host the
        # tool contacts (LOC, IA, Wikimedia, image hosts...).
        self._headers = {"x-api-key": api_key} if api_key else None

    def _search_page(self, query: str, offset: int) -> dict:
        url = f"{config.NARA_API_BASE}/records/search"
        params = {
            "q": query,
            "limit": config.RESULTS_PER_PAGE,
            "offset": offset,
        }
        try:
            return cached_json(url, params,
                               lambda: self.client.get_json(url, params,
                                                            headers=self._headers),
                               use_cache=self.use_cache)
        except Exception as exc:  # noqa: BLE001
            if "403" in str(exc):
                self._disabled = True  # latch: no more doomed requests this run
                if not self._warned_403:
                    self._warned_403 = True
                    log.warning("NARA returned 403 — set a free NARA_API_KEY "
                                "(api.data.gov) and rerun. Skipping NARA for "
                                "the rest of this run.")
            raise

    def search(self, query: str, media_type: str, group: str) -> Iterator[Item]:
        if self._disabled:
            return
        start_year = int(config.START_DATE[:4])
        end_year = int(config.END_DATE[:4])

        for page in range(config.MAX_PAGES_PER_QUERY):
            offset = page * config.RESULTS_PER_PAGE
            try:
                data = self._search_page(query, offset)
            except Exception:  # noqa: BLE001 - already logged; stop this source's query
                return
            hits = (((data.get("body") or {}).get("hits") or {}).get("hits")) or []
            if not hits:
                break
            for hit in hits:
                item = self._parse_hit(hit, media_type, group, query,
                                       start_year, end_year)
                if item is not None:
                    yield item

    def _parse_hit(self, hit, media_type, group, query, start_year, end_year) -> Optional[Item]:
        record = _record_of(hit)
        objects = _digital_objects(record)
        if not objects:
            return None  # digitized-only

        fmt = _classify(record, objects)
        if fmt != media_type:
            return None

        year = _year_in_record(record)
        streaming_note = ""
        if year is not None and not (start_year <= year <= end_year):
            return None
        if year is None:
            streaming_note = "undated — verify era"

        na_id = hit.get("_id") or record.get("naId") or record.get("naId")
        if not na_id:
            return None
        item_id = f"nara:{na_id}"

        img_objs, vid_objs = _split_objects(objects)
        if media_type == "film":
            best = vid_objs[0]["url"] if vid_objs else ""
            master = _largest(vid_objs) or (best or "")
            thumb = img_objs[0]["url"] if img_objs else ""
        else:
            thumb = _smallest(img_objs) or (img_objs[0]["url"] if img_objs else "")
            best = thumb
            master = _largest(img_objs) or thumb

        rights = _rights(record)

        return Item(
            item_id=item_id,
            source=self.source,
            format=media_type,
            group=group,
            title=str(record.get("title") or record.get("naId") or "").strip(),
            date=str(year) if year else "",
            creator=_creator(record),
            collection=str(record.get("recordGroupNumber") or "National Archives"),
            rights=rights,
            item_page_url=f"https://catalog.archives.gov/id/{na_id}",
            best_download_url=best if media_type == "photo" else "",
            thumbnail_url=thumb,
            master_url=master,
            streaming_note=streaming_note,
            query=query,
        )

    def resolve_stream(self, item: Item) -> Optional[str]:
        # For NARA the streamable/master video URL is already on master_url.
        low = (item.master_url or "").lower().split("?")[0]
        if item.master_url and low.endswith(_VIDEO_EXT):
            return item.master_url
        return None

    def enrich_master(self, item: Item) -> Item:
        return item  # master_url already the hi-res object


def _split_objects(objects: List[dict]) -> Tuple[List[dict], List[dict]]:
    imgs, vids = [], []
    for o in objects:
        low = o["url"].lower().split("?")[0]
        if low.endswith(_VIDEO_EXT) or "video" in o["type"].lower():
            vids.append(o)
        elif low.endswith(_IMAGE_EXT) or "image" in o["type"].lower():
            imgs.append(o)
    return imgs, vids


def _size(o: dict) -> int:
    try:
        return int(o.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def _largest(objs: List[dict]) -> str:
    return max(objs, key=_size)["url"] if objs else ""


def _smallest(objs: List[dict]) -> str:
    sized = [o for o in objs if _size(o) > 0]
    return min(sized, key=_size)["url"] if sized else ""


def _rights(record: dict) -> str:
    parts = []
    for key in ("useRestriction", "accessRestriction"):
        val = record.get(key)
        if isinstance(val, dict):
            status = val.get("status") or val.get("value")
            if status:
                parts.append(f"{key}: {status}")
        elif val:
            parts.append(f"{key}: {val}")
    return " | ".join(parts) if parts else "US Government record (typically public domain)"


def _creator(record: dict) -> str:
    for key in ("creators", "creator", "contributors", "productionOrganization"):
        val = record.get(key)
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, dict):
                return str(first.get("heading") or first.get("name") or "").strip()
            return str(first)
        if isinstance(val, str) and val:
            return val
    return ""
