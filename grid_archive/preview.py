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
            return
        try:
            written = self.client.download(url, dest)
            if written > 0:
                item.preview_path = dest
                item.file_size_bytes = written
        except Exception as exc:  # noqa: BLE001
            log.warning("photo preview failed for %s: %s", item.item_id, exc)

    def _preview_film(self, item: Item) -> None:
        fetcher = self.fetchers.get(item.source)
        # Resolve the MP4 derivative lazily (per-item detail call).
        mp4_url = None
        if fetcher is not None:
            try:
                mp4_url = fetcher.resolve_stream(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("stream resolve failed for %s: %s", item.item_id, exc)

        if not mp4_url:
            # Fall back to the thumbnail JPEG and flag it for manual sourcing.
            item.streaming_note = NOT_STREAMING
            if item.thumbnail_url:
                os.makedirs(config.PREVIEW_DIR, exist_ok=True)
                dest = os.path.join(config.PREVIEW_DIR,
                                    safe_filename(item.item_id, ".jpg"))
                if not os.path.exists(dest):
                    try:
                        self.client.download(item.thumbnail_url, dest)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("thumb fallback failed for %s: %s", item.item_id, exc)
                if os.path.exists(dest):
                    item.preview_path = dest
            return

        item.best_download_url = mp4_url
        os.makedirs(config.CLIP_DIR, exist_ok=True)
        clip_path = os.path.join(config.CLIP_DIR, safe_filename(item.item_id, ".mp4"))

        if not os.path.exists(clip_path):
            try:
                written = self.client.download(mp4_url, clip_path,
                                               max_bytes=self.max_clip_bytes)
            except Exception as exc:  # noqa: BLE001
                log.warning("clip download failed for %s: %s", item.item_id, exc)
                written = -1
            if written == -1:
                # Over the cap: keep the row, note it, use the thumbnail.
                item.streaming_note = f"clip exceeds {self.max_clip_bytes // (1024*1024)}MB cap"
                if item.thumbnail_url:
                    dest = os.path.join(config.PREVIEW_DIR,
                                        safe_filename(item.item_id, ".jpg"))
                    os.makedirs(config.PREVIEW_DIR, exist_ok=True)
                    try:
                        self.client.download(item.thumbnail_url, dest)
                        if os.path.exists(dest):
                            item.preview_path = dest
                    except Exception:  # noqa: BLE001
                        pass
                return

        item.clip_path = clip_path
        item.file_size_bytes = os.path.getsize(clip_path)

        if self.have_ffmpeg:
            duration = _probe_duration(clip_path)
            if duration is not None:
                item.duration_seconds = duration
            item.frame_paths = _sample_frames(clip_path, item.item_id,
                                               item.duration_seconds)

    def preview_item(self, item: Item) -> None:
        if item.format == "photo":
            self._preview_photo(item)
        else:
            self._preview_film(item)


def run_preview(max_clip_mb: int, use_cache: bool = True) -> List[Item]:
    items = manifest.load_items()
    if not items:
        log.warning("no manifest found; run `search` first")
        return []
    previewer = Previewer(max_clip_mb, use_cache=use_cache)
    for i, item in enumerate(items, 1):
        previewer.preview_item(item)
        if i % 25 == 0:
            manifest.save_items(items)  # checkpoint for resumability
            log.info("preview progress: %d/%d", i, len(items))
    manifest.save_items(items)
    log.info("preview complete for %d items", len(items))
    return items
