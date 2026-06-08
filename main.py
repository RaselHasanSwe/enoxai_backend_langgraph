"""
main.py

FastAPI application entry point.

Startup:
  1. Configure logging
  2. Load FAQ data from disk
  3. Load saved FAISS index — or build fresh if none exists

Run:
  uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.rag.engine import rag_engine
from app.utils.utils import configure_logging
from app.databases.chat_store import init_db

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    configure_logging()

    print("[Startup] Loading FAQ data...")
    rag_engine.load_faq_data(settings.faq_data_path)

    print("[Startup] Attempting to load saved FAISS index...")
    loaded = rag_engine.load_index()

    if not loaded:
        print("[Startup] No saved index found — building fresh index (OpenAI embeddings call)...")
        rag_engine.build_index()

    print(f"[Startup] Ready. {rag_engine.total_docs} documents indexed.")
    #print(f"[Startup] Categories: {rag_engine.categories}")

    print("[Startup] Initializing chat message database...")

    init_db()
    
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    print("[Shutdown] Goodbye.")


app = FastAPI(
    title="Enorsia Ecommerce AI Agent",
    description="""
## Enorsia Ecommerce AI Agent

Two-path architecture:

| Path | Trigger | How it works |
|------|---------|--------------|
| **RAG** | General FAQ / policy questions | Hybrid FAISS + BM25 retrieval → LLM answer |
| **Agent** | Transactional requests (orders, returns, exchanges…) | LangGraph ReAct agent with 13 backend tools |

### Key endpoints
- `POST /api/v1/chat` — standard JSON response
- `POST /api/v1/chat/stream` — Server-Sent Events streaming
- `POST /api/v1/index/build` — rebuild FAQ index after updating faq.json
- `GET  /api/v1/index/status` — index health check
- `GET  /api/v1/health` — liveness probe
- `POST /api/v1/debug/retrieve` — inspect raw retrieval (dev only)
    """,
    version="2.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — adjust origins for your frontend domain in production
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Enorsia Ecommerce AI Agent",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
