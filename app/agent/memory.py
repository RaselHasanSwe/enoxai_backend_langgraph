"""Persistent LangGraph checkpoint storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import get_settings

_settings = get_settings()
_checkpoint_path = Path(_settings.chat_store_path).resolve().parent / "agent_checkpoints.db"
_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

_conn = sqlite3.connect(str(_checkpoint_path), check_same_thread=False)
agent_memory = SqliteSaver(_conn)
agent_memory.setup()
