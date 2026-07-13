"""Command-line entry point for the expense report builder.

Subcommands:
  scan-email   scan configured IMAP accounts (iCloud, Gmail) for receipt and
               Amex alert emails; parse into the ledger
  import-csv   import Amex CSV export(s); also auto-discovers new files in
               the statements/ directory
  import-pdf   parse Amex PDF statement(s) and cross-verify the CSV data
  match        reconcile receipts against transactions
  flags        run the redundancy / reconciliation / fraud rule set
  report       render expense_report.html + report.csv/json
  run          scan-email -> import-csv -> import-pdf -> match -> flags -> report

Secrets come from the environment, macOS Keychain, or the gitignored .env
(see .env.example). All data stays under the gitignored expense_data/.
"""

from __future__ import annotations

import argparse

import expense_config as config
from grid_archive.cli import load_dotenv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="expense-report",
        description="Itemized expense reports from iCloud/Gmail receipts and "
                    "Amex statements, with redundancy and fraud flagging.")
    sub = p.add_subparsers(dest="command", required=True)

    date_help = "ISO date YYYY-MM-DD"

    se = sub.add_parser("scan-email", help="scan IMAP accounts for receipts")
    se.add_argument("--account", help="only this account label (icloud, gmail)")
    se.add_argument("--folder", help="mailbox folder (default: per-account config)")
    se.add_argument("--since", help=date_help)
    se.add_argument("--until", help=date_help)
    se.add_argument("--limit", type=int, help="stop after N new messages per folder")
    se.add_argument("--no-cache", action="store_true",
                    help="re-fetch and re-parse messages already in the cache")
    se.add_argument("--llm", action="store_true",
                    help="use Claude to itemize receipts the rules couldn't parse")

    ic = sub.add_parser("import-csv",
                        help="import card CSV export(s) — Amex or Capital One")
    ic.add_argument("paths", nargs="*",
                    help=f"CSV files (default: new files in {config.STATEMENTS_DIR}/)")
    ic.add_argument("--column-map", nargs="*", default=[], metavar="FIELD=HEADER",
                    help='override a column mapping, e.g. date="Trans Date"')

    ip = sub.add_parser("import-pdf", help="cross-verify against PDF statement(s)")
    ip.add_argument("paths", nargs="*",
                    help=f"PDF files (default: new files in {config.STATEMENTS_DIR}/)")

    ma = sub.add_parser("match", help="reconcile receipts with transactions")
    ma.add_argument("--rematch", action="store_true",
                    help="clear existing links and match from scratch")

    fl = sub.add_parser("flags", help="run redundancy/reconciliation/fraud rules")
    fl.add_argument("--llm", action="store_true",
                    help="ask Claude for a second opinion on warn/high flags")

    sub.add_parser("report", help="render expense_report.html + report.csv/json")

    di = sub.add_parser("dismiss", help="dismiss flag(s) by id")
    di.add_argument("flag_ids", nargs="+")

    rn = sub.add_parser("run", help="full pipeline: scan -> import -> match -> flags -> report")
    rn.add_argument("--account", help="only this account label")
    rn.add_argument("--since", help=date_help)
    rn.add_argument("--until", help=date_help)
    rn.add_argument("--csv", nargs="*", default=None, help="CSV paths (default: statements/)")
    rn.add_argument("--pdf", nargs="*", default=None, help="PDF paths (default: statements/)")
    rn.add_argument("--llm", action="store_true", help="enable both Claude passes")

    return p


def main(argv=None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    if args.command == "scan-email":
        from .imap_client import run_scan
        run_scan(account=args.account, folder=args.folder, since=args.since,
                 until=args.until, limit=args.limit,
                 use_cache=not args.no_cache, use_llm=args.llm)
    elif args.command == "import-csv":
        from .card_csv import run_import_csv
        run_import_csv(paths=args.paths, column_overrides=args.column_map)
    elif args.command == "import-pdf":
        from .card_pdf import run_import_pdf
        run_import_pdf(paths=args.paths)
    elif args.command == "match":
        from .match import run_match
        run_match(rematch=args.rematch)
    elif args.command == "flags":
        from .flags import run_flags
        run_flags(use_llm=args.llm)
    elif args.command == "report":
        from .report import run_report
        run_report()
    elif args.command == "dismiss":
        from .flags import run_dismiss
        run_dismiss(args.flag_ids)
    elif args.command == "run":
        from .imap_client import run_scan
        from .card_csv import run_import_csv
        from .card_pdf import run_import_pdf
        from .match import run_match
        from .flags import run_flags
        from .report import run_report
        run_scan(account=args.account, folder=None, since=args.since,
                 until=args.until, limit=None, use_cache=True, use_llm=args.llm)
        run_import_csv(paths=args.csv or [], column_overrides=[])
        run_import_pdf(paths=args.pdf or [])
        run_match(rematch=False)
        run_flags(use_llm=args.llm)
        run_report()
    return 0
