"""`make-delivery` subcommand: turn a batch folder's masters into shareable
high-quality JPEGs, every one carrying its own paperwork.

Masters stay untouched (the 120MB+ LOC TIFFs are the archival copies). This
writes masters/<batch>/delivery/ with:
  * one JPEG per raster master — TIFFs/PNGs re-encoded, JPEGs re-saved —
    full resolution unless --max-px caps the long edge
  * credits embedded in EVERY file's EXIF (Artist, Copyright/credit line,
    title - place, date in the description, capture year in DateTimeOriginal)
  * CREDITS.txt + credits.csv covering exactly the delivered files

Needs Pillow (in requirements.txt); films and unreadable files are skipped
with a log line and remain covered by the batch folder's own credits files.
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

import config
from .logging_setup import get_logger
from .masters import _embed_credit_exif, write_credit_files
from .models import Item
from . import manifest

log = get_logger()


def _resolve_batch_dir(batch: Optional[str]) -> str:
    """Explicit --batch name, else the most recent batch folder under masters/,
    else the flat masters/ itself (buckets that pulled before batch folders)."""
    if batch:
        d = os.path.join(config.MASTER_DIR, batch)
        if not os.path.isdir(d):
            raise SystemExit(f"no such batch folder: {d}")
        return d
    if os.path.isdir(config.MASTER_DIR):
        subs = sorted(
            os.path.join(config.MASTER_DIR, n)
            for n in os.listdir(config.MASTER_DIR)
            if os.path.isdir(os.path.join(config.MASTER_DIR, n)) and n != "delivery")
        if subs:
            return subs[-1]  # dated names sort chronologically
        return config.MASTER_DIR
    raise SystemExit("no masters downloaded yet — run fetch-masters first")


def _batch_items(batch_dir: str) -> List[Item]:
    norm = os.path.normpath(os.path.abspath(batch_dir))
    return [it for it in manifest.load_items()
            if it.master_path and os.path.exists(it.master_path)
            and os.path.normpath(os.path.abspath(
                os.path.dirname(it.master_path))) == norm]


def run_make_delivery(batch: Optional[str] = None, quality: int = 90,
                      max_px: Optional[int] = None) -> Optional[str]:
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("make-delivery needs Pillow: "
                         ".venv/bin/pip install -r requirements.txt")
    # Archive scans run well past Pillow's decompression-bomb default; these
    # files come from archives we chose to pull from, not hostile uploads.
    Image.MAX_IMAGE_PIXELS = None

    batch_dir = _resolve_batch_dir(batch)
    items = _batch_items(batch_dir)
    if not items:
        log.warning("no manifest items with masters in %s", batch_dir)
        return None

    out_dir = os.path.join(batch_dir, "delivery")
    os.makedirs(out_dir, exist_ok=True)
    log.info("delivery folder: %s (%d masters, quality=%d%s)",
             out_dir, len(items), quality,
             f", long edge capped at {max_px}px" if max_px else ", full resolution")

    delivered: List[Tuple[str, Item]] = []
    embedded = skipped = 0
    for it in sorted(items, key=lambda x: x.master_path):
        src = it.master_path
        name = os.path.splitext(os.path.basename(src))[0] + ".jpg"
        dest = os.path.join(out_dir, name)
        if os.path.exists(dest):
            delivered.append((name, it))  # rerun: keep, still covered by credits
            continue
        try:
            with Image.open(src) as img:
                if img.mode in ("I", "I;16", "I;16B", "I;16L"):
                    # 16-bit scans: scale down to 8-bit grayscale first.
                    img = img.point(lambda v: v / 256).convert("L")
                elif img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                if max_px and max(img.size) > max_px:
                    img.thumbnail((max_px, max_px), Image.LANCZOS)
                img.save(dest, "JPEG", quality=quality, optimize=True)
        except Exception as exc:  # noqa: BLE001 - film/corrupt file: skip, keep going
            log.warning("not deliverable as JPEG, skipping %s: %s",
                        os.path.basename(src), exc)
            skipped += 1
            continue
        if _embed_credit_exif(it, path=dest):
            embedded += 1
        delivered.append((name, it))

    if delivered:
        write_credit_files(out_dir, delivered)
    log.info("delivery ready: %s (%d JPEGs, %d with embedded EXIF credits, "
             "%d skipped)", out_dir, len(delivered), embedded, skipped)
    return out_dir
