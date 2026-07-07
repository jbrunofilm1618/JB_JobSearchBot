"""
grid-archive-scraper — tunable configuration.

This is the ONE file you edit to tune a run. Query lists, date bounds,
polite delays, and the clip-size cap all live here. Nothing below this file's
comments is magic; change a list, rerun `search`, and the manifest updates.

Item ids are namespaced by source ("loc:..." / "ia:...") so nothing collides
and dedup is exact.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Date window (inclusive). ISO 8601. Applied to every query on every source.
# --------------------------------------------------------------------------- #
START_DATE = "1930-01-01"
END_DATE = "1959-12-31"

# --------------------------------------------------------------------------- #
# Politeness / rate limiting. The archives throttle anonymous traffic; these
# keep us under the radar. Delays are seconds.
# --------------------------------------------------------------------------- #
BASE_DELAY_SECONDS = 1.5          # polite pause between requests (1-2s)
MAX_RETRIES = 5                   # on 429 / CAPTCHA / transient network error
BACKOFF_FACTOR = 2.0             # exponential: delay * factor**attempt
BACKOFF_CAP_SECONDS = 60.0        # never sleep longer than this
RESULTS_PER_PAGE = 25             # LOC c= / IA rows=
MAX_PAGES_PER_QUERY = 4           # never paginate deep; keep queries narrow

# --------------------------------------------------------------------------- #
# Video handling
# --------------------------------------------------------------------------- #
MAX_CLIP_MB = 100                 # skip MP4 derivatives larger than this
FRAMES_PER_CLIP = 6               # evenly spaced frames sampled per film

# --------------------------------------------------------------------------- #
# Judge pass (Claude). Requires ANTHROPIC_API_KEY in the environment.
# --------------------------------------------------------------------------- #
JUDGE_MODEL = "claude-sonnet-4-6"     # change to taste; see README
JUDGE_ITEMS_PER_REQUEST = 4           # batch a few items per API call to control cost
JUDGE_MAX_TOKENS = 1500

# The creative brief handed to Claude verbatim on every judge request.
CREATIVE_BRIEF = """\
I am sourcing archival material for a 60-second brand film about the American
electric grid buildout, 1930s through 1950s. I want a positive, forward-looking
register: linemen raising poles, crews stringing wire, farm families at the
switch throwing on their first electric light, dams and turbine halls, and city
lights at night. I need locked-off compositions that can hold on screen for 1.5
to 2 seconds. I do NOT want decay, ruin, blackout, or disaster imagery.
Score each item 1 (irrelevant) to 5 (hero shot) for this specific film.
"""

# --------------------------------------------------------------------------- #
# Query matrix. Each entry is (group, query_text). `group` buckets the item on
# the contact sheet: "rural", "urban", or "big_machine".
#
# Every query runs against BOTH photos and film on BOTH sources (subject to the
# LOC-only collection/contributor boosts below). Results are deduped by item id.
# --------------------------------------------------------------------------- #
QUERIES = [
    # --- Rural buildout -------------------------------------------------- #
    ("rural", "rural electrification"),
    ("rural", "Rural Electrification Administration"),
    ("rural", "REA cooperative"),
    ("rural", "electric cooperative"),
    ("rural", "power line construction"),
    ("rural", "transmission line"),
    ("rural", "lineman"),
    ("rural", "utility pole"),
    ("rural", "electric light farm"),
    ("rural", "farmhouse electricity"),
    # --- Big machine / infrastructure ------------------------------------ #
    ("big_machine", "TVA"),
    ("big_machine", "Norris Dam"),
    ("big_machine", "Bonneville Power Administration"),
    ("big_machine", "Grand Coulee"),
    ("big_machine", "power plant"),
    ("big_machine", "substation"),
    ("big_machine", "generator"),
    ("big_machine", "powerhouse"),
    # --- Urban / night --------------------------------------------------- #
    ("urban", "city lights"),
    ("urban", "street lighting"),
    ("urban", "skyline night"),
    ("urban", "neon"),
]

# LOC-only collection boosts, tried as `fa=` filters. The first is the FSA/OWI
# motherlode (1935-1944 b&w negatives); if it returns nothing we fall back to
# the plain-language collection name.
LOC_COLLECTION_BOOSTS = [
    "partof:fsa/owi black-and-white negatives",
]
LOC_COLLECTION_FALLBACK = "partof:farm security administration"

# LOC-only contributor (photographer) boosts. Each is tried as a `fa=` filter
# alongside the rural query set — these are the FSA photographers who shot the
# electrification story.
LOC_CONTRIBUTOR_BOOSTS = [
    "contributor:lange, dorothea",
    "contributor:rothstein, arthur",
    "contributor:lee, russell",
    "contributor:vachon, john",
    "contributor:post wolcott, marion",
]

# Internet Archive: collections to scope film searches to, plus a hard-coded
# high-value target pulled in on every run.
IA_COLLECTIONS = ["prelinger", "FedFlix"]
IA_ALWAYS_INCLUDE_TITLES = ["Power and the Land"]  # 1940, Joris Ivens, REA

# --------------------------------------------------------------------------- #
# Output / working directories (relative to the current working directory).
# --------------------------------------------------------------------------- #
OUTPUT_DIR = "."
CACHE_DIR = "cache"               # cached API responses + downloads, keyed by id
PREVIEW_DIR = "previews"          # medium-res preview JPEGs
CLIP_DIR = "previews/clips"       # capped MP4 derivatives
FRAME_DIR = "previews/frames"     # sampled filmstrip frames
MASTER_DIR = "masters"            # highest-res assets (fetch-masters)
LOG_FILE = "grid_archive.log"

MANIFEST_CSV = "manifest.csv"
MANIFEST_JSON = "manifest.json"
SHORTLIST_JSON = "shortlist.json"
CONTACT_SHEET_HTML = "contact_sheet.html"

# HTTP identity. A descriptive UA is courteous and reduces the odds of a block.
USER_AGENT = (
    "grid-archive-scraper/1.0 (filmmaker research; "
    "https://github.com/jbrunofilm1618/grid-archive-scraper)"
)
