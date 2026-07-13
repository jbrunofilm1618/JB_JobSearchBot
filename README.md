# grid-archive-scraper

> This repo also hosts **expense-report**, an itemized expense report builder
> that scans iCloud/Gmail receipts and Amex/Capital One statements with
> redundancy + fraud flagging. See [EXPENSE_REPORT.md](EXPENSE_REPORT.md).

Source archival **photos and film of the American electric grid being built** —
rural electrification and big-city power, roughly **1930–1959** — from the
**Library of Congress** and the **Internet Archive**, into a reviewable
**contact sheet** and a **manifest of possibilities you can check yourself**.

Built for a filmmaker sourcing material for a 60-second brand film. The output
is meant to be *reviewed*, not blindly trusted: a dark HTML contact sheet with
thumbnails and filmstrips grouped rural / urban / big-machine, plus
`manifest.csv` and `manifest.json` carrying every rights statement and license
so nothing is silently dropped.

Photos and film are first-class citizens of one combined run — format is a
column, not a separate tool.

---

## Install

```bash
pip install -r requirements.txt          # requests (core)
pip install anthropic                     # only for the optional `judge` pass
```

`ffmpeg` and `ffprobe` must be on your PATH for film frame sampling (clips still
download without them; you just won't get filmstrips).

Python 3.9+.

## One-command local setup

```bash
git clone https://github.com/jbrunofilm1618/JB_JobSearchBot
cd JB_JobSearchBot
git checkout claude/archive-scraper-restructure-qefxhm
./setup.sh          # venv + deps + ffmpeg check + smoke test
```

Then activate the venv and run (see below):

```bash
source .venv/bin/activate
python -m grid_archive run
```

## Quick start

```bash
# 1. Run the query matrix across both sources, photos + film. Writes the manifest.
python -m grid_archive search

# 2. Download medium previews + capped MP4 clips, sample 6 frames per film.
python -m grid_archive preview --max-clip-mb 100

# 3. Build the contact sheet.
python -m grid_archive sheet
# -> open contact_sheet.html

# (or do all three at once)
python -m grid_archive run
```

Then, optionally, let Claude triage against the creative brief:

```bash
export ANTHROPIC_API_KEY=sk-...
python -m grid_archive judge          # writes shortlist.json, merges scores into the manifest
python -m grid_archive sheet          # re-render to pick up scores + shortlist toggle
```

Prefer not to `export` every time? Drop the key in a local `.env` (copy
`.env.example`) — the CLI auto-loads it, it's gitignored, and an exported
variable still overrides it:

```bash
cp .env.example .env      # then edit .env and paste your key
```

Finally, pull full-resolution masters for only the shots you chose:

```bash
python -m grid_archive fetch-masters                       # defaults to the judged shortlist
python -m grid_archive fetch-masters --ids loc:2017878331 ia:PowerAndTheLand1940
python -m grid_archive fetch-masters --ids-file selected.txt
```

If you installed with `pip install .`, a `grid-archive-scraper` console command
is available as a shorthand for `python -m grid_archive`.

## Everything is resumable

API responses and downloads are cached under `cache/`, keyed so reruns are
cheap. Preview/judge checkpoint the manifest as they go. Re-running `search`
with an expanded query list only *adds* items — it never discards judge scores
or preview paths you already have. Deduplication is by namespaced item id
(`loc:…` / `ia:…`) across every query and both sources.

## Tuning

Everything you'd want to change lives at the top of **`config.py`**: the query
matrix (grouped rural / urban / big-machine), the 1930–1959 date window, polite
request delays and retry/backoff, the MP4 size cap and frames-per-clip, the
LOC collection/contributor boosts, the Internet Archive collections and
always-include titles, and the judge model + creative brief.

## What each subcommand does

| Command | Output |
|---|---|
| `search` | `manifest.csv`, `manifest.json` — item id, title, date, creator/photographer, collection, source, rights/license, item page URL, best download URL, format, duration, judge score/reasoning (once judged), and the query that found it. Deduped across queries and sources. |
| `preview` | Medium-res preview JPEGs into `previews/`; capped MP4 derivatives into `previews/clips/`; 6 evenly-spaced frames per film into `previews/frames/`. Films with no MP4 derivative fall back to a thumbnail and are flagged **"not digitized for streaming"** so you know to source them elsewhere. |
| `sheet` | `contact_sheet.html` — one static dark page, thumbnails and filmstrips grouped rural / urban / big-machine, each linking to its LOC or Internet Archive page, with title, date, photographer, rights, and judge score. Toggles: media-type filter (all / photos / film), sort-by-judge-score, shortlist-only. |
| `judge` | Sends each item's frames/image to Claude with the creative brief; gets keep/skip + 1–5 score + one line of reasoning. Writes `shortlist.json`, merges scores into the manifest. Skip verdicts on long films are treated as provisional — the manifest keeps everything. |
| `fetch-masters` | Highest-resolution asset (LOC TIFF/derivative, IA original, NARA digital object, Wikimedia original) for only the selected item ids, into `masters/`. DPLA rows point to the holding institution instead. |

## Sources

- **Library of Congress** — `loc.gov` JSON API, no key. Photos via `/photos/`,
  film via `/film-and-videos/`, full download URLs via `/item/{id}/`. FSA/OWI
  black-and-white negatives and named FSA photographers (Lange, Rothstein, Lee,
  Vachon, Post Wolcott) are boosted; film also searches the National Screening
  Room. Anonymous rate limits and HTML CAPTCHA interstitials are handled with a
  polite base delay and exponential backoff.
- **Internet Archive** — `advancedsearch` + `metadata` endpoints, no key.
  Scoped to the `prelinger` and `FedFlix` collections; *Power and the Land*
  (1940, Joris Ivens, REA) is pulled in on every run. `licenseurl` captured per
  item.
- **National Archives (NARA)** — `catalog.archives.gov` API v2. US-government
  public-domain moving images and photographs (TVA, REA, WPA); digital objects
  are the hi-res masters. Runs anonymously; if the host returns `403`, set a free
  `NARA_API_KEY` (from api.data.gov) and rerun.
- **Wikimedia Commons** — MediaWiki API, no key. Freely-licensed historical
  photos and some film, full-res originals, explicit license per file. Only
  freely-reusable files are kept (`WIKIMEDIA_FREE_ONLY`); the license is always
  recorded. Dates are checked client-side, so undated files are kept and flagged.
- **DPLA** — `api.dp.la` v2, **free key required** (`DPLA_API_KEY`; request one
  at <https://pro.dp.la/developers/policies>). One API over 4000+ US
  repositories. DPLA is a discovery layer: rows carry metadata, a thumbnail, and
  a link to the holding institution — the hi-res master lives at that
  institution, so DPLA film rows are flagged as leads and `fetch-masters` can't
  pull their originals directly. Skipped with a warning if no key is set.

All sources sit behind a common `Fetcher` interface
(`grid_archive/fetchers/base.py`) and are registered in
`grid_archive/search.build_fetchers()` — adding another (e.g. Europeana) is one
subclass plus one line. Any source needing an API key it doesn't have is skipped
with a warning, never a crash.

### API keys

Put keys in your shell or a local `.env` (auto-loaded, gitignored):

```bash
DPLA_API_KEY=...      # required for DPLA
NARA_API_KEY=...      # optional; only if NARA returns 403
ANTHROPIC_API_KEY=... # only for the judge pass
```

## Notes

- **Digitized only.** Items with no image or resource are skipped; every kept
  item carries its rights statement (LOC) or license URL (IA) into the manifest.
- **Politeness.** Queries stay narrow and shallow (no deep pagination). Every
  request URL and every 429 is logged to `grid_archive.log`.
- The judge model defaults to `claude-sonnet-4-6` in `config.py`; change
  `JUDGE_MODEL` to any model your API key can call.
