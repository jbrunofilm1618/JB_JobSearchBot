"""`judge` subcommand (optional, run on demand): score every previewed item for
fitness to the creative brief using the Claude API.

For each item Claude returns keep/skip, a 1-5 relevance score, and one line of
reasoning. Results are written to shortlist.json and merged back into the
manifest. Skip verdicts on long films are advisory only — six sampled frames can
miss a great shot — so nothing is ever deleted from the manifest.
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import List, Optional

import config
from .logging_setup import get_logger
from .models import Item
from . import manifest

log = get_logger()

_MEDIA_TYPE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

_INSTRUCTION = """\
You are helping a filmmaker triage archival footage and photographs.

{brief}

Below are {n} candidate items, each labelled ITEM <k> followed by one image
(for photos) or several sampled frames (for films). For EACH item decide whether
to keep it for this specific 60-second film.

Respond with ONLY a JSON array, one object per item, no prose:
[{{"item": <k>, "keep": true|false, "score": 1-5, "reason": "<one short line>"}}]
"""


def _image_block(path: str) -> Optional[dict]:
    ext = os.path.splitext(path)[1].lower()
    media_type = _MEDIA_TYPE.get(ext)
    if not media_type or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            data = base64.standard_b64encode(fh.read()).decode("ascii")
    except OSError as exc:
        log.warning("cannot read image %s: %s", path, exc)
        return None
    return {"type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data}}


def _item_images(item: Item) -> List[str]:
    if item.format == "film" and item.frame_paths:
        return list(item.frame_paths)
    if item.preview_path:
        return [item.preview_path]
    return []


def _extract_json(text: str):
    """Pull the JSON array out of the model's reply, tolerating stray prose."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            return None
    return None


def _judge_batch(client, batch: List[Item]) -> None:
    content: List[dict] = []
    labelled: List[Item] = []
    for item in batch:
        images = _item_images(item)
        blocks = [b for b in (_image_block(p) for p in images) if b]
        if not blocks:
            continue  # nothing to look at; leave unjudged
        # CRITICAL: the ITEM label must be the position in `labelled`, not in
        # `batch` — if an item is skipped for missing images, batch-indexed
        # labels desynchronize from the verdict mapping below and every verdict
        # after the gap lands on the wrong item.
        labelled.append(item)
        content.append({"type": "text",
                        "text": f"ITEM {len(labelled)}: {item.title or '(untitled)'} "
                                f"[{item.format}, {item.date or 'n.d.'}]"})
        content.extend(blocks)

    if not labelled:
        return

    content.append({"type": "text",
                    "text": _INSTRUCTION.format(brief=config.CREATIVE_BRIEF,
                                                n=len(labelled))})
    resp = client.messages.create(
        model=config.JUDGE_MODEL,
        max_tokens=config.JUDGE_MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    verdicts = _extract_json(text)
    if not isinstance(verdicts, list):
        log.warning("judge: could not parse response for batch of %d", len(labelled))
        return

    by_index = {int(v["item"]): v for v in verdicts if isinstance(v, dict) and "item" in v}
    for idx, item in enumerate(labelled, 1):
        v = by_index.get(idx)
        if not v:
            continue
        try:
            item.judge_score = int(v.get("score")) if v.get("score") is not None else None
        except (TypeError, ValueError):
            item.judge_score = None
        item.judge_keep = bool(v.get("keep"))
        item.judge_reason = str(v.get("reason", ""))[:300]


def run_judge(rejudge: bool = False) -> List[Item]:
    try:
        import anthropic
    except ImportError:
        raise SystemExit("The `anthropic` package is required for `judge`: pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY to run the judge pass.")

    items = manifest.load_items()
    if not items:
        log.warning("no manifest found; run `search` and `preview` first")
        return []

    client = anthropic.Anthropic()
    todo = [it for it in items if (rejudge or it.judge_score is None) and _item_images(it)]
    log.info("judging %d items (%d already scored, skipped)",
             len(todo), len(items) - len(todo))

    batch_size = config.JUDGE_ITEMS_PER_REQUEST
    for start in range(0, len(todo), batch_size):
        batch = todo[start:start + batch_size]
        try:
            _judge_batch(client, batch)
        except Exception as exc:  # noqa: BLE001 - one batch failing must not sink the pass
            log.warning("judge batch %d failed: %s", start // batch_size, exc)
        manifest.save_items(items)  # checkpoint

    shortlist = [it for it in items if it.judge_keep]
    shortlist.sort(key=lambda it: it.judge_score or 0, reverse=True)
    with open(os.path.join(config.OUTPUT_DIR, config.SHORTLIST_JSON), "w", encoding="utf-8") as fh:
        json.dump([it.manifest_row() for it in shortlist], fh, indent=2, default=str)

    manifest.save_items(items)
    log.info("judge complete: %d kept -> %s", len(shortlist), config.SHORTLIST_JSON)
    return items
