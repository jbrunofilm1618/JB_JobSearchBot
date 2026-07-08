"""Wikimedia Commons fetcher (MediaWiki API, no key).

  * Endpoint: https://commons.wikimedia.org/w/api.php
  * Strategy: generator=search over the File namespace (6), then imageinfo for
    each file's full-res original URL, MIME/mediatype, and extmetadata (license,
    date, author).

Commons holds a deep pool of public-domain historical photographs (and some
film). Every file carries an explicit license; we record it and, when
WIKIMEDIA_FREE_ONLY is set, skip anything that doesn't read as freely reusable.
Date bounding is client-side off extmetadata (the search API can't date-filter),
so items with no parseable date are kept and flagged for you to check.
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

# mediatype (from imageinfo) -> our format. Everything else is skipped.
_MEDIATYPE_FORMAT = {"BITMAP": "photo", "DRAWING": "photo", "VIDEO": "film"}

_TAG_RE = re.compile(r"<[^>]+>")
_YEAR_RE = re.compile(r"(1[89]\d\d|20\d\d)")

from ..rights import classify_rights, TIER_RESTRICTED


def _strip(text: Optional[str]) -> str:
    if not text:
        return ""
    return _TAG_RE.sub("", str(text)).strip()


def _extract_year(*values) -> Optional[int]:
    for v in values:
        if not v:
            continue
        m = _YEAR_RE.search(str(v))
        if m:
            return int(m.group(1))
    return None


class WikimediaFetcher(Fetcher):
    source = "WIKIMEDIA"

    def _search_page(self, gsrsearch: str, offset: int) -> dict:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": gsrsearch,
            "gsrnamespace": 6,               # File namespace
            "gsrlimit": config.RESULTS_PER_PAGE,
            "gsroffset": offset,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|mediatype|extmetadata",
            "iiurlwidth": 1024,              # yields a medium thumburl + the original url
        }
        url = config.WIKIMEDIA_API
        return cached_json(url, params,
                           lambda: self.client.get_json(url, params),
                           use_cache=self.use_cache)

    def search(self, query: str, media_type: str, group: str) -> Iterator[Item]:
        want_mediatypes = {mt for mt, fmt in _MEDIATYPE_FORMAT.items() if fmt == media_type}
        filetype = "video" if media_type == "film" else "bitmap"
        gsrsearch = f'{query} filetype:{filetype}'

        offset = 0
        for _ in range(config.MAX_PAGES_PER_QUERY):
            data = self._search_page(gsrsearch, offset)
            pages = ((data.get("query") or {}).get("pages") or {})
            if not pages:
                break
            for page in pages.values():
                item = self._parse_page(page, media_type, group, query, want_mediatypes)
                if item is not None:
                    yield item
            cont = (data.get("continue") or {}).get("gsroffset")
            if cont is None:
                break
            offset = cont

    def _parse_page(self, page: dict, media_type: str, group: str, query: str,
                    want_mediatypes: set) -> Optional[Item]:
        infos = page.get("imageinfo") or []
        if not infos:
            return None
        info = infos[0]

        mediatype = str(info.get("mediatype", "")).upper()
        if mediatype not in want_mediatypes:
            return None

        meta = info.get("extmetadata") or {}

        def m(key):
            return _strip((meta.get(key) or {}).get("value"))

        license_short = m("LicenseShortName")
        license_url = m("LicenseUrl")
        usage = m("UsageTerms")
        rights = license_short or usage or "unspecified"
        if license_url:
            rights = f"{rights} ({license_url})"

        if config.WIKIMEDIA_FREE_ONLY:
            # Full classifier, not a substring blacklist: catches CC-BY-NC /
            # ND / in-copyright markers the old hint list let through, while
            # still keeping "No known copyright restrictions" (Flickr Commons).
            if classify_rights(f"{license_short} {usage} {license_url}") == TIER_RESTRICTED:
                return None

        # Era check uses DateTimeOriginal ONLY. extmetadata DateTime is the file
        # UPLOAD/scan timestamp (2005-2025 for almost everything) — using it as a
        # fallback would misdate genuinely in-era photos to the upload year and
        # silently drop them. An undated 1930s photo must be kept and flagged.
        year = _extract_year(m("DateTimeOriginal"))
        start_year = int(config.START_DATE[:4])
        end_year = int(config.END_DATE[:4])
        streaming_note = ""
        if year is not None and not (start_year <= year <= end_year):
            return None  # confidently out of era
        if year is None:
            streaming_note = "undated — verify era"

        pageid = page.get("pageid")
        title = _strip(page.get("title")).replace("File:", "")
        item_id = f"wc:{pageid}" if pageid else f"wc:{title}"

        original = info.get("url", "")            # full-res original
        thumb = info.get("thumburl", "") or original
        creator = _strip(m("Artist")) or _strip(m("Credit"))

        item = Item(
            item_id=item_id,
            source=self.source,
            format=media_type,
            group=group,
            title=title,
            date=m("DateTimeOriginal") or (str(year) if year else ""),
            creator=creator,
            collection="Wikimedia Commons",
            rights=rights,
            item_page_url=info.get("descriptionurl", ""),
            best_download_url=thumb if media_type == "photo" else "",
            thumbnail_url=thumb,
            master_url=original,
            streaming_note=streaming_note,
            query=query,
        )
        return item

    def resolve_stream(self, item: Item) -> Optional[str]:
        # The original file IS the streamable asset (often .webm/.ogv; ffmpeg
        # samples those fine). We stored it as master_url at search time.
        return item.master_url or None

    def enrich_master(self, item: Item) -> Item:
        # master_url already points at the full-res original.
        return item
