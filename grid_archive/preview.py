"""`preview` subcommand: pull medium-res preview JPEGs for photos and capped
MP4 derivatives for films, then sample a filmstrip of frames from each clip.

Everything is resumable: a preview / clip / frame that already exists on disk is
not re-downloaded or re-extracted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Dict, List, Optional

import config
from .cache import safe_filename
from .fetchers.base import Fetcher
from .http import HttpClient
from .logging_setup import get_logger
from .models import Item
from .search import fetcher_map
from . import manifest

log = get_logger()

NOT_STREAMING = "not digitized for streaming"
DOWNLOAD_FAILED = "download failed — will retry on next preview run"

# Preview-status notes are transient and owned by this stage; search-time notes
# ("lead only — master at provider", "undated — verify era") are provenance and
# must never be clobbered. Notes are managed as a ';'-joined set.
_STATUS_PREFIXES = (NOT_STREAMING, "download failed", "clip exceeds")


def _note_parts(item: Item) -> list:
    return [p.strip() for p in (item.streaming_note or "").split(";") if p.strip()]


def _append_note(item: Item, note: str) -> None:
    parts = [p for p in _note_parts(item)
             if not p.startswith(_STATUS_PREFIXES)]  # one status note at a time
    parts.append(note)
    item.streaming_note = "; ".join(parts)


def _clear_status_notes(item: Item) -> None:
    parts = [p for p in _note_parts(item) if not p.startswith(_STATUS_PREFIXES)]
    item.streaming_note = "; ".join(parts)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe_duration(path: str) -> Optional[float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError) as exc:
        log.warning("ffprobe failed on %s: %s", path, exc)
        return None


def _sample_frames(clip_path: str, item_id: str, duration: Optional[float]) -> List[str]:
    """Extract FRAMES_PER_CLIP evenly-spaced frames. Returns frame paths."""
    if duration is None or duration <= 0:
        return []
    os.makedirs(config.FRAME_DIR, exist_ok=True)
    frames: List[str] = []
    n = config.FRAMES_PER_CLIP
    for i in range(1, n + 1):
        ts = duration * i / (n + 1)  # evenly spaced, no black head/tail frames
        out_path = os.path.join(config.FRAME_DIR,
                                safe_filename(f"{item_id}_{i}", ".jpg"))
        if os.path.exists(out_path):
            frames.append(out_path)
            continue
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", clip_path,
                 "-frames:v", "1", "-q:v", "3", out_path],
                capture_output=True, timeout=120, check=True)
            if os.path.exists(out_path):
                frames.append(out_path)
        except subprocess.SubprocessError as exc:
            log.warning("ffmpeg frame %d failed for %s: %s", i, item_id, exc)
    return frames


class Previewer:
    def __init__(self, max_clip_mb: int, use_cache: bool = True):
        self.client = HttpClient()
        self.max_clip_bytes = int(max_clip_mb * 1024 * 1024)
        self.fetchers: Dict[str, Fetcher] = fetcher_map(self.client, use_cache)
        self.have_ffmpeg = _ffmpeg_available()
        if not self.have_ffmpeg:
            log.warning("ffmpeg/ffprobe not found on PATH: clips download but "
                        "no frames will be sampled")

    def _preview_photo(self, item: Item) -> None:
        url = item.best_download_url or item.thumbnail_url
        if not url:
            item.streaming_note = "no image url"
            return
        os.makedirs(config.PREVIEW_DIR, exist_ok=True)
        dest = os.path.join(config.PREVIEW_DIR, safe_filename(item.item_id, ".jpg"))
        if os.path.exists(dest):
            item.preview_path = dest
            if not item.file_size_bytes:  # backfill after interrupted runs
                item.file_size_bytes = os.path.getsize(dest)
            return
        try:
            written = self.client.download(url, dest)
            if written > 0:
                item.preview_path = dest
                item.file_size_bytes = written
        except Exception as exc:  # noqa: BLE001
            log.warning("photo preview failed for %s: %s", item.item_id, exc)

    def _thumb_fallback(self, item: Item) -> None:
        """Give the row a reviewable image via its thumbnail (idempotent)."""
        if not item.thumbnail_url:
            return
        os.makedirs(config.PREVIEW_DIR, exist_ok=True)
        dest = os.path.join(config.PREVIEW_DIR, safe_filename(item.item_id, ".jpg"))
        if not os.path.exists(dest):
            try:
                self.client.download(item.thumbnail_url, dest)
            except Exception as exc:  # noqa: BLE001
                log.warning("thumb fallback failed for %s: %s", item.item_id, exc)
        if os.path.exists(dest):
            item.preview_path = dest

    def _finish_clip(self, item: Item, clip_path: str) -> None:
        """Record a clip that exists on disk: size, duration, frames, clean notes."""
        item.clip_path = clip_path
        item.file_size_bytes = os.path.getsize(clip_path)
        _clear_status_notes(item)  # a working clip refutes any stale status
        if self.have_ffmpeg:
            if item.duration_seconds is None:
                duration = _probe_duration(clip_path)
                if duration is not None:
                    item.duration_seconds = duration
            item.frame_paths = _sample_frames(clip_path, item.item_id,
                                              item.duration_seconds)

    def _preview_film(self, item: Item) -> None:
        os.makedirs(config.CLIP_DIR, exist_ok=True)
        clip_path = os.path.join(config.CLIP_DIR, safe_filename(item.item_id, ".mp4"))

        # Resumability short-circuit #1: clip already on disk — no detail
        # request, no download. Frames/duration are filled if missing.
        if os.path.exists(clip_path):
            self._finish_clip(item, clip_path)
            return

        # Resumability short-circuit #2: a persisted over-cap decision. Don't
        # re-attempt the oversized download on every rerun; the note names the
        # cap, so raising --max-clip-mb (new cap not in note) retries naturally.
        cap_note = f"clip exceeds {self.max_clip_bytes // (1024 * 1024)}MB cap"
        if cap_note in _note_parts(item):
            self._thumb_fallback(item)
            return

        fetcher = self.fetchers.get(item.source)
        # Resolve the MP4 derivative lazily (per-item detail call).
        mp4_url = None
        if fetcher is not None:
            try:
                mp4_url = fetcher.resolve_stream(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("stream resolve failed for %s: %s", item.item_id, exc)

        if not mp4_url:
            # No streamable derivative. Appends rather than replaces, so
            # provenance notes like DPLA's "lead only — master at provider"
            # survive next to the streaming status.
            _append_note(item, NOT_STREAMING)
            self._thumb_fallback(item)
            return

        item.best_download_url = mp4_url
        try:
            written = self.client.download(mp4_url, clip_path,
                                           max_bytes=self.max_clip_bytes)
        except Exception as exc:  # noqa: BLE001
            # Transient failure is NOT the same as over-cap: mark it retryable
            # and leave the row clean for the next preview run.
            log.warning("clip download failed for %s: %s", item.item_id, exc)
            _append_note(item, DOWNLOAD_FAILED)
            return

        if written == -1:
            # Genuinely over the cap: persist the decision, keep the row.
            _append_note(item, cap_note)
            self._thumb_fallback(item)
            return

        self._finish_clip(item, clip_path)

    def preview_item(self, item: Item) -> None:
        if item.format == "photo":
            self._preview_photo(item)
        else:
            self._preview_film(item)


def _preview_source(source: str, its: List[Item], max_clip_mb: int,
                    use_cache: bool, stop_event) -> str:
    """Preview one source's items on its own thread with its own Previewer
    (own HttpClient), so polite pacing is per host and sources overlap.
    Checks stop_event between items so Ctrl+C ends the run promptly instead of
    the thread grinding on to the end of its list."""
    previewer = Previewer(max_clip_mb, use_cache=use_cache)
    for i, item in enumerate(its, 1):
        if stop_event.is_set():
            log.info("preview[%s]: stopped at %d/%d", source, i - 1, len(its))
            return source
        previewer.preview_item(item)
        if i % 25 == 0:
            log.info("preview[%s]: %d/%d", source, i, len(its))
    return source


def run_preview(max_clip_mb: int, use_cache: bool = True,
                sources: Optional[set] = None) -> List[Item]:
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    items = manifest.load_items()
    if not items:
        log.warning("no manifest found; run `search` first")
        return []

    by_source: Dict[str, List[Item]] = {}
    for it in items:
        if sources and it.source not in sources:
            continue  # --sources filter: other rows stay untouched in the manifest
        by_source.setdefault(it.source, []).append(it)

    stop_event = threading.Event()
    executor = ThreadPoolExecutor(max_workers=max(1, len(by_source)))
    try:
        futures = {executor.submit(_preview_source, src, its, max_clip_mb,
                                   use_cache, stop_event): src
                   for src, its in by_source.items()}
        for fut in as_completed(futures):
            source = fut.result()
            manifest.save_items(items)  # checkpoint as each source finishes
            log.info("preview[%s] complete (checkpointed)", source)
    except KeyboardInterrupt:
        stop_event.set()  # workers bail after their current item
        manifest.save_items(items)
        log.warning("interrupted — partial preview state saved; workers stopping")
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    manifest.save_items(items)
    log.info("preview complete for %d items", len(items))
    return items
