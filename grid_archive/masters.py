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
from .http import HttpClient
from .logging_setup import get_logger
from .models import Item
from .search import fetcher_map
from . import manifest

log = get_logger()


def credit_line(item: Item) -> str:
    """The human-usable credit requirement for one item."""
    from .rights import ensure_tier
    tier = ensure_tier(item)
    creator = (item.creator or "").strip() or "creator on source page"
    if tier == "public_domain":
        return f"none required (public domain — {creator})"
    if tier == "attribution":
        return f'REQUIRED — "Photo: {creator}, CC BY"'
    if tier == "share_alike":
        return f'GATED (ShareAlike) — "Photo: {creator}, CC BY-SA"; clear before commercial use'
    if tier == "restricted":
        return "DO NOT USE without a direct license from the rights holder"
    return f"VERIFY rights before use ({creator})"


def _embed_credit_exif(item: Item) -> bool:
    """Losslessly embed creator/credit/title into a JPEG master's EXIF, so the
    credit travels inside the file itself. No re-encode — piexif inserts the
    APP1 block in place. Non-JPEG masters (TIFF, video) are skipped; the
    sidecar CREDITS files cover them."""
    path = item.master_path
    if not path or not os.path.exists(path) or not path.lower().endswith((".jpg", ".jpeg")):
        return False
    try:
        import piexif
        try:
            exif = piexif.load(path)
        except Exception:  # noqa: BLE001 - unparseable existing EXIF: start fresh
            exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        zeroth = exif.setdefault("0th", {})
        creator = (item.creator or "").strip()
        if creator:
            zeroth[piexif.ImageIFD.Artist] = creator.encode("ascii", "replace")
        zeroth[piexif.ImageIFD.Copyright] = (
            f"{item.rights} | credit: {credit_line(item)}"[:250]
            .encode("ascii", "replace"))
        if item.title or item.place or item.date:
            desc = item.title or "(untitled)"
            when_where = ", ".join(p for p in (item.place, item.date) if p)
            if when_where:
                # Plain hyphen: EXIF text is ASCII, an em dash would render "?"
                desc += f" - {when_where}"
            zeroth[piexif.ImageIFD.ImageDescription] = (
                f"{desc} ({item.item_id})"[:250].encode("ascii", "replace"))
        # Capture year into DateTimeOriginal so Frame.io / Lightroom / Photos
        # sort by when the photo was SHOT, not when it was downloaded.
        import re
        year = re.search(r"(1[89]\d\d|20\d\d)", item.date or "")
        if year:
            exif.setdefault("Exif", {})[piexif.ExifIFD.DateTimeOriginal] = (
                f"{year.group(1)}:01:01 00:00:00".encode("ascii"))
        piexif.insert(piexif.dump(exif), path)
        return True
    except ImportError:
        log.info("piexif not installed — skipping in-file EXIF credits "
                 "(pip install piexif); CREDITS.txt still written")
        return False
    except Exception as exc:  # noqa: BLE001 - never corrupt a master over metadata
        log.warning("EXIF embed skipped for %s: %s", path, exc)
        return False


def write_credits(items: List[Item]) -> None:
    """Write CREDITS.txt and credits.csv into EACH folder that holds downloaded
    masters (batch folders are per pull date), covering exactly that folder's
    files, and embed credits into JPEG EXIF. Run after every fetch so every
    folder is self-documenting — upload one batch folder to Frame.io or hand it
    to a designer and its paperwork goes with it."""
    import csv

    with_masters = [it for it in items
                    if it.master_path and os.path.exists(it.master_path)]
    if not with_masters:
        return

    embedded = sum(1 for it in with_masters if _embed_credit_exif(it))

    by_dir: Dict[str, List[Item]] = {}
    for it in with_masters:
        by_dir.setdefault(os.path.dirname(it.master_path), []).append(it)

    for folder, group in by_dir.items():
        txt_path = os.path.join(folder, "CREDITS.txt")
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write("PHOTO CREDITS & RIGHTS — travels with these files\n")
            fh.write("=" * 60 + "\n\n")
            for it in sorted(group, key=lambda x: os.path.basename(x.master_path)):
                fh.write(f"{os.path.basename(it.master_path)}\n")
                fh.write(f"  Title:  {it.title or '(untitled)'}\n")
                fh.write(f"  Date:   {it.date or 'undated'}\n")
                fh.write(f"  Place:  {it.place or 'not recorded by the archive'}\n")
                fh.write(f"  Credit: {credit_line(it)}\n")
                fh.write(f"  Rights: {it.rights} [{it.rights_tier}]\n")
                fh.write(f"  Source: {it.item_page_url}\n\n")

        csv_path = os.path.join(folder, "credits.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "item_id", "title", "date", "place", "creator",
                        "rights_tier", "credit_line", "rights", "source_page"])
            for it in group:
                w.writerow([os.path.basename(it.master_path), it.item_id, it.title,
                            it.date, it.place, it.creator, it.rights_tier,
                            credit_line(it), it.rights, it.item_page_url])

    log.info("credits written in %d folder(s) (%d masters, %d with embedded "
             "EXIF credits)", len(by_dir), len(with_masters), embedded)


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
                      use_cache: bool = True,
                      force: bool = False,
                      archive_list: Optional[str] = None,
                      batch: Optional[str] = None) -> List[Item]:
    from datetime import datetime
    from .dedupe import load_archive_keys, in_archive
    archive_keys = load_archive_keys(archive_list)
    # Each pull lands in a dated batch folder (masters/2026-10-17/ by default;
    # --batch names it explicitly). Items fetched in earlier batches keep their
    # recorded master_path, so a rerun never re-downloads them into today's
    # folder — the batch folder only gains what is actually new.
    batch = batch or datetime.now().strftime("%Y-%m-%d")
    dest_dir = os.path.join(config.MASTER_DIR, batch)
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
    fetchers: Dict[str, Fetcher] = fetcher_map(client, use_cache)
    os.makedirs(dest_dir, exist_ok=True)
    log.info("this pull's batch folder: %s", dest_dir)

    for item_id in wanted:
        item = index.get(item_id)
        if item is None:
            log.warning("id not in manifest: %s", item_id)
            continue
        # Archive dedupe: this shot is already in the team archive (Frame.io
        # et al.) per archive_list.txt — don't pull a duplicate. Remove its
        # line from the list to pull it anyway.
        if in_archive(item_id, archive_keys):
            log.info("already in archive, skipping %s (delete its line from "
                     "the archive list to re-pull)", item_id)
            continue
        # Rights gate: never pull masters for NC/ND/SA/in-copyright items —
        # this is commercial use. --force overrides for a shot you intend to
        # license directly with the rights holder.
        from .rights import ensure_tier, HARD_GATED_TIERS
        if ensure_tier(item) in HARD_GATED_TIERS and not force:
            log.warning("rights-gated (%s), skipping master for %s — rights: %r "
                        "(use --force only if you have a direct license)",
                        item.rights_tier, item_id, item.rights[:120])
            continue
        # Rerun short-circuit: already fetched -> no detail request, no download.
        if item.master_path and os.path.exists(item.master_path):
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
        dest = os.path.join(dest_dir, safe_filename(item.item_id, ext))
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

    write_credits(items)
    manifest.save_items(items)
    return items
