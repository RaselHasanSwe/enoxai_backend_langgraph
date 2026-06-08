"""
app/config.py

Central settings loaded from environment variables / .env file.
All other modules import ``get_settings()`` — never read os.environ directly.
"""

from functools import lru_cache
from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"

    # RAG tuning
    faq_data_path: str = "data/faq.json"
    faiss_index_path: str = "data/faiss_index"
    top_k_results: int = 4
    bm25_weight: float = 0.4
    semantic_weight: float = 0.6

    # Chat store
    chat_store_path: str = "data/enoxai.db"


    # LangChain / LangSmith Tracing
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "EnoXAI"


    # Laravel backend
    enox_api_url: str = "http://localhost:8000"
    enox_api_key: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """
    Return a cached Settings instance.
    The cache is module-level so settings are read from .env exactly once.
    """
    return Settings()
