"""Offline unit tests: parsing, dedup, manifest round-trip, sheet rendering.

No network. The archive hosts are blocked in CI-style sandboxes anyway, so the
fetchers are exercised against captured-shape fixtures via a fake HttpClient.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

# Redirect all cache writes into a throwaway dir so tests never touch ./cache.
config.CACHE_DIR = tempfile.mkdtemp(prefix="gas_cache_")

from grid_archive.models import Item, MANIFEST_COLUMNS
from grid_archive.fetchers.loc import LocFetcher, _iter_urls, _slug_from_url, _as_text
from grid_archive.fetchers.internet_archive import InternetArchiveFetcher, _parse_length
from grid_archive.fetchers.wikimedia import WikimediaFetcher
from grid_archive.fetchers.dpla import DplaFetcher
from grid_archive.fetchers.nara import NaraFetcher
from grid_archive import manifest, sheet
from grid_archive.cli import load_dotenv, parse_sources


class FakeClient:
    """Returns queued JSON payloads in order; records requested URLs."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []
        self.session = type("S", (), {"headers": {}})()  # mimic requests.Session.headers

    def get_json(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self._payloads.pop(0) if self._payloads else {}


# --------------------------------------------------------------------------- #
# LOC parsing
# --------------------------------------------------------------------------- #

LOC_PAGE = {
    "results": [
        {
            "id": "http://www.loc.gov/item/2017878331/",
            "url": "https://www.loc.gov/item/2017878331/",
            "title": "Stringing power line, REA project",
            "date": "1940",
            "contributor": ["lee, russell"],
            "partof": ["farm security administration"],
            "image_url": ["//img/s.jpg", "//img/m.jpg", "//img/l.jpg"],
            "rights": "No known restrictions on publication.",
            "access_restricted": False,
        },
        {  # not digitized -> must be skipped
            "id": "http://www.loc.gov/item/nope/",
            "title": "no image here",
        },
    ],
    "pagination": {"next": None},
}


def test_loc_parses_and_skips_non_digitized():
    client = FakeClient([LOC_PAGE])
    f = LocFetcher(client, use_cache=False)
    items = list(f._run_spec_iter("photos", "rural electrification", "photo", "rural", [], "base"))
    assert len(items) == 1
    it = items[0]
    assert it.item_id == "loc:2017878331"
    assert it.source == "LOC" and it.format == "photo" and it.group == "rural"
    assert it.creator == "lee, russell"
    assert it.best_download_url == "//img/m.jpg"   # medium, not the largest
    assert it.thumbnail_url == "//img/s.jpg"
    assert it.master_url == "//img/l.jpg"
    assert "No known restrictions" in it.rights


def test_loc_access_restricted_flag_carried():
    raw = dict(LOC_PAGE["results"][0], access_restricted=True)
    f = LocFetcher(FakeClient([]), use_cache=False)
    it = f._parse_result(raw, "photo", "rural", "q")
    assert "access_restricted" in it.rights


def test_loc_helpers():
    assert _slug_from_url("https://www.loc.gov/item/abc123/") == "abc123"
    assert _as_text([{"title": "A"}, {"title": "B"}]) == "A; B"
    urls = [u for u, _ in _iter_urls({"files": [[{"url": "x.mp4", "mimetype": "video/mp4"}]]})]
    assert "x.mp4" in urls


def test_loc_resolve_stream_finds_mp4():
    detail = {"resources": [{"files": [[
        {"mimetype": "image/jpeg", "url": "poster.jpg"},
        {"mimetype": "video/mp4", "url": "https://tile.loc.gov/clip.mp4"},
    ]]}]}
    f = LocFetcher(FakeClient([detail]), use_cache=False)
    it = Item(item_id="loc:xyz", source="LOC", format="film", group="rural")
    assert f.resolve_stream(it) == "https://tile.loc.gov/clip.mp4"


# --------------------------------------------------------------------------- #
# Internet Archive parsing
# --------------------------------------------------------------------------- #

IA_PAGE = {
    "response": {
        "numFound": 1,
        "docs": [
            {
                "identifier": "PowerAndTheLand1940",
                "title": "Power and the Land",
                "year": "1940",
                "creator": "Joris Ivens",
                "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/",
                "collection": ["prelinger"],
                "mediatype": "movies",
            }
        ],
    }
}


def test_ia_search_emits_always_include_then_query():
    # First film query also emits the always-include target; both share collections.
    client = FakeClient([IA_PAGE, IA_PAGE])
    f = InternetArchiveFetcher(client, use_cache=False)
    items = list(f.search("rural electrification", "film", "rural"))
    assert len(items) == 2
    it = items[0]
    assert it.item_id == "ia:PowerAndTheLand1940"
    assert it.rights.startswith("http")           # licenseurl carried
    assert it.item_page_url.endswith("/details/PowerAndTheLand1940")
    assert "always-include" in items[0].query


def test_ia_ignores_photos():
    f = InternetArchiveFetcher(FakeClient([]), use_cache=False)
    assert list(f.search("neon", "photo", "urban")) == []


def test_ia_stream_prefers_smallest_mp4():
    meta = {"files": [
        {"name": "big.mp4", "format": "h.264", "size": "900000000", "length": "600"},
        {"name": "small.mp4", "format": "512Kb MPEG4", "size": "10000000", "length": "10:00"},
    ]}
    f = InternetArchiveFetcher(FakeClient([meta]), use_cache=False)
    it = Item(item_id="ia:PowerAndTheLand1940", source="IA", format="film", group="rural")
    url = f.resolve_stream(it)
    assert url.endswith("/small.mp4")
    assert it.duration_seconds == 600.0           # "10:00" parsed to seconds


def test_parse_length_variants():
    assert _parse_length("123.5") == 123.5
    assert _parse_length("2:03") == 123.0
    assert _parse_length("1:00:00") == 3600.0
    assert _parse_length(None) is None
    assert _parse_length("garbage") is None


# --------------------------------------------------------------------------- #
# Manifest round-trip + sheet
# --------------------------------------------------------------------------- #

def _sample_items():
    return [
        Item(item_id="loc:1", source="LOC", format="photo", group="rural",
             title="Lineman", date="1938", creator="Rothstein",
             rights="No known restrictions", item_page_url="https://loc.gov/item/1/",
             preview_path="previews/loc_1.jpg", judge_score=5, judge_keep=True,
             judge_reason="hero shot", query="lineman"),
        Item(item_id="ia:2", source="IA", format="film", group="big_machine",
             title="Power and the Land", date="1940", rights="http://cc/pdm",
             item_page_url="https://archive.org/details/2",
             frame_paths=["previews/frames/ia_2_1.jpg", "previews/frames/ia_2_2.jpg"],
             duration_seconds=3600, streaming_note="", query="always-include"),
    ]


def test_manifest_roundtrip_and_columns():
    items = _sample_items()
    with tempfile.TemporaryDirectory() as d:
        old = config.OUTPUT_DIR
        config.OUTPUT_DIR = d
        try:
            manifest.save_items(items)
            loaded = manifest.load_items()
            assert {it.item_id for it in loaded} == {"loc:1", "ia:2"}
            back = next(it for it in loaded if it.item_id == "loc:1")
            assert back.judge_score == 5 and back.judge_keep is True

            with open(manifest.manifest_csv_path()) as fh:
                header = fh.readline().strip().split(",")
            assert header == MANIFEST_COLUMNS
        finally:
            config.OUTPUT_DIR = old


def test_manifest_row_only_has_declared_columns():
    row = _sample_items()[0].manifest_row()
    assert set(row.keys()) == set(MANIFEST_COLUMNS)


def test_sheet_renders_groups_toggles_and_links():
    out = sheet.render_sheet(_sample_items())
    assert "Rural buildout" in out and "Big machine" in out
    assert 'id="fmt"' in out and 'id="sort"' in out and 'id="shortlist"' in out
    assert "https://archive.org/details/2" in out
    assert 'class="filmstrip"' in out          # film with frames -> filmstrip
    assert "★ 5" in out                          # judge score badge


def test_sheet_handles_empty_manifest():
    out = sheet.render_sheet([])
    assert "contact sheet" in out.lower()


def test_run_sheet_writes_timestamped_snapshot():
    import glob
    with tempfile.TemporaryDirectory() as d:
        old = config.OUTPUT_DIR
        config.OUTPUT_DIR = d
        try:
            manifest.save_items(_sample_items())
            sheet.run_sheet()
            live = os.path.join(d, config.CONTACT_SHEET_HTML)
            snaps = glob.glob(os.path.join(d, config.SHEET_ARCHIVE_DIR,
                                           "contact_sheet_*.html"))
            assert os.path.exists(live)
            assert len(snaps) == 1
            content = open(snaps[0]).read()
            assert '<base href="../">' in content   # images resolve from archive/
        finally:
            config.OUTPUT_DIR = old


# --------------------------------------------------------------------------- #
# .env loader
# --------------------------------------------------------------------------- #

def test_dotenv_loads_but_never_overrides_real_env():
    with tempfile.TemporaryDirectory() as d:
        env_path = os.path.join(d, ".env")
        with open(env_path, "w") as fh:
            fh.write("# comment\n")
            fh.write('GRID_TEST_NEW="hello"\n')
            fh.write("GRID_TEST_EXISTING=from_file\n")
            fh.write("blank_line_ignored\n")
        os.environ.pop("GRID_TEST_NEW", None)
        os.environ["GRID_TEST_EXISTING"] = "from_env"
        try:
            load_dotenv(env_path)
            assert os.environ["GRID_TEST_NEW"] == "hello"       # quotes stripped
            assert os.environ["GRID_TEST_EXISTING"] == "from_env"  # real env wins
        finally:
            os.environ.pop("GRID_TEST_NEW", None)
            os.environ.pop("GRID_TEST_EXISTING", None)


# --------------------------------------------------------------------------- #
# Wikimedia Commons
# --------------------------------------------------------------------------- #

WIKIMEDIA_PHOTO = {
    "query": {"pages": {"123": {
        "pageid": 123,
        "title": "File:REA lineman 1938.jpg",
        "imageinfo": [{
            "url": "https://upload.wikimedia.org/rea_lineman.jpg",
            "thumburl": "https://upload.wikimedia.org/thumb/rea_lineman.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:REA_lineman_1938.jpg",
            "mime": "image/jpeg", "mediatype": "BITMAP",
            "extmetadata": {
                "DateTimeOriginal": {"value": "1938"},
                "Artist": {"value": "<a href='#'>Russell Lee</a>"},
                "LicenseShortName": {"value": "Public domain"},
                "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/mark/1.0/"},
            },
        }],
    }}}
}


def test_wikimedia_parses_photo_strips_tags_and_carries_license():
    f = WikimediaFetcher(FakeClient([WIKIMEDIA_PHOTO]), use_cache=False)
    items = list(f.search("rural electrification", "photo", "rural"))
    assert len(items) == 1
    it = items[0]
    assert it.item_id == "wc:123" and it.source == "WIKIMEDIA" and it.format == "photo"
    assert it.creator == "Russell Lee"                       # HTML tags stripped
    assert "Public domain" in it.rights and "publicdomain" in it.rights
    assert it.master_url == "https://upload.wikimedia.org/rea_lineman.jpg"
    assert it.best_download_url.endswith("thumb/rea_lineman.jpg")


def test_wikimedia_filters_out_of_era_and_wrong_mediatype():
    old = dict(WIKIMEDIA_PHOTO)
    payload = {"query": {"pages": {"9": {
        "pageid": 9, "title": "File:x.jpg",
        "imageinfo": [{"url": "u", "mediatype": "BITMAP",
                       "extmetadata": {"DateTimeOriginal": {"value": "1912"},
                                       "LicenseShortName": {"value": "Public domain"}}}]}}}}
    f = WikimediaFetcher(FakeClient([payload]), use_cache=False)
    assert list(f.search("q", "photo", "rural")) == []       # 1912 out of 1930-1959
    # A VIDEO file requested as a photo is skipped.
    vid = {"query": {"pages": {"7": {"pageid": 7, "title": "File:v.webm",
           "imageinfo": [{"url": "v.webm", "mediatype": "VIDEO",
                          "extmetadata": {"DateTimeOriginal": {"value": "1940"},
                                          "LicenseShortName": {"value": "Public domain"}}}]}}}}
    f2 = WikimediaFetcher(FakeClient([vid]), use_cache=False)
    assert list(f2.search("q", "photo", "rural")) == []


def test_wikimedia_free_only_skips_nonfree():
    nonfree = {"query": {"pages": {"5": {"pageid": 5, "title": "File:nf.jpg",
        "imageinfo": [{"url": "u", "mediatype": "BITMAP",
                       "extmetadata": {"DateTimeOriginal": {"value": "1940"},
                                       "LicenseShortName": {"value": "Fair use"}}}]}}}}
    f = WikimediaFetcher(FakeClient([nonfree]), use_cache=False)
    assert list(f.search("q", "photo", "rural")) == []


def test_wikimedia_film_stream_is_original():
    f = WikimediaFetcher(FakeClient([]), use_cache=False)
    it = Item(item_id="wc:1", source="WIKIMEDIA", format="film", group="rural",
              master_url="https://upload.wikimedia.org/movie.webm")
    assert f.resolve_stream(it) == "https://upload.wikimedia.org/movie.webm"


# --------------------------------------------------------------------------- #
# DPLA
# --------------------------------------------------------------------------- #

DPLA_PAGE = {
    "count": 1,
    "docs": [{
        "id": "abc123",
        "sourceResource": {
            "title": "TVA switchyard",
            "date": {"displayDate": "1942", "begin": "1942"},
            "creator": "Tennessee Valley Authority",
            "type": "moving image",
            "rights": "No known copyright restrictions",
        },
        "object": "https://thumb.dp.la/abc123.jpg",
        "isShownAt": "https://provider.org/item/abc123",
        "dataProvider": "TVA Archive",
    }],
}


def test_dpla_parses_film_lead():
    f = DplaFetcher(FakeClient([DPLA_PAGE]), api_key="k", use_cache=False)
    items = list(f.search("TVA", "film", "big_machine"))
    assert len(items) == 1
    it = items[0]
    assert it.item_id == "dpla:abc123" and it.source == "DPLA" and it.format == "film"
    assert it.date == "1942" and it.creator == "Tennessee Valley Authority"
    assert it.rights.startswith("No known copyright")
    assert it.item_page_url == "https://provider.org/item/abc123"
    assert it.master_url == ""                                # DPLA has no direct master
    assert "lead only" in it.streaming_note


def test_dpla_type_mismatch_skipped():
    f = DplaFetcher(FakeClient([DPLA_PAGE]), api_key="k", use_cache=False)
    assert list(f.search("TVA", "photo", "big_machine")) == []  # it's a moving image


def test_dpla_stream_is_none():
    f = DplaFetcher(FakeClient([]), api_key="k", use_cache=False)
    it = Item(item_id="dpla:x", source="DPLA", format="film", group="rural")
    assert f.resolve_stream(it) is None


# --------------------------------------------------------------------------- #
# NARA
# --------------------------------------------------------------------------- #

def _nara_page(source_key="_source", gtypes=None, year="1940-01-01"):
    return {"body": {"hits": {"hits": [{
        "_id": "12345",
        source_key: {
            "title": "Stringing REA line",
            "generalRecordsTypes": gtypes if gtypes is not None else ["Moving Images"],
            "productionDates": [{"logicalDate": year}],
            "useRestriction": {"status": "Unrestricted"},
            "digitalObjects": [
                {"objectType": "Video (MP4)",
                 "objectUrl": "https://catalog.archives.gov/media/12345/clip.mp4",
                 "objectFileSize": 5000000},
                {"objectType": "Thumbnail",
                 "objectUrl": "https://catalog.archives.gov/media/12345/thumb.jpg",
                 "objectFileSize": 20000},
            ],
        },
    }]}}}


def test_nara_parses_film_and_finds_master():
    f = NaraFetcher(FakeClient([_nara_page()]), use_cache=False)
    items = list(f.search("REA", "film", "rural"))
    assert len(items) == 1
    it = items[0]
    assert it.item_id == "nara:12345" and it.source == "NARA" and it.format == "film"
    assert it.master_url.endswith("/clip.mp4")
    assert it.thumbnail_url.endswith("/thumb.jpg")
    assert it.date == "1940" and "Unrestricted" in it.rights
    assert it.item_page_url == "https://catalog.archives.gov/id/12345"
    assert f.resolve_stream(it) == it.master_url


def test_nara_defensive_record_nesting():
    # Some deployments nest the record under 'fields' instead of '_source'.
    f = NaraFetcher(FakeClient([_nara_page(source_key="fields")]), use_cache=False)
    items = list(f.search("REA", "film", "rural"))
    assert len(items) == 1 and items[0].item_id == "nara:12345"


def test_nara_out_of_era_and_type_filtered():
    f = NaraFetcher(FakeClient([_nara_page(year="1965-01-01")]), use_cache=False)
    assert list(f.search("REA", "film", "rural")) == []           # 1965 excluded
    f2 = NaraFetcher(FakeClient([_nara_page()]), use_cache=False)
    assert list(f2.search("REA", "photo", "rural")) == []          # it's a moving image


# --------------------------------------------------------------------------- #
# Audit-fix regressions
# --------------------------------------------------------------------------- #

def test_loc_contributor_boosts_rural_only_and_page_capped():
    f = LocFetcher(FakeClient([]), use_cache=False)
    rural = f._search_specs("lineman", "photo", "rural")
    urban = f._search_specs("neon", "photo", "urban")
    assert any("contributor" in label for _, label, _ in rural)
    assert not any("contributor" in label for _, label, _ in urban)  # spec: rural only
    # base spec paginates fully; boost specs are capped at 1 page
    assert rural[0][2] == config.MAX_PAGES_PER_QUERY
    assert all(mp == 1 for _, label, mp in rural if label != "base")


def test_loc_client_side_year_backstop():
    raw = dict(LOC_PAGE["results"][0], date="1972")
    f = LocFetcher(FakeClient([]), use_cache=False)
    assert f._parse_result(raw, "photo", "rural", "q") is None   # out of era
    undated = dict(LOC_PAGE["results"][0], date="")
    assert f._parse_result(undated, "photo", "rural", "q") is not None  # kept


def test_wikimedia_upload_timestamp_not_used_for_era():
    # Undated original + modern upload timestamp must be KEPT and flagged,
    # not misdated to the upload year and dropped.
    payload = {"query": {"pages": {"8": {"pageid": 8, "title": "File:undated.jpg",
        "imageinfo": [{"url": "u.jpg", "mediatype": "BITMAP",
                       "extmetadata": {"DateTime": {"value": "2017-06-27"},
                                       "LicenseShortName": {"value": "No known copyright restrictions"}}}]}}}}
    f = WikimediaFetcher(FakeClient([payload]), use_cache=False)
    items = list(f.search("q", "photo", "rural"))
    assert len(items) == 1
    assert "undated" in items[0].streaming_note
    # And the Flickr Commons tag is treated as free, not blacklisted.
    assert "No known copyright" in items[0].rights


def test_cache_key_excludes_api_key():
    from grid_archive.cache import _api_cache_path
    a = _api_cache_path("https://api.dp.la/v2/items", {"q": "TVA", "api_key": "AAA"})
    b = _api_cache_path("https://api.dp.la/v2/items", {"q": "TVA", "api_key": "BBB"})
    c = _api_cache_path("https://api.dp.la/v2/items", {"q": "REA", "api_key": "AAA"})
    assert a == b          # rotating the key keeps the cache
    assert a != c          # different queries still differ


def test_http_log_redaction():
    from grid_archive.http import _redact
    out = _redact("https://api.dp.la/v2/items?q=TVA&api_key=SECRET123&page=1")
    assert "SECRET123" not in out and "api_key=***" in out


def test_nara_key_not_on_shared_session_and_403_latch():
    client = FakeClient([])
    f = NaraFetcher(client, api_key="NARAKEY", use_cache=False)
    assert "x-api-key" not in client.session.headers      # session untouched
    f._disabled = True
    assert list(f.search("REA", "film", "rural")) == []   # latch short-circuits


def test_preview_notes_append_not_clobber():
    from grid_archive.preview import _append_note, _clear_status_notes, NOT_STREAMING
    it = Item(item_id="dpla:x", source="DPLA", format="film", group="rural",
              streaming_note="lead only — master at provider")
    _append_note(it, NOT_STREAMING)
    assert "lead only" in it.streaming_note and NOT_STREAMING in it.streaming_note
    _append_note(it, "clip exceeds 100MB cap")            # replaces status, keeps provenance
    assert "lead only" in it.streaming_note
    assert NOT_STREAMING not in it.streaming_note
    _clear_status_notes(it)
    assert it.streaming_note == "lead only — master at provider"


def test_judge_labels_match_verdict_mapping_with_gaps():
    # Item 2 of 3 has no image on disk; labels must be 1..len(labelled) so
    # verdicts map back to the right items.
    import grid_archive.judge as judge

    a = Item(item_id="a", source="LOC", format="photo", group="rural", title="A",
             preview_path="/nonexistent-but-labelled-a.jpg")
    b = Item(item_id="b", source="LOC", format="photo", group="rural", title="B",
             preview_path="/missing.jpg")
    c = Item(item_id="c", source="LOC", format="photo", group="rural", title="C",
             preview_path="/nonexistent-but-labelled-c.jpg")

    sent = {}

    class FakeResp:
        content = [type("T", (), {"type": "text",
                                  "text": '[{"item":1,"keep":true,"score":5,"reason":"r1"},'
                                          '{"item":2,"keep":false,"score":1,"reason":"r2"}]'})()]

    class FakeMessages:
        def create(self, **kw):
            sent["labels"] = [blk["text"] for blk in kw["messages"][0]["content"]
                              if blk.get("type") == "text" and blk["text"].startswith("ITEM")]
            return FakeResp()

    class FakeAnthropic:
        messages = FakeMessages()

    real_block = judge._image_block
    judge._image_block = lambda p: (None if "missing" in p else
                                    {"type": "image", "source": {"type": "base64",
                                     "media_type": "image/jpeg", "data": "AA=="}})
    try:
        judge._judge_batch(FakeAnthropic(), [a, b, c])
    finally:
        judge._image_block = real_block

    assert sent["labels"][0].startswith("ITEM 1: A")
    assert sent["labels"][1].startswith("ITEM 2: C")   # C is labelled 2, not 3
    assert a.judge_score == 5 and a.judge_keep is True
    assert c.judge_score == 1 and c.judge_keep is False  # verdict 2 -> C, not lost
    assert b.judge_score is None                          # skipped stays unjudged


def test_download_fails_fast_on_404_no_retries():
    """A dead link (4xx) must cost ONE attempt, not MAX_RETRIES rounds of backoff."""
    from grid_archive.http import HttpClient, RateLimitedError

    class Resp404:
        status_code = 404
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    sleeps = []
    client = HttpClient(sleep=lambda s: sleeps.append(s))
    attempts = []
    client.session = type("S", (), {
        "get": lambda self, url, **kw: attempts.append(url) or Resp404(),
        "headers": {},
    })()

    try:
        client.download("https://dead.example/x.jpg", "/tmp/never-written.jpg")
        assert False, "expected RateLimitedError"
    except RateLimitedError as e:
        assert "404" in str(e)
    assert len(attempts) == 1                      # exactly one request
    assert sum(sleeps) <= config.BASE_DELAY_SECONDS  # only the polite delay, no backoff


def test_parse_sources_aliases_and_errors():
    assert parse_sources(None) is None
    assert parse_sources("") is None
    assert parse_sources("nara, wiki,dpla") == {"NARA", "WIKIMEDIA", "DPLA"}
    assert parse_sources("LOC") == {"LOC"}
    assert parse_sources("internet-archive".replace("-", "")) == {"IA"}
    try:
        parse_sources("napster")
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "napster" in str(e)


# --------------------------------------------------------------------------- #
# Registry wiring
# --------------------------------------------------------------------------- #

def test_build_fetchers_respects_dpla_key_presence():
    from grid_archive.search import build_fetchers
    from grid_archive.http import HttpClient

    os.environ.pop(config.DPLA_API_KEY_ENV, None)
    sources = [f.source for f in build_fetchers(HttpClient(), use_cache=False)]
    assert "DPLA" not in sources
    for expected in ("LOC", "IA", "WIKIMEDIA", "NARA"):
        assert expected in sources

    os.environ[config.DPLA_API_KEY_ENV] = "test-key"
    try:
        sources2 = [f.source for f in build_fetchers(HttpClient(), use_cache=False)]
        assert "DPLA" in sources2
    finally:
        os.environ.pop(config.DPLA_API_KEY_ENV, None)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
