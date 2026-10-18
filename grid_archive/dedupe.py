"""Archive dedupe: never re-pull a shot that's already in the team's archive
(Frame.io or anywhere else).

The archive is described by a plain text file — by default archive_list.txt in
the project folder — with one filename or item id per line. Any dump works:
copied Frame.io asset names, `ls` output from an old masters/ folder, or raw
item ids. Matching is deliberately forgiving about extensions, case, and
punctuation, because Frame.io shows "loc_2017878331.jpg" for what the manifest
calls "loc:2017878331".

Blank lines and #-comments are ignored, so the file can be annotated.
"""

from __future__ import annotations

import os
from typing import Optional, Set

import config
from .logging_setup import get_logger

log = get_logger()

DEFAULT_ARCHIVE_LIST = "archive_list.txt"

# Only strip extensions we know; a bare rsplit(".") would mangle ids with
# dots in them (e.g. "loc_afc2001001.69787.jpg" must become "loc_afc2001001.69787").
_MEDIA_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".webp",
               ".mp4", ".mov", ".avi", ".mpeg", ".mpg", ".webm", ".bin")


def norm_key(name: str) -> str:
    """Normalize a filename or item id to a comparable key: basename, known
    media extension stripped, every non-alphanumeric squashed to '_' (the same
    mapping safe_filename uses), lowercased."""
    name = str(name or "").strip().strip('"').strip("'")
    if not name:
        return ""
    base = os.path.basename(name.replace("\\", "/"))
    low = base.lower()
    for ext in _MEDIA_EXTS:
        if low.endswith(ext):
            base = base[: -len(ext)]
            break
    return "".join(c if c.isalnum() else "_" for c in base).lower()


def load_archive_keys(path: Optional[str] = None) -> Set[str]:
    """Read the archive list into a set of normalized keys. With no explicit
    path, looks for archive_list.txt in the project folder and returns an empty
    set (dedupe off) when it isn't there. An explicit path that doesn't exist
    is an error — a typo must not silently disable the guardrail."""
    explicit = path is not None
    path = path or os.path.join(config.OUTPUT_DIR, DEFAULT_ARCHIVE_LIST)
    if not os.path.exists(path):
        if explicit:
            raise SystemExit(f"archive list not found: {path}")
        return set()
    keys: Set[str] = set()
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key = norm_key(line)
            if key:
                keys.add(key)
    log.info("archive list loaded: %s (%d entries) — matching items are "
             "flagged on the sheet and skipped by fetch-masters", path, len(keys))
    return keys


def in_archive(item_id: str, keys: Set[str]) -> bool:
    return bool(keys) and norm_key(item_id) in keys
