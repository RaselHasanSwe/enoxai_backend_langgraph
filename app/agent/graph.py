"""
app/agent/graph.py

The single LangGraph ReAct agent for the Enorsia ecommerce assistant.

All 14 tools are registered here — including search_knowledge_base (RAG).
The LLM decides in one reasoning loop which tool(s) to call, in what order,
and when it has enough information to give a final answer.

No router. No classifier. No separate code paths.

Product search special handling:
  - When search_products fires, its raw JSON result is captured.
  - The SSE stream emits an extra event:  data: {"product_data": [...]}
  - The LLM is instructed to respond with a compact JSON envelope:
      {"products": ["Title A", "Title B"]}
    so the frontend can match titles to the rich product_data payload.
"""

from __future__ import annotations

import logging
import json
from typing import AsyncIterator

from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain.globals import set_verbose, set_debug

from app.config import get_settings
from app.tools.tools import ALL_TOOLS
from app.agent.prompt import _SYSTEM_PROMPT
from app.databases.chat_store import save_message

settings = get_settings()
logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Agent Configuration
# ---------------------------------------------------------------------------

set_verbose(True)
#set_debug(True)

agent_memory = MemorySaver()

_llm = ChatOpenAI(
    model=settings.openai_model,
    api_key=settings.openai_api_key,
    temperature=0.2,
    streaming=True,
    model_kwargs={"stream_options": {"include_usage": True}}
)

_agent = create_react_agent(model=_llm, tools=ALL_TOOLS, state_modifier=_SYSTEM_PROMPT, checkpointer=agent_memory)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def stream_agent(message: str, session_id: str) -> AsyncIterator[str | dict]:
    """
    Stream the agent's output as an async iterator.

    Yields either:
      - str   — a text token to be forwarded as  data: {"token": "..."}
      - dict  — a product payload to be forwarded as  data: {"product_data": [...]}

    The caller (routes.py) is responsible for JSON-encoding each yield.
    """
    logger.info(
        "USER MESSAGE:stream_agent()  | session=%s message=%s",
        session_id, message,
    )
    save_message(session_id, "user", message)

    config: RunnableConfig = {"configurable": {"thread_id": session_id}}
    tool_calls: list[str] = []
    agent_response = ""

    # Collect search_products results emitted by ToolMessage nodes
    # so we can forward full product data to the frontend.
    product_data_to_emit: list[dict] | None = None

    try:
        async for msg_chunk, metadata in _agent.astream(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            stream_mode="messages",
        ):
            # ── Tool result interception ──────────────────────────────────
            if isinstance(msg_chunk, ToolMessage):
                tool_name = msg_chunk.name  # set by LangGraph on ToolMessage
                if tool_name == "search_products":
                    try:
                        payload = json.loads(msg_chunk.content)
                        if payload.get("status") and payload.get("products"):
                            product_data_to_emit = payload["products"]
                            logger.info(
                                "PRODUCT-STREAM | captured %d products for session=%s",
                                len(product_data_to_emit), session_id,
                            )
                    except (json.JSONDecodeError, AttributeError):
                        logger.warning(
                            "PRODUCT-STREAM | failed to parse search_products result for session=%s",
                            session_id,
                        )

            # ── AI text tokens ────────────────────────────────────────────
            if isinstance(msg_chunk, AIMessageChunk):
                if (
                    msg_chunk.content
                    and not msg_chunk.tool_calls
                    and not msg_chunk.tool_call_chunks
                ):
                    content_str = msg_chunk.content
                    if isinstance(content_str, str) and content_str:
                        yield content_str
                        agent_response += content_str

                if msg_chunk.tool_calls:
                    for tc in msg_chunk.tool_calls:
                        tool_calls.append(tc.get("name", "unknown"))

        # ── After stream ends, emit product data if we have it ────────────
        if product_data_to_emit is not None:
            yield {"__product_data__": product_data_to_emit}

    except Exception:
        logger.exception("AGENT FAILED:stream_agent()  | session=%s", session_id)
        yield "\nSorry, something went wrong."
    finally:
        if agent_response.strip():
            save_message(session_id, "ai", agent_response)

        logger.info(
            "AGENT RESPONSE:stream_agent()  | session=%s response=%s",
            session_id, agent_response,
        )
        logger.info(
            "AGENT TOOLS USED:stream_agent()  | session=%s tools=%s",
            session_id, tool_calls,
        )


async def run_agent(message: str, session_id: str) -> dict:
    """
    Run one agent turn and return a structured result dict.

    Returns:
        {
            "answer":     str  — final response to show the customer,
            "tool_calls": list — names of every tool that fired this turn,
        }
    """
    logger.info(
        "USER MESSAGE:run_agent()  | session=%s message=%s",
        session_id, message[:80],
    )
    save_message(session_id, "user", message)
    config: RunnableConfig = {"configurable": {"thread_id": session_id}}

    answer = ""
    tool_calls: list[str] = []

    try:
        result = await _agent.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config=config,
        )
        for msg in result.get("messages", []):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(tc.get("name", "unknown"))

            if (
                hasattr(msg, "content")
                and msg.content
                and not getattr(msg, "tool_calls", None)
            ):
                answer = msg.content

    except Exception:
        logger.exception("AGENT FAILED:run_agent()  | session=%s", session_id)
        answer = "Sorry, something went wrong."
    finally:
        if answer.strip():
            save_message(session_id, "ai", answer)
        logger.info(
            "AGENT RESPONSE:run_agent()  | session=%s response=%s",
            session_id, answer,
        )
        logger.info(
            "AGENT TOOLS USED:run_agent()  | session=%s tools=%s",
            session_id, tool_calls,
        )

    return {"answer": answer, "tool_calls": tool_calls}