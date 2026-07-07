"""Library of Congress fetcher (loc.gov JSON API, no key).

Verified API facts this is built on:
  * Base pattern:  https://www.loc.gov/{endpoint}/?fo=json
  * Endpoints:     /photos/ (photos), /film-and-videos/ (film),
                   /item/{id}/ (full detail — where real download URLs live)
  * Params:        q, fa (filters as filter-name:value, pipe-joined),
                   c (per page), sp (page), at=results,pagination,
                   start_date, end_date, fo=json
  * Anonymous rate limits are enforced; 429s and HTML CAPTCHA responses are
    handled upstream in http.HttpClient with exponential backoff.

Collection / contributor boosts and (for film) the National Screening Room
collection are applied here, inside the fetcher, so the orchestrator stays
source-agnostic. Deduplication across all of these axes happens by item id.
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

_ENDPOINT = {"photo": "photos", "film": "film-and-videos"}
_FILM_COLLECTION_BOOST = "partof:national screening room"
_YEAR_RE = re.compile(r"(1[89]\d\d|20\d\d)")


def _slug_from_url(url: str) -> str:
    """Extract a stable id slug from a loc.gov item/resource URL."""
    if not url:
        return ""
    parts = [p for p in url.split("/") if p]
    return parts[-1] if parts else url


def _as_text(value) -> str:
    """LOC fields come back as str, list[str], or list[dict]. Flatten to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("title") or value.get("label") or ""
    if isinstance(value, list):
        parts = [_as_text(v) for v in value]
        return "; ".join(p for p in parts if p)
    return str(value)


def _iter_urls(node) -> Iterator[Tuple[str, dict]]:
    """Walk an arbitrary nested LOC JSON structure yielding (url, container)
    for every dict that carries a 'url'. Used to find video/master assets in the
    irregular item `resources`/`files` blocks."""
    if isinstance(node, dict):
        url = node.get("url")
        if isinstance(url, str):
            yield url, node
        for v in node.values():
            yield from _iter_urls(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_urls(v)


class LocFetcher(Fetcher):
    source = "LOC"

    # ---- search ---------------------------------------------------------- #

    def _search_specs(self, query: str, media_type: str,
                      group: str) -> List[Tuple[List[str], str, int]]:
        """Return (fa_filters, label, max_pages) triples defining the boosted
        searches to run for one (query, media_type, group).

        Boost specs are subset filters of the same q, so pages 2+ mostly refetch
        what the base spec already yielded — they get 1 page. Contributor boosts
        (the FSA photographers) run only against the rural query set, per spec:
        'neon' filtered by Dorothea Lange is a guaranteed-empty request."""
        specs: List[Tuple[List[str], str, int]] = [([], "base", config.MAX_PAGES_PER_QUERY)]
        if media_type == "photo":
            for boost in config.LOC_COLLECTION_BOOSTS:
                specs.append(([boost], f"collection:{boost}", 1))
            if group == "rural":
                for contrib in config.LOC_CONTRIBUTOR_BOOSTS:
                    specs.append(([contrib], f"contributor:{contrib}", 1))
        else:  # film
            specs.append(([_FILM_COLLECTION_BOOST], "national-screening-room", 1))
        return specs

    def _fetch_page(self, endpoint: str, query: str, fa: List[str], page: int) -> dict:
        url = f"https://www.loc.gov/{endpoint}/"
        params = {
            "q": query,
            "fo": "json",
            "c": config.RESULTS_PER_PAGE,
            "sp": page,
            "at": "results,pagination",
            # Belt and braces: LOC documentation describes date filtering both as
            # start_date/end_date params and as a dates=YYYY/YYYY facet; unknown
            # params are silently ignored, so send both. A client-side year check
            # in _parse_result backstops whichever the server ignores.
            "start_date": config.START_DATE,
            "end_date": config.END_DATE,
            "dates": f"{config.START_DATE[:4]}/{config.END_DATE[:4]}",
        }
        if fa:
            params["fa"] = "|".join(fa)
        return cached_json(url, params,
                           lambda: self.client.get_json(url, params),
                           use_cache=self.use_cache)

    def search(self, query: str, media_type: str, group: str) -> Iterator[Item]:
        endpoint = _ENDPOINT[media_type]

        for fa, label, max_pages in self._search_specs(query, media_type, group):
            items = list(
                self._run_spec_iter(endpoint, query, media_type, group, fa, label,
                                    max_pages))

            # FSA/OWI motherlode fallback: if the boost yielded nothing, retry
            # with the plain-language collection name.
            if not items and fa and fa[0] in config.LOC_COLLECTION_BOOSTS:
                log.info("LOC boost %r empty; falling back to %r",
                         fa[0], config.LOC_COLLECTION_FALLBACK)
                items = list(self._run_spec_iter(
                    endpoint, query, media_type, group,
                    [config.LOC_COLLECTION_FALLBACK], "collection-fallback", 1))

            yield from items

    def _run_spec_iter(self, endpoint, query, media_type, group, fa, label,
                       max_pages=None) -> Iterator[Item]:
        for page in range(1, (max_pages or config.MAX_PAGES_PER_QUERY) + 1):
            data = self._fetch_page(endpoint, query, fa, page)
            results = data.get("results") or []
            if not results:
                break
            for raw in results:
                item = self._parse_result(raw, media_type, group, query)
                if item is not None:
                    yield item
            pagination = data.get("pagination") or {}
            if not pagination.get("next"):
                break

    # ---- parsing --------------------------------------------------------- #

    def _parse_result(self, raw: dict, media_type: str, group: str,
                      query: str) -> Optional[Item]:
        # Digitized-only: require an image_url or a resource. Otherwise skip.
        image_urls = raw.get("image_url") or []
        resources = raw.get("resources") or []
        if not image_urls and not resources:
            return None

        page_url = raw.get("url") or _as_text(raw.get("id"))
        slug = _slug_from_url(raw.get("id") or page_url)
        if not slug:
            return None
        item_id = f"loc:{slug}"

        # image_url is ordered small -> large; medium preview is a middle entry,
        # thumbnail is the first. Never point best_download at the giant TIFF.
        thumbnail = image_urls[0] if image_urls else ""
        if media_type == "photo":
            best = image_urls[len(image_urls) // 2] if image_urls else ""
        else:
            best = ""  # resolved lazily to an MP4 during preview
        master = image_urls[-1] if image_urls else ""

        # Client-side date backstop: whichever server-side date param the API
        # ignores, a confidently out-of-era item never enters the manifest.
        # Undated items are kept (dates are irregular; the manifest date column
        # is there for you to check).
        date_text = _as_text(raw.get("date")) or _as_text(raw.get("dates"))
        year_match = _YEAR_RE.search(date_text)
        if year_match:
            year = int(year_match.group(1))
            if not (int(config.START_DATE[:4]) <= year <= int(config.END_DATE[:4])):
                return None

        rights = _as_text(raw.get("rights")) or _as_text(raw.get("rights_information")) \
            or _as_text(raw.get("rights_advisory"))
        if raw.get("access_restricted"):
            rights = (rights + " | access_restricted").strip(" |")

        return Item(
            item_id=item_id,
            source=self.source,
            format=media_type,
            group=group,
            title=_as_text(raw.get("title")),
            date=date_text,
            creator=_as_text(raw.get("contributor")) or _as_text(raw.get("creator")),
            collection=_as_text(raw.get("partof")),
            rights=rights or "unspecified",
            item_page_url=page_url,
            best_download_url=best,
            thumbnail_url=thumbnail,
            master_url=master,
            query=query,
        )

    # ---- lazy detail (stream + master) ----------------------------------- #

    def _item_detail(self, item: Item) -> dict:
        slug = item.item_id.split(":", 1)[1]
        url = f"https://www.loc.gov/item/{slug}/"
        params = {"fo": "json"}
        return cached_json(url, params,
                           lambda: self.client.get_json(url, params),
                           use_cache=self.use_cache)

    def resolve_stream(self, item: Item) -> Optional[str]:
        try:
            detail = self._item_detail(item)
        except Exception as exc:  # noqa: BLE001 - never crash a run on one item
            log.warning("LOC detail failed for %s: %s", item.item_id, exc)
            return None
        for url, node in _iter_urls(detail.get("resources") or {}):
            mimetype = str(node.get("mimetype", "")).lower()
            if mimetype == "video/mp4" or url.lower().split("?")[0].endswith(".mp4"):
                return url
        return None

    def enrich_master(self, item: Item) -> Item:
        try:
            detail = self._item_detail(item)
        except Exception as exc:  # noqa: BLE001
            log.warning("LOC detail failed for %s: %s", item.item_id, exc)
            return item

        best_url, best_score = item.master_url, -1
        for url, node in _iter_urls(detail.get("resources") or {}):
            low = url.lower().split("?")[0]
            mimetype = str(node.get("mimetype", "")).lower()
            width = node.get("width") or 0
            try:
                width = int(width)
            except (TypeError, ValueError):
                width = 0
            # Prefer TIFF masters, then MP4 for film, then the widest image.
            if low.endswith((".tif", ".tiff")):
                score = 1_000_000
            elif item.format == "film" and (mimetype == "video/mp4" or low.endswith(".mp4")):
                score = 900_000
            elif low.endswith((".jpg", ".jpeg", ".png")):
                score = width
            else:
                continue
            if score > best_score:
                best_url, best_score = url, score
        item.master_url = best_url
        return item
