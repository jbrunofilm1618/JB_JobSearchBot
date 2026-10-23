"""`credits-report` subcommand: full metadata + licensing paperwork for a list
of photos, wherever they now live (Frame.io, a website, a drive).

Input is a plain text file of filenames (one per line — a Frame.io folder
listing, `ls` output, or item ids; # comments fine). Those names are matched
back to the scrape manifests the same forgiving way the archive dedupe works,
across one or more bucket folders (any folder holding a manifest.json from a
run of this tool). Output is the complete provenance record for each photo:

  <out>.csv  — spreadsheet: file, item id, title, date, place, creator,
               rights tier, credit line, full rights text, source page, bucket
  <out>.txt  — the same, human-readable, plus an UNMATCHED section listing
               names found in no manifest (to chase by hand)
"""

from __future__ import annotations

import csv
import json
import os
from typing import Dict, List, Optional, Tuple

from .dedupe import norm_key
from .logging_setup import get_logger
from .masters import credit_line
from .models import Item

log = get_logger()


def _load_bucket(bucket: str) -> List[Item]:
    path = os.path.join(os.path.expanduser(bucket), "manifest.json")
    if not os.path.exists(path):
        raise SystemExit(f"no manifest.json in {bucket!r} — is it a scrape bucket?")
    with open(path, "r", encoding="utf-8") as fh:
        return [Item.from_dict(d) for d in json.load(fh)]


def run_credits_report(names_file: str, buckets: List[str],
                       out: str = "website_credits") -> Tuple[str, str]:
    with open(names_file, "r", encoding="utf-8") as fh:
        names = [ln.strip() for ln in fh
                 if ln.strip() and not ln.strip().startswith("#")]
    if not names:
        raise SystemExit(f"{names_file} is empty")

    # key -> (item, bucket). First bucket listed wins on duplicates, so put
    # the most authoritative bucket first on the command line.
    index: Dict[str, Tuple[Item, str]] = {}
    for bucket in buckets:
        items = _load_bucket(bucket)
        label = os.path.basename(os.path.normpath(os.path.expanduser(bucket)))
        fresh = 0
        for it in items:
            key = norm_key(it.item_id)
            if key and key not in index:
                index[key] = (it, label)
                fresh += 1
        log.info("bucket %s: %d manifest items (%d new to the index)",
                 label, len(items), fresh)

    matched: List[Tuple[str, Item, str]] = []
    unmatched: List[str] = []
    seen = set()
    for name in names:
        key = norm_key(name)
        if key in seen:
            continue  # same shot listed twice (e.g. master + delivery copy)
        seen.add(key)
        hit = index.get(key)
        if hit:
            matched.append((name, hit[0], hit[1]))
        else:
            unmatched.append(name)

    csv_path = out + ".csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "item_id", "title", "date", "place", "creator",
                    "rights_tier", "credit_line", "rights", "source_page",
                    "bucket"])
        for name, it, bucket in matched:
            w.writerow([name, it.item_id, it.title, it.date, it.place,
                        it.creator, it.rights_tier, credit_line(it),
                        it.rights, it.item_page_url, bucket])

    txt_path = out + ".txt"
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write("PHOTO METADATA & LICENSING REPORT\n")
        fh.write(f"{len(matched)} matched, {len(unmatched)} unmatched "
                 f"of {len(seen)} unique files\n")
        fh.write("=" * 60 + "\n\n")
        for name, it, bucket in sorted(matched, key=lambda m: m[0]):
            fh.write(f"{name}\n")
            fh.write(f"  Title:   {it.title or '(untitled)'}\n")
            fh.write(f"  Date:    {it.date or 'undated'}\n")
            fh.write(f"  Place:   {it.place or 'not recorded by the archive'}\n")
            fh.write(f"  Creator: {it.creator or 'unknown'}\n")
            fh.write(f"  License: {it.rights} [{it.rights_tier}]\n")
            fh.write(f"  Credit:  {credit_line(it)}\n")
            fh.write(f"  Source:  {it.item_page_url}\n")
            fh.write(f"  Scrape:  {bucket} ({it.item_id})\n\n")
        if unmatched:
            fh.write("UNMATCHED — in no scrape manifest, chase by hand\n")
            fh.write("-" * 60 + "\n")
            for name in unmatched:
                fh.write(f"  {name}\n")

    log.info("report written: %s and %s (%d matched, %d unmatched)",
             csv_path, txt_path, len(matched), len(unmatched))
    if unmatched:
        log.warning("%d file(s) matched no manifest — see the UNMATCHED "
                    "section in %s", len(unmatched), txt_path)
    return csv_path, txt_path
