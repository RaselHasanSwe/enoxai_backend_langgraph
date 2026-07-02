"""
Save user-uploaded chat images to disk (not in the database as base64).
"""

from __future__ import annotations

import base64
import logging
import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def save_chat_image(session_id: str, image_base64: str) -> str:
    """
    Decode base64 image data, save as JPEG, and return a relative path
    suitable for storing in chat_messages.image_path (session_id/filename).
    """
    settings = get_settings()

    b64 = image_base64
    if "," in b64:
        b64 = b64.split(",", 1)[1]

    image_bytes = base64.b64decode(b64)
    image = Image.open(BytesIO(image_bytes)).convert("RGB")

    upload_dir = Path(settings.chat_uploads_path) / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid4().hex}.jpg"
    filepath = upload_dir / filename
    image.save(filepath, "JPEG", quality=85)

    relative_path = f"{session_id}/{filename}"
    logger.info("CHAT-IMAGE | saved | path=%s", relative_path)
    return relative_path


def get_chat_image_path(image_path: str) -> Path | None:
    """Resolve a stored image_path to an absolute file path, or None if invalid."""
    if not image_path or ".." in image_path:
        return None

    parts = image_path.split("/")
    if len(parts) != 2:
        return None

    session_id, filename = parts
    if not _FILENAME_RE.match(session_id) or not _FILENAME_RE.match(filename):
        return None

    settings = get_settings()
    filepath = Path(settings.chat_uploads_path) / session_id / filename
    return filepath if filepath.is_file() else None
