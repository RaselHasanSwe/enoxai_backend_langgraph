"""
main.py

FastAPI application entry point.
"""

from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env before any app imports that read settings.
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=True)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings, reload_settings, resolve_enox_api_key, resolve_enox_api_url
from app.rag.engine import rag_engine
from app.utils.utils import configure_logging
from app.databases.chat_store import init_db
from app.rag.product_engine import product_rag_engine
from app.rag.product_image_engine import product_image_engine
from fastapi import Request
from fastapi.responses import JSONResponse
from app.middleware.rate_limit import RateLimitMiddleware

settings = reload_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    reload_settings()
    configure_logging()

    api_key = resolve_enox_api_key()
    api_url = resolve_enox_api_url()
    if not api_key:
        print("[Startup] WARNING: ENOX_API_KEY missing in backend/.env")
    else:
        print(f"[Startup] Laravel API key loaded from .env (prefix={api_key[:6]}...)")
    print(f"[Startup] Laravel API URL: {api_url}")

    print("[Startup] Loading FAQ data...")
    rag_engine.load_faq_data(settings.faq_data_path)

    print("[Startup] Attempting to load saved FAISS index...")
    loaded = rag_engine.load_index()

    if not loaded:
        print("[Startup] No saved index found — building fresh index (OpenAI embeddings call)...")
        rag_engine.build_index()

    print(f"[Startup] Ready. {rag_engine.total_docs} documents indexed.")
    #print(f"[Startup] Categories: {rag_engine.categories}")

    print("[Startup] Loading product data...")
    product_rag_engine.load_product_data(settings.product_data_path)
    product_loaded = product_rag_engine.load_index()
    if not product_loaded:
        print("[Startup] Building fresh product index...")
        product_rag_engine.build_index()
    print(f"[Startup] Products ready. {product_rag_engine.total_products} products indexed.")
    
    # ── NEW: Product Image (CLIP) engine ────────────────────────────────────
    print("[Startup] Loading CLIP model for image search...")
    product_image_engine.load_model()
 
    print("[Startup] Loading product image data...")
    product_image_engine.load_product_data(settings.product_data_path) 
 
    image_index_loaded = product_image_engine.load_index()
    
    if not image_index_loaded:
        print("[Startup] ⚠️  No image index found. Build one via POST /api/v1/image-index/build")
        # NOTE: unlike text RAG, we do NOT auto-build here because CLIP indexing
        # downloads every product image over HTTP — too slow/expensive for every
        # cold start. Build explicitly once via the endpoint, then it persists.
    print(f"[Startup] Image search ready. {product_image_engine.total_products} products indexed.")



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
    docs_url="/docs" if settings.debug_mode else None,
    redoc_url="/redoc" if settings.debug_mode else None,
    openapi_url="/openapi.json" if settings.debug_mode else None,
)

# ---------------------------------------------------------------------------
# CORS — configure allowed origins via CORS_ORIGINS env (comma-separated)
# ---------------------------------------------------------------------------

_cors_origins = [
    origin.strip()
    for origin in settings.cors_origins.split(",")
    if origin.strip()
] or ["*"]
_allow_credentials = "*" not in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(RateLimitMiddleware)


@app.middleware("http")
async def maintenance_mode(request: Request, call_next):
    # Allow health check and docs if needed
    allowed_paths = [
        "/api/v1/health",
    ]

    if settings.maintenance_mode and request.url.path not in allowed_paths:
        return JSONResponse(
            status_code=503,
            content={
                "status": "maintenance",
                "message": "System is currently under maintenance. Please try again later."
            }
        )

    response = await call_next(request)
    return response

app.include_router(router, prefix="/api/v1")


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Enorsia Ecommerce AI Agent",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
