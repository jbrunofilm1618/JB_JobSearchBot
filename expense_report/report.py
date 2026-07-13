"""Render the expense report: a self-contained static HTML page (no server,
no build step, same approach as grid_archive/sheet.py) plus machine-readable
report.csv / report.json, all under expense_data/reports/.

Page layout: summary tiles -> open flags (grouped by kind, severity badges,
dismiss command shown per flag) -> month x category matrix -> transactions
grouped by category with expandable itemized line items. Pure client-side JS
filters: month, category, flagged-only, unmatched-only.
"""

from __future__ import annotations

import csv
import html
import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

import expense_config as config
from .logging_setup import get_logger
from .models import Transaction, Receipt, Flag, cents_to_dollars
from . import store

log = get_logger()

_SEVERITY_ORDER = {"high": 0, "warn": 1, "info": 2}
_KIND_LABELS = [("fraud", "Possible fraud"),
                ("redundancy", "Redundancy"),
                ("reconciliation", "Reconciliation")]


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _dollars(cents) -> str:
    if cents is None:
        return ""
    return ("-$" if cents < 0 else "$") + cents_to_dollars(abs(cents))


def _flag_card(f: Flag, txn_by_id: Dict[str, Transaction],
               receipt_by_id: Dict[str, Receipt]) -> str:
    refs = []
    for tid in f.txn_ids:
        t = txn_by_id.get(tid)
        if t:
            refs.append(f"{_e(t.date)} · {_e(t.merchant)} · {_dollars(t.amount_cents)}")
    for rid in f.receipt_ids:
        r = receipt_by_id.get(rid)
        if r:
            refs.append(f"{_e(r.email_date)} · email: {_e(r.subject)[:60]}")
    llm = ""
    if f.llm_reviewed:
        verdict = "agrees" if f.llm_agrees else "thinks this is fine"
        llm = (f'<div class="llm">Claude {verdict}: {_e(f.llm_reason)}</div>')
    return f"""
<div class="flag {f.severity}" data-rule="{_e(f.rule)}">
  <span class="badge {f.severity}">{_e(f.severity.upper())}</span>
  <span class="rule">{_e(f.rule)}</span>
  <div class="detail">{_e(f.detail)}</div>
  <div class="refs">{"<br>".join(refs)}</div>{llm}
  <div class="dismiss">dismiss: <code>expense-report dismiss {_e(f.flag_id)}</code></div>
</div>"""


def _txn_card(t: Transaction, receipt_by_id: Dict[str, Receipt],
              flagged_ids: set) -> str:
    receipt = receipt_by_id.get(t.matched_receipt_id)
    items_html = ""
    if receipt and receipt.line_items:
        rows = "".join(
            f"<tr><td>{_e(li.get('description'))}"
            f"{' × ' + _e(int(li['quantity'])) if li.get('quantity') else ''}</td>"
            f"<td class='amt'>{_dollars(li.get('amount_cents'))}</td></tr>"
            for li in receipt.line_items)
        items_html = (f"<details><summary>{len(receipt.line_items)} line "
                      f"item(s)</summary><table>{rows}</table></details>")
    match_note = ("matched receipt" if t.matched_receipt_id else
                  '<span class="unmatched">no receipt</span>')
    alert_note = ' <span class="prov">alert</span>' if t.source == "alert" else ""
    flag_note = ' <span class="flagged">⚑</span>' if t.txn_id in flagged_ids else ""
    return f"""
<div class="txn" data-month="{_e(t.month)}" data-category="{_e(t.category)}"
     data-flagged="{'1' if t.txn_id in flagged_ids else '0'}"
     data-matched="{'1' if t.matched_receipt_id else '0'}">
  <span class="date">{_e(t.date)}</span>
  <span class="merchant">{_e(t.merchant)}{alert_note}{flag_note}</span>
  <span class="amt">{_dollars(t.amount_cents)}</span>
  <span class="note">{match_note}</span>
  {items_html}
</div>"""


_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { background:#14161a; color:#d8dce2; font:14px/1.5 -apple-system,
       BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; margin:0; padding:24px; }
h1 { font-size:20px; } h2 { font-size:16px; margin-top:32px;
     border-bottom:1px solid #2a2e35; padding-bottom:6px; }
.tiles { display:flex; gap:12px; flex-wrap:wrap; margin:16px 0; }
.tile { background:#1c1f25; border:1px solid #2a2e35; border-radius:8px;
        padding:12px 18px; min-width:130px; }
.tile .n { font-size:22px; font-weight:600; display:block; }
.tile .l { color:#8b93a1; font-size:12px; }
.controls { display:flex; gap:10px; flex-wrap:wrap; margin:14px 0;
            align-items:center; }
select, .toggle { background:#1c1f25; color:#d8dce2; border:1px solid #2a2e35;
                  border-radius:6px; padding:5px 9px; font-size:13px; }
.toggle.on { border-color:#5b9dd9; color:#9ecbff; }
.flag { background:#1c1f25; border:1px solid #2a2e35; border-left:4px solid;
        border-radius:6px; padding:10px 14px; margin:8px 0; }
.flag.high { border-left-color:#e5534b; } .flag.warn { border-left-color:#d9a03f; }
.flag.info { border-left-color:#5b9dd9; }
.badge { font-size:10px; font-weight:700; padding:2px 7px; border-radius:9px;
         margin-right:8px; }
.badge.high { background:#4a1d1a; color:#ff8a80; }
.badge.warn { background:#41351a; color:#ffd27a; }
.badge.info { background:#1a2c41; color:#9ecbff; }
.rule { color:#8b93a1; font-size:12px; letter-spacing:.4px; }
.detail { margin-top:6px; } .refs { color:#8b93a1; font-size:12px; margin-top:4px; }
.llm { color:#9ecbff; font-size:12px; margin-top:4px; }
.dismiss { color:#5d6470; font-size:11px; margin-top:6px; }
code { background:#14161a; padding:1px 5px; border-radius:4px; }
table.matrix { border-collapse:collapse; margin-top:10px; }
table.matrix td, table.matrix th { border:1px solid #2a2e35; padding:5px 12px;
        text-align:right; font-size:13px; }
table.matrix th { color:#8b93a1; font-weight:500; }
table.matrix td:first-child, table.matrix th:first-child { text-align:left; }
.cat h3 { font-size:14px; margin:18px 0 6px; color:#b7bec9; }
.txn { display:flex; gap:14px; flex-wrap:wrap; align-items:baseline;
       padding:6px 10px; border-bottom:1px solid #21252c; }
.txn .date { color:#8b93a1; min-width:88px; }
.txn .merchant { flex:1; min-width:180px; }
.txn .amt { font-variant-numeric:tabular-nums; min-width:90px; text-align:right; }
.txn .note { color:#5d6470; font-size:12px; min-width:110px; }
.unmatched { color:#d9a03f; } .flagged { color:#e5534b; }
.prov { font-size:10px; color:#9ecbff; border:1px solid #2a4a6b;
        border-radius:6px; padding:0 5px; }
details { width:100%; } details table { margin:4px 0 4px 100px; }
details td { padding:2px 10px; color:#a7aeba; font-size:13px; }
details td.amt { text-align:right; }
summary { cursor:pointer; color:#8b93a1; font-size:12px; }
.hidden { display:none !important; }
.empty { color:#5d6470; }
"""

_JS = """
function applyFilters() {
  const month = document.getElementById('f-month').value;
  const cat = document.getElementById('f-cat').value;
  const flagged = document.getElementById('f-flagged').classList.contains('on');
  const unmatched = document.getElementById('f-unmatched').classList.contains('on');
  document.querySelectorAll('.txn').forEach(el => {
    let show = true;
    if (month && el.dataset.month !== month) show = false;
    if (cat && el.dataset.category !== cat) show = false;
    if (flagged && el.dataset.flagged !== '1') show = false;
    if (unmatched && el.dataset.matched !== '0') show = false;
    el.classList.toggle('hidden', !show);
  });
  document.querySelectorAll('.cat').forEach(sec => {
    const any = sec.querySelectorAll('.txn:not(.hidden)').length > 0;
    sec.classList.toggle('hidden', !any);
  });
}
function toggle(id) {
  document.getElementById(id).classList.toggle('on');
  applyFilters();
}
"""


def render_html(ledger: store.Ledger) -> str:
    txns = sorted(ledger.active_transactions(), key=lambda t: t.date,
                  reverse=True)
    txn_by_id = ledger.txn_by_id()
    receipt_by_id = ledger.receipt_by_id()
    open_flags = sorted((f for f in ledger.flags if f.status == "open"),
                        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.rule))
    flagged_ids = {tid for f in open_flags for tid in f.txn_ids}

    spend = sum(t.amount_cents for t in txns if t.amount_cents > 0)
    credits = sum(-t.amount_cents for t in txns if t.amount_cents < 0)
    matched = sum(1 for t in txns if t.matched_receipt_id)
    months = sorted({t.month for t in txns if t.month}, reverse=True)
    categories = sorted({t.category or config.DEFAULT_CATEGORY for t in txns})

    tiles = f"""
<div class="tiles">
  <div class="tile"><span class="n">{_dollars(spend)}</span>
      <span class="l">total spend</span></div>
  <div class="tile"><span class="n">{_dollars(credits)}</span>
      <span class="l">credits / refunds</span></div>
  <div class="tile"><span class="n">{len(txns)}</span>
      <span class="l">transactions</span></div>
  <div class="tile"><span class="n">{len(ledger.receipts)}</span>
      <span class="l">receipts scanned</span></div>
  <div class="tile"><span class="n">{matched}/{len(txns)}</span>
      <span class="l">matched to receipts</span></div>
  <div class="tile"><span class="n">{len(open_flags)}</span>
      <span class="l">open flags</span></div>
</div>"""

    flags_html = ""
    for kind, label in _KIND_LABELS:
        group = [f for f in open_flags if f.kind == kind]
        if not group:
            continue
        cards = "".join(_flag_card(f, txn_by_id, receipt_by_id) for f in group)
        flags_html += f"<h2>{_e(label)} ({len(group)})</h2>{cards}"
    if not flags_html:
        flags_html = '<h2>Flags</h2><p class="empty">Nothing flagged. 🎉</p>'

    # Month x category matrix
    matrix = defaultdict(lambda: defaultdict(int))
    for t in txns:
        if t.amount_cents > 0:
            matrix[t.month][t.category or config.DEFAULT_CATEGORY] += t.amount_cents
    header = "".join(f"<th>{_e(c)}</th>" for c in categories)
    rows = ""
    for month in months:
        cells = "".join(
            f"<td>{_dollars(matrix[month][c]) if matrix[month][c] else '·'}</td>"
            for c in categories)
        total = sum(matrix[month].values())
        rows += (f"<tr><th>{_e(month)}</th>{cells}"
                 f"<td><b>{_dollars(total)}</b></td></tr>")
    matrix_html = (f'<h2>Spend by month × category</h2>'
                   f'<div style="overflow-x:auto"><table class="matrix">'
                   f"<tr><th></th>{header}<th>total</th></tr>{rows}</table></div>"
                   if rows else "")

    month_opts = '<option value="">all months</option>' + "".join(
        f'<option>{_e(m)}</option>' for m in months)
    cat_opts = '<option value="">all categories</option>' + "".join(
        f'<option>{_e(c)}</option>' for c in categories)

    by_cat = defaultdict(list)
    for t in txns:
        by_cat[t.category or config.DEFAULT_CATEGORY].append(t)
    txns_html = ""
    for cat in categories:
        cards = "".join(_txn_card(t, receipt_by_id, flagged_ids)
                        for t in by_cat[cat])
        subtotal = sum(t.amount_cents for t in by_cat[cat] if t.amount_cents > 0)
        txns_html += (f'<div class="cat"><h3>{_e(cat)} · {_dollars(subtotal)}'
                      f"</h3>{cards}</div>")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Expense report</title>
<style>{_CSS}</style></head>
<body>
<h1>Expense report <span style="color:#5d6470;font-size:13px">generated {generated}
— local data only, nothing leaves this machine</span></h1>
{tiles}
{flags_html}
{matrix_html}
<h2>Transactions</h2>
<div class="controls">
  <select id="f-month" onchange="applyFilters()">{month_opts}</select>
  <select id="f-cat" onchange="applyFilters()">{cat_opts}</select>
  <button class="toggle" id="f-flagged" onclick="toggle('f-flagged')">⚑ flagged only</button>
  <button class="toggle" id="f-unmatched" onclick="toggle('f-unmatched')">no receipt only</button>
</div>
{txns_html}
<script>{_JS}</script>
</body></html>"""


def run_report() -> None:
    ledger = store.load_ledger()
    reports_dir = config.data_path(config.REPORTS_DIRNAME)
    os.makedirs(reports_dir, exist_ok=True)

    html_path = os.path.join(reports_dir, "expense_report.html")
    tmp = html_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(render_html(ledger))
    os.replace(tmp, html_path)

    # Machine-readable snapshots.
    json_path = os.path.join(reports_dir, "report.json")
    tmp = json_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({
            "generated": datetime.now().isoformat(timespec="seconds"),
            "transactions": [t.to_dict() for t in ledger.active_transactions()],
            "receipts": [r.to_dict() for r in ledger.receipts],
            "flags": [f.to_dict() for f in ledger.flags],
        }, fh, indent=2, default=str)
    os.replace(tmp, json_path)

    csv_path = os.path.join(reports_dir, "report.csv")
    tmp = csv_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "merchant", "amount", "category", "month",
                         "provider", "source", "matched_receipt", "order_id",
                         "flags"])
        flag_index = defaultdict(list)
        for f in ledger.flags:
            if f.status == "open":
                for tid in f.txn_ids:
                    flag_index[tid].append(f.rule)
        receipt_by_id = ledger.receipt_by_id()
        for t in sorted(ledger.active_transactions(), key=lambda t: t.date):
            receipt = receipt_by_id.get(t.matched_receipt_id)
            writer.writerow([
                t.date, t.merchant, cents_to_dollars(t.amount_cents),
                t.category, t.month, t.provider, t.source,
                "yes" if t.matched_receipt_id else "no",
                receipt.order_id if receipt else "",
                ";".join(flag_index.get(t.txn_id, [])),
            ])
    os.replace(tmp, csv_path)

    log.info("report written: %s (+ report.json, report.csv)", html_path)
