#!/usr/bin/env bash
# Local bootstrap for grid-archive-scraper.
# Creates a venv, installs deps, checks ffmpeg, and runs the offline test suite.
# Usage:  ./setup.sh
set -euo pipefail

cd "$(dirname "$0")"

echo "==> grid-archive-scraper local setup"

# --- Python ---------------------------------------------------------------- #
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.9+ (e.g. 'brew install python')." >&2
  exit 1
fi
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "    Python $PYV detected"

# --- Virtual environment --------------------------------------------------- #
if [ ! -d ".venv" ]; then
  echo "==> Creating virtualenv (.venv)"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet anthropic   # optional; only used by the `judge` subcommand

# --- ffmpeg ---------------------------------------------------------------- #
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  echo "    ffmpeg/ffprobe found"
else
  echo "    WARNING: ffmpeg/ffprobe not on PATH."
  echo "             Clips still download, but film frame sampling is skipped."
  echo "             Install with:  brew install ffmpeg"
fi

# --- .env ------------------------------------------------------------------ #
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo "    Created .env from template — add your ANTHROPIC_API_KEY for the judge pass."
fi

# --- Smoke test ------------------------------------------------------------ #
echo "==> Running offline test suite"
if python -m pytest tests/ -q 2>/dev/null; then
  :
else
  python tests/test_grid_archive.py
fi

cat <<'DONE'

==> Setup complete.

Next:
  source .venv/bin/activate
  python -m grid_archive run       # search -> preview -> contact_sheet.html
  open contact_sheet.html

Optional Claude triage pass (needs ANTHROPIC_API_KEY in .env or exported):
  python -m grid_archive judge
  python -m grid_archive sheet
DONE
