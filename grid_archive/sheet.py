"""`sheet` subcommand: render contact_sheet.html — one static, dark page with
thumbnails and filmstrips grouped rural / urban / big-machine.

Interactive controls (pure client-side JS, no server, no build step):
  * media-type filter: all / photos / film
  * sort by judge score
  * shortlist-only toggle
Each item links out to its LOC or Internet Archive page.
"""

from __future__ import annotations

import html
import os
from typing import List

import config
from .logging_setup import get_logger
from .models import Item
from . import manifest

log = get_logger()

_GROUP_LABELS = [
    # golden age (1930s-50s)
    ("rural", "Rural buildout"),
    ("urban", "Urban &amp; night"),
    ("big_machine", "Big machine / infrastructure"),
    # modern era
    ("digital", "Digital revolution"),
    ("clean_energy", "Clean energy at scale"),
    ("grid_modern", "The smarter grid"),
    ("motion", "Energy in motion"),
]


def _card_html(item: Item) -> str:
    title = html.escape(item.title or "(untitled)")
    date = html.escape(item.date or "n.d.")
    creator = html.escape(item.creator or "unknown")
    rights = html.escape(item.rights or "unspecified")
    page = html.escape(item.item_page_url or "#")
    score = "" if item.judge_score is None else str(item.judge_score)
    score_badge = f'<span class="score">★ {score}</span>' if score else ""
    reason = html.escape(item.judge_reason or "")
    note = html.escape(item.streaming_note or "")
    note_badge = f'<span class="note">{note}</span>' if note else ""

    # Media block: filmstrip for films with frames, else single image.
    if item.format == "film" and item.frame_paths:
        strip = "".join(
            f'<img loading="lazy" src="{html.escape(p)}" alt="frame">'
            for p in item.frame_paths)
        media = f'<div class="filmstrip">{strip}</div>'
    else:
        src = item.preview_path or item.thumbnail_url or ""
        media = (f'<img class="thumb" loading="lazy" src="{html.escape(src)}" alt="{title}">'
                 if src else '<div class="thumb missing">no preview</div>')

    fmt_badge = f'<span class="fmt {item.format}">{item.format}</span>'
    dur = ""
    if item.duration_seconds:
        m, s = divmod(int(item.duration_seconds), 60)
        dur = f'<span class="dur">{m}:{s:02d}</span>'

    keep = "1" if item.judge_keep else "0"
    sort_score = item.judge_score if item.judge_score is not None else -1

    return f"""
    <figure class="card" data-format="{item.format}" data-shortlist="{keep}" data-score="{sort_score}">
      <a href="{page}" target="_blank" rel="noopener">{media}</a>
      <figcaption>
        <div class="badges">{fmt_badge}{dur}{score_badge}{note_badge}</div>
        <div class="title"><a href="{page}" target="_blank" rel="noopener">{title}</a></div>
        <div class="meta">{date} &middot; {creator}</div>
        <div class="rights">{rights}</div>
        {f'<div class="reason">{reason}</div>' if reason else ''}
        <div class="src">{item.source} &middot; {html.escape(item.query or '')}</div>
      </figcaption>
    </figure>"""


def _group_section(group_key: str, label: str, items: List[Item]) -> str:
    cards = "\n".join(_card_html(it) for it in items)
    return f"""
    <section class="group" data-group="{group_key}">
      <h2>{label} <span class="count">{len(items)}</span></h2>
      <div class="grid">{cards}</div>
    </section>"""


_CSS = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0d0d0f; color: #e8e8ea;
         font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  header { position: sticky; top: 0; z-index: 10; background: #141418;
           border-bottom: 1px solid #26262c; padding: 14px 20px; }
  header h1 { margin: 0 0 8px; font-size: 18px; font-weight: 600; }
  .controls { display: flex; gap: 18px; flex-wrap: wrap; align-items: center; font-size: 13px; }
  .controls label { display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }
  .controls select, .controls button { background: #1e1e24; color: #e8e8ea;
           border: 1px solid #33333b; border-radius: 6px; padding: 4px 8px; }
  main { padding: 8px 20px 60px; }
  .group h2 { font-size: 15px; font-weight: 600; margin: 28px 0 12px;
              padding-bottom: 6px; border-bottom: 1px solid #26262c; }
  .group h2 .count { color: #7a7a85; font-weight: 400; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
  .card { margin: 0; background: #16161b; border: 1px solid #26262c; border-radius: 8px;
          overflow: hidden; display: flex; flex-direction: column; }
  .card a { color: inherit; text-decoration: none; }
  .thumb { width: 100%; height: 200px; object-fit: cover; display: block; background: #000; }
  .thumb.missing { display: flex; align-items: center; justify-content: center; color: #55555f; }
  .filmstrip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: #000; }
  .filmstrip img { width: 100%; height: 99px; object-fit: cover; display: block; }
  figcaption { padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; }
  .badges { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .fmt { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
         padding: 1px 6px; border-radius: 4px; }
  .fmt.photo { background: #23394d; color: #9ecbff; }
  .fmt.film { background: #4d2e23; color: #ffb98a; }
  .dur { font-size: 11px; color: #9a9aa3; }
  .score { font-size: 11px; color: #ffd479; }
  .note { font-size: 11px; color: #ff8a8a; }
  .title { font-weight: 600; font-size: 14px; }
  .title a:hover { text-decoration: underline; }
  .meta { color: #b6b6bd; font-size: 12px; }
  .rights { color: #7a7a85; font-size: 11px; }
  .reason { color: #cdcdd4; font-size: 12px; font-style: italic; border-left: 2px solid #33333b; padding-left: 8px; }
  .src { color: #55555f; font-size: 11px; }
  .empty { color: #7a7a85; padding: 40px 0; text-align: center; }
"""

_JS = """
  const state = { fmt: 'all', sort: 'group', shortlistOnly: false };
  function apply() {
    document.querySelectorAll('.group').forEach(group => {
      const grid = group.querySelector('.grid');
      let cards = Array.from(grid.querySelectorAll('.card'));
      let visible = 0;
      cards.forEach(c => {
        const okFmt = state.fmt === 'all' || c.dataset.format === state.fmt;
        const okShort = !state.shortlistOnly || c.dataset.shortlist === '1';
        const show = okFmt && okShort;
        c.style.display = show ? '' : 'none';
        if (show) visible++;
      });
      if (state.sort === 'score') {
        cards.sort((a, b) => Number(b.dataset.score) - Number(a.dataset.score))
             .forEach(c => grid.appendChild(c));
      }
      group.style.display = visible ? '' : 'none';
    });
  }
  document.getElementById('fmt').addEventListener('change', e => { state.fmt = e.target.value; apply(); });
  document.getElementById('sort').addEventListener('change', e => { state.sort = e.target.value; apply(); });
  document.getElementById('shortlist').addEventListener('change', e => { state.shortlistOnly = e.target.checked; apply(); });
  apply();
"""


def render_sheet(items: List[Item]) -> str:
    by_group: dict = {}
    for it in items:
        by_group.setdefault(it.group, []).append(it)

    # Known groups render in curated order with curated labels; any group key
    # not listed (a user-added query group) still renders, with a label derived
    # from the key — nothing is ever silently hidden. Empty groups are skipped.
    known = [k for k, _ in _GROUP_LABELS]
    labels = dict(_GROUP_LABELS)
    ordered = [k for k in known if by_group.get(k)] + \
              sorted(k for k in by_group if k not in known)
    sections = "\n".join(
        _group_section(k, labels.get(k, html.escape(k.replace("_", " ").title())),
                       by_group[k])
        for k in ordered)

    total = len(items)
    photos = sum(1 for it in items if it.format == "photo")
    films = total - photos

    return f"""<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>grid-archive-scraper contact sheet</title>
  <style>{_CSS}</style>
</head><body>
  <header>
    <h1>Grid buildout contact sheet <span style="color:#7a7a85;font-weight:400">
      &mdash; {total} items ({photos} photos, {films} film)</span></h1>
    <div class="controls">
      <label>Media
        <select id="fmt">
          <option value="all">all</option>
          <option value="photo">photos</option>
          <option value="film">film</option>
        </select>
      </label>
      <label>Sort
        <select id="sort">
          <option value="group">by group</option>
          <option value="score">by judge score</option>
        </select>
      </label>
      <label><input type="checkbox" id="shortlist"> shortlist only</label>
    </div>
  </header>
  <main>{sections}</main>
  <script>{_JS}</script>
</body></html>"""


def run_sheet() -> str:
    from datetime import datetime

    items = manifest.load_items()
    if not items:
        log.warning("no manifest found; run `search` first")
    html_out = render_sheet(items)
    path = os.path.join(config.OUTPUT_DIR, config.CONTACT_SHEET_HTML)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html_out)
    log.info("wrote %s (%d items)", path, len(items))

    # Timestamped snapshot so regenerating never loses a prior version. The
    # snapshot lives one level down in archive/, so a <base href="../"> makes
    # its relative previews/ image paths resolve against the project root.
    archive_dir = os.path.join(config.OUTPUT_DIR, config.SHEET_ARCHIVE_DIR)
    os.makedirs(archive_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot = os.path.join(archive_dir, f"contact_sheet_{stamp}.html")
    with open(snapshot, "w", encoding="utf-8") as fh:
        fh.write(html_out.replace("<head>", '<head><base href="../">', 1))
    log.info("archived snapshot: %s", snapshot)
    return path
