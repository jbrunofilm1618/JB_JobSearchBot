"""Core data model: a single archival item, and the manifest column order."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from typing import Optional


# Manifest column order. Every field the user asked for is here, in the order
# it appears in manifest.csv. `Item` is a superset (it also carries local
# working paths); MANIFEST_COLUMNS is the reviewable slice.
MANIFEST_COLUMNS = [
    "item_id",
    "source",           # LOC | IA
    "format",           # photo | film
    "group",            # rural | urban | big_machine
    "title",
    "date",
    "creator",          # creator / photographer
    "collection",
    "rights",           # LOC rights statement, or IA licenseurl
    "item_page_url",
    "best_download_url",
    "duration_seconds",
    "file_size_bytes",
    "streaming_note",   # e.g. "not digitized for streaming"
    "judge_score",
    "judge_keep",
    "judge_reason",
    "query",            # the query that first surfaced this item
]


@dataclass
class Item:
    """One archival photo or film, with everything needed for the manifest and
    for local preview/judge work."""

    item_id: str                       # namespaced: "loc:2017..." / "ia:PowerAndTheLand"
    source: str                        # "LOC" | "IA"
    format: str                        # "photo" | "film"
    group: str                         # "rural" | "urban" | "big_machine"
    title: str = ""
    date: str = ""
    creator: str = ""
    collection: str = ""
    rights: str = ""
    item_page_url: str = ""
    best_download_url: str = ""         # medium preview / MP4 derivative
    thumbnail_url: str = ""             # small fallback thumbnail
    master_url: str = ""                # highest-res asset (for fetch-masters)
    duration_seconds: Optional[float] = None
    file_size_bytes: Optional[int] = None
    streaming_note: str = ""
    query: str = ""

    # Judge results (filled by the `judge` subcommand).
    judge_score: Optional[int] = None
    judge_keep: Optional[bool] = None
    judge_reason: str = ""

    # Local working paths (filled by `preview` / `fetch-masters`). Not core
    # manifest columns but persisted so runs are resumable.
    preview_path: str = ""
    clip_path: str = ""
    frame_paths: list = field(default_factory=list)
    master_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def manifest_row(self) -> dict:
        d = self.to_dict()
        return {col: d.get(col, "") for col in MANIFEST_COLUMNS}

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
