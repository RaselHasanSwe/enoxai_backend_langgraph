from __future__ import annotations

import json
import logging
from pathlib import Path
import requests
from app.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()

IMAGE_JSON_URL = settings.image_json_url

# This file lives at backend/app/rag/product_image_sync.py
# -> parents[0]=rag, parents[1]=app, parents[2]=backend
BACKEND_ROOT = Path(__file__).resolve().parents[2]

# settings.image_json_path is a relative string like "data/product_images.json".
# Anchor it to backend/ explicitly so this works the same whether it's run via
# cron, a script, or an API call -- regardless of the process's cwd.
CURRENT_FILE = (BACKEND_ROOT / settings.image_json_path).resolve()
NEW_FILE = CURRENT_FILE.with_name(CURRENT_FILE.stem + "_new" + CURRENT_FILE.suffix)


def _load_json(path: Path):
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def sync_product_images():
    logger.info("Downloading latest product images from %s", IMAGE_JSON_URL)

    response = requests.get(IMAGE_JSON_URL, timeout=60)
    response.raise_for_status()

    remote_data = response.json()

    # Save downloaded file as a temp/staging copy
    _save_json(NEW_FILE, remote_data)

    existing_data = _load_json(CURRENT_FILE)

    # Index by product_id
    existing = {
        str(item["product_id"]): item
        for item in existing_data
    }

    added = 0
    changed = 0

    for item in remote_data:
        product_id = str(item["product_id"])
        remote_images = item.get("product_image", [])

        if product_id not in existing:
            # Brand new product_id -> add as-is
            existing[product_id] = {
                "product_id": product_id,
                "product_image": list(remote_images),
            }
            added += 1
            continue

        # Existing product_id -> merge images, never remove old ones
        current_images = existing[product_id].get("product_image", [])
        current_set = set(current_images)

        new_images = [img for img in remote_images if img not in current_set]

        if new_images:
            existing[product_id]["product_image"] = current_images + new_images
            changed += 1

    merged = list(existing.values())

    _save_json(CURRENT_FILE, merged)

    if NEW_FILE.exists():
        NEW_FILE.unlink()

    logger.info(
        "Sync completed. Added=%s Changed=%s Total=%s",
        added,
        changed,
        len(merged),
    )

    return {
        "added": added,
        "changed": changed,
        "total": len(merged),
    }