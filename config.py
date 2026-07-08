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
# Additional sources. Each is a Fetcher registered in
# grid_archive.search.build_fetchers(). Flip any OFF here to skip it.
#
# API keys are read from the ENVIRONMENT, never stored in this file — put them in
# your shell or a local .env (auto-loaded). The env var name is noted per source.
# --------------------------------------------------------------------------- #

# National Archives (NARA) Catalog API — US-government public-domain film & photos
# (TVA, REA, WPA). Digital objects ARE the hi-res masters.
NARA_ENABLED = True
NARA_API_BASE = "https://catalog.archives.gov/api/v2"
NARA_API_KEY_ENV = "NARA_API_KEY"   # free from api.data.gov; some deployments of the
                                    # v2 API require it. Runs anonymously if unset; if
                                    # requests come back 403, set this and rerun.

# Wikimedia Commons — freely-licensed historical photos + some film, full-res
# originals, explicit license per file. No key needed.
WIKIMEDIA_ENABLED = True
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"

# Digital Public Library of America — one API over 4000+ US repositories.
# REQUIRES a free key (request: https://pro.dp.la/developers/policies#get-a-key).
# Without the key this source is skipped (with a warning), not an error.
# Note: DPLA returns metadata + a thumbnail + a link to the holding institution;
# hi-res masters live at that institution, so DPLA rows are leads to chase, and
# `fetch-masters` cannot pull their originals directly.
DPLA_ENABLED = True
DPLA_API_BASE = "https://api.dp.la/v2"
DPLA_API_KEY_ENV = "DPLA_API_KEY"

# Only keep freely-reusable Wikimedia files (public-domain / CC). When True, files
# whose license doesn't look free are skipped. The license is always recorded.
WIKIMEDIA_FREE_ONLY = True

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
SHEET_ARCHIVE_DIR = "archive"     # every `sheet` run also snapshots here, timestamped

# HTTP identity. A descriptive UA is courteous and reduces the odds of a block.
USER_AGENT = (
    "grid-archive-scraper/1.0 (filmmaker research; "
    "https://github.com/jbrunofilm1618/grid-archive-scraper)"
)

# --------------------------------------------------------------------------- #
# Eras. The film has two chapters; each era swaps the date window, the query
# matrix, the LOC/IA boosts that only make sense for its period, and the
# creative brief the judge scores against. Select per run with `--era modern`
# (no file editing needed — the default stays the 1930s-50s buildout).
#
# Items from different eras coexist in one manifest (ids never collide) and the
# contact sheet groups them separately, so the whole film lives on one sheet.
# --------------------------------------------------------------------------- #

MODERN_CREATIVE_BRIEF = """\
I am sourcing material for the modern chapter of a 60-second brand film about
the American electric grid: the grid now needs MORE energy and SMARTER usage,
and the imagery should show the incredible progress of humankind. I want:
AI datacenters and server halls, robotics and automation, IoT and smart
devices, EV charging stations, solar panel fields, wind turbines on land and
offshore, modern transmission and grid control rooms — and fast-moving
light trails from vehicles at night (long exposures) suggesting progress and
energy in motion. Positive, forward-looking, awe register. Locked-off or slow-move
compositions that can hold 1.5 to 2 seconds on screen. No decay, no disaster,
no blackout imagery.
Score each item 1 (irrelevant) to 5 (hero shot) for this specific film.
"""

ERAS = {
    "golden_age": {
        # The values already defined above ARE the golden-age defaults; this
        # entry exists so `--era golden_age` can restore them after a modern run.
        "START_DATE": START_DATE,
        "END_DATE": END_DATE,
        "QUERIES": list(QUERIES),
        "CREATIVE_BRIEF": CREATIVE_BRIEF,
        "LOC_COLLECTION_BOOSTS": list(LOC_COLLECTION_BOOSTS),
        "LOC_CONTRIBUTOR_BOOSTS": list(LOC_CONTRIBUTOR_BOOSTS),
        "IA_ALWAYS_INCLUDE_TITLES": list(IA_ALWAYS_INCLUDE_TITLES),
    },
    "modern": {
        "START_DATE": "1995-01-01",
        "END_DATE": "2026-12-31",
        "QUERIES": [
            # --- The digital revolution ---------------------------------- #
            ("digital", "data center"),
            ("digital", "server room"),
            ("digital", "supercomputer"),
            ("digital", "fiber optic"),
            ("digital", "internet infrastructure"),
            ("digital", "industrial robot"),
            ("digital", "robotics"),
            ("digital", "smart home IoT"),
            # --- Clean energy at scale ------------------------------------ #
            ("clean_energy", "solar farm"),
            ("clean_energy", "photovoltaic array"),
            ("clean_energy", "solar panel field"),
            ("clean_energy", "wind turbine"),
            ("clean_energy", "wind farm"),
            ("clean_energy", "offshore wind"),
            # --- The smarter, hungrier grid ------------------------------- #
            ("grid_modern", "EV charging station"),
            ("grid_modern", "electric vehicle charging"),
            ("grid_modern", "smart meter"),
            ("grid_modern", "battery energy storage"),
            ("grid_modern", "high voltage transmission line"),
            ("grid_modern", "power grid control room"),
            # --- Energy in motion (light trails / long exposure) ---------- #
            ("motion", "light trails traffic"),
            ("motion", "long exposure highway night"),
            ("motion", "city night timelapse"),
        ],
        "CREATIVE_BRIEF": MODERN_CREATIVE_BRIEF,
        # Period boosts make no sense post-1995: FSA/OWI is 1935-44 and the
        # REA film is 1940 — disable them so no requests are wasted.
        "LOC_COLLECTION_BOOSTS": [],
        "LOC_CONTRIBUTOR_BOOSTS": [],
        "IA_ALWAYS_INCLUDE_TITLES": [],
    },
}

#: names of the module globals an era is allowed to swap
_ERA_KEYS = ("START_DATE", "END_DATE", "QUERIES", "CREATIVE_BRIEF",
             "LOC_COLLECTION_BOOSTS", "LOC_CONTRIBUTOR_BOOSTS",
             "IA_ALWAYS_INCLUDE_TITLES")


def apply_era(name: str) -> None:
    """Swap the era-dependent settings in place. Called by the CLI's --era flag
    before any fetcher reads this module."""
    if name not in ERAS:
        raise SystemExit(f"unknown era {name!r} — valid: {', '.join(sorted(ERAS))}")
    for key in _ERA_KEYS:
        globals()[key] = ERAS[name][key]
