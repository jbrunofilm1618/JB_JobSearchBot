"""Offline unit tests: parsing, dedup, manifest round-trip, sheet rendering.

No network. The archive hosts are blocked in CI-style sandboxes anyway, so the
fetchers are exercised against captured-shape fixtures via a fake HttpClient.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from grid_archive.models import Item, MANIFEST_COLUMNS
from grid_archive.fetchers.loc import LocFetcher, _iter_urls, _slug_from_url, _as_text
from grid_archive.fetchers.internet_archive import InternetArchiveFetcher, _parse_length
from grid_archive import manifest, sheet


class FakeClient:
    """Returns queued JSON payloads in order; records requested URLs."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
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
