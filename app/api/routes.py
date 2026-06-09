"""
app/api/routes.py

All FastAPI endpoints. Clean and thin — business logic lives in graph.py.

Endpoints:
  POST /chat          — full JSON response
  POST /chat/stream   — Server-Sent Events streaming
  POST /index/build   — rebuild FAISS index from faq.json
  GET  /index/status  — index health
  GET  /health        — liveness probe
  POST /debug/retrieve — raw retrieval without LLM (dev only)
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.agent.graph import run_agent, stream_agent
from app.models import ChatRequest, ChatResponse, ChatUser, ChatUserRequest, HealthResponse, IndexResponse, ChatHistoryResponse
from app.rag.engine import rag_engine
from app.databases.chat_store import get_history, get_or_create_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Chat — JSON
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Send a message and receive a full JSON response.

    The agent decides internally whether to call the knowledge base,
    order tools, or both — no routing logic on this side.
    """
    try:
        result = await run_agent(
            message=request.message,
            session_id=request.session_id,
        )
        return ChatResponse(
            session_id=request.session_id,
            answer=result["answer"],
            tool_calls=result["tool_calls"],
        )
    except Exception as exc:
        logger.exception("Error in /chat | session=%s", request.session_id)
        raise HTTPException(status_code=500, detail="An unexpected error occurred.") from exc


# ---------------------------------------------------------------------------
# Chat — SSE streaming
# ---------------------------------------------------------------------------

@router.post("/chat/stream", tags=["Chat"])
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    async def event_stream():
        try:
            tool_calls: list[str] = []

            async for token in stream_agent(request.message, request.session_id):
                yield f"data: {json.dumps({'token': token})}\n\n"

        except Exception as exc:
            logger.exception("Streaming error | session=%s", request.session_id)
            yield f"data: {json.dumps({'error': 'An unexpected error occurred.'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

@router.post("/index/build", response_model=IndexResponse, tags=["Index"])
async def build_index() -> IndexResponse:
    """Rebuild the FAISS + BM25 index from the current faq.json."""
    try:
        from app.config import get_settings
        settings = get_settings()
        rag_engine.load_faq_data(settings.faq_data_path)
        rag_engine.build_index()
    except Exception as exc:
        logger.exception("Index build failed")
        raise HTTPException(status_code=500, detail=f"Index build failed: {exc}") from exc

    return IndexResponse(
        status="rebuilt",
        total_documents=rag_engine.total_docs,
        categories=rag_engine.categories,
    )


@router.get("/index/status", response_model=IndexResponse, tags=["Index"])
async def index_status() -> IndexResponse:
    return IndexResponse(
        status="ready" if rag_engine.is_ready else "not_loaded",
        total_documents=rag_engine.total_docs,
        categories=rag_engine.categories,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        index_loaded=rag_engine.is_ready,
        total_docs=rag_engine.total_docs,
    )


# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------

@router.post("/debug/retrieve", tags=["Debug"])
async def debug_retrieve(request: ChatRequest) -> dict:
    """
    Returns raw retrieval results with similarity scores — no LLM involved.
    Useful for tuning BM25/semantic weights. Do not expose in production.
    """
    if not rag_engine.is_ready:
        raise HTTPException(status_code=503, detail="Index not loaded.")

    results = rag_engine.retrieve_with_scores(
        query=request.message,
        category_filter=request.category_filter,
    )
    return {
        "query": request.message,
        "results": [
            {
                "score": round(score, 4),
                "id": doc.metadata.get("id"),
                "category": doc.metadata.get("category"),
                "action_type": doc.metadata.get("action_type"),
                "question": doc.metadata.get("question"),
                "answer": doc.metadata.get("answer"),
            }
            for doc, score in results
        ],
    }


@router.post("/chat/user", response_model=ChatUser, tags=["Chat"])
async def user(request: ChatUserRequest) -> ChatUser:
    """
    Chat endpoint. before start chat session create or get user.
    """
    result = get_or_create_user(name=request.name, email=request.email)
    
    return ChatUser(
        id=result["id"],
        name=result["name"],
        email=result["email"],
        session_id=result["session_id"],
    )

@router.get("/chat/history", response_model=ChatHistoryResponse, tags=["Chat"])
async def get_chat_history(
    user_id: int = Query(..., description="User ID"),
    page: int = Query(1, ge=1, description="Page number starting from 1"),
) -> ChatHistoryResponse:

    result = get_history(user_id=user_id, page=page)

    return ChatHistoryResponse(
        user=result["user"],
        data=result["data"],
        pagination=result["pagination"]
    )