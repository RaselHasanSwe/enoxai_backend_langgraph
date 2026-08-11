"""Persistent LangGraph checkpoint storage (async for streaming)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings

_settings = get_settings()
_checkpoint_path = Path(_settings.chat_store_path).resolve().parent / "agent_checkpoints.db"
_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

agent_memory: Any = None
_memory_context = None


async def init_agent_memory() -> None:
    """Initialize AsyncSqliteSaver during FastAPI startup."""
    global agent_memory, _memory_context

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    _memory_context = AsyncSqliteSaver.from_conn_string(str(_checkpoint_path))
    agent_memory = await _memory_context.__aenter__()
    await agent_memory.setup()


async def close_agent_memory() -> None:
    """Close checkpoint connection on app shutdown."""
    global agent_memory, _memory_context

    if _memory_context is not None:
        await _memory_context.__aexit__(None, None, None)

    agent_memory = None
    _memory_context = None
