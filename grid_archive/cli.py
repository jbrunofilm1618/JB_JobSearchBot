"""Command-line entry point.

Subcommands:
  search         run the query matrix, dedupe, write manifest.csv / manifest.json
  preview        download preview JPEGs + capped MP4 clips, sample filmstrip frames
  sheet          render contact_sheet.html
  judge          score items against the creative brief via the Claude API
  fetch-masters  pull highest-res assets for selected item ids
  run            search -> preview -> sheet in one go (no judge)

Run `python -m grid_archive <subcommand> --help` for options.
"""

from __future__ import annotations

import argparse
import os

import config


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a local .env into os.environ if present.

    Never overrides a variable already set in the real environment, so an
    explicit `export` still wins. Kept dependency-free on purpose. The .env file
    is gitignored — put secrets like ANTHROPIC_API_KEY here and they stay local.
    """
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grid-archive-scraper",
        description="Source archival photos and film of the American electric "
                    "grid buildout (1930s-1950s) from LOC + Internet Archive.")
    p.add_argument("--no-cache", action="store_true",
                   help="ignore cached API responses (still writes them)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("search", help="run the query matrix and write the manifest")

    pv = sub.add_parser("preview", help="download previews/clips and sample frames")
    pv.add_argument("--max-clip-mb", type=int, default=config.MAX_CLIP_MB,
                    help=f"skip MP4 derivatives larger than this (default {config.MAX_CLIP_MB})")

    sub.add_parser("sheet", help="render contact_sheet.html from the manifest")

    jd = sub.add_parser("judge", help="score items against the brief via Claude")
    jd.add_argument("--rejudge", action="store_true",
                    help="re-score items that already have a judge score")

    fm = sub.add_parser("fetch-masters", help="pull highest-res assets for selected ids")
    fm.add_argument("--ids", nargs="*", help="item ids to fetch (e.g. loc:2017... ia:...)")
    fm.add_argument("--ids-file", help="file with one item id per line")

    rn = sub.add_parser("run", help="search -> preview -> sheet")
    rn.add_argument("--max-clip-mb", type=int, default=config.MAX_CLIP_MB)

    return p


def main(argv=None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    use_cache = not args.no_cache

    if args.command == "search":
        from .search import run_search
        run_search(use_cache=use_cache)
    elif args.command == "preview":
        from .preview import run_preview
        run_preview(args.max_clip_mb, use_cache=use_cache)
    elif args.command == "sheet":
        from .sheet import run_sheet
        run_sheet()
    elif args.command == "judge":
        from .judge import run_judge
        run_judge(rejudge=args.rejudge)
    elif args.command == "fetch-masters":
        from .masters import run_fetch_masters
        run_fetch_masters(ids=args.ids, ids_file=args.ids_file, use_cache=use_cache)
    elif args.command == "run":
        from .search import run_search
        from .preview import run_preview
        from .sheet import run_sheet
        run_search(use_cache=use_cache)
        run_preview(args.max_clip_mb, use_cache=use_cache)
        run_sheet()
    return 0
