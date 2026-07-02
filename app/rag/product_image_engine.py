"""
app/rag/product_image_engine.py
================================
CLIP-based product image similarity search.
Mirrors the structure of your existing rag_engine / product_rag_engine.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
import pickle
import logging
import base64
from pathlib import Path
from io import BytesIO
from typing import Optional

import numpy as np
import faiss
import torch
import requests
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from app.config import get_settings
from app.models import ImageSearchResult
from app.tools.tools import _to_json

logger = logging.getLogger(__name__)
settings = get_settings()

class ProductImageRAGEngine:
    """
    CLIP-based image similarity engine.
    Loaded once at app startup (see main.py lifespan), mirrors rag_engine pattern.
    """

    def __init__(self) -> None:
        self.clip_model: Optional[CLIPModel] = None
        self.clip_processor: Optional[CLIPProcessor] = None

        self.index: Optional[faiss.Index] = None
        self.index_ids: list[str] = []
        self.products_map: dict = {}

        self._model_loaded = False

    # ────────────────────────────────────────────────
    # Model loading (call once at startup)
    # ────────────────────────────────────────────────
    def load_model(self) -> None:
        if self._model_loaded:
            return
        
        model_name = settings.image_clip_model
        if not model_name:
            logger.warning("No CLIP model specified in settings")
            return
        logger.info("Loading CLIP model: %s", model_name)
        self.clip_model = CLIPModel.from_pretrained(model_name)  # type: ignore
        self.clip_processor = CLIPProcessor.from_pretrained(model_name)  # type: ignore
        self.clip_model.eval()  # type: ignore[attr-defined]
        self._model_loaded = True
        logger.info("CLIP model loaded ✅")

    # ────────────────────────────────────────────────
    # Product data
    # ────────────────────────────────────────────────
    def load_product_data(self, json_path: str) -> None:
        path = Path(json_path)
        if not path.exists():
            logger.warning("Product image JSON not found at %s", json_path)
            self.products_map = {}
            return

        with open(path, "r") as f:
            products = json.load(f)

        if isinstance(products, dict):
            products = list(products.values())

        self.products_map = {p["product_id"]: p for p in products}
        logger.info("Loaded %d products for image indexing", len(self.products_map))

    # ────────────────────────────────────────────────
    # Index load / save
    # ────────────────────────────────────────────────
    def load_index(self) -> bool:
        index_path = settings.image_index_path
        ids_path = settings.image_ids_path

        if not index_path or not ids_path:
            logger.warning("No image index or id paths specified in settings")
            return False

        if Path(index_path).exists() and Path(ids_path).exists():
            self.index = faiss.read_index(index_path)
            with open(ids_path, "rb") as f:
                self.index_ids = pickle.load(f)
            logger.info("Image index loaded ✅ (%d products)", len(self.index_ids))
            return True
        logger.warning("No saved image index found at %s", index_path)
        return False

    def build_index(
        self, 
        limit: Optional[int] = None,
    ) -> dict:
        """Fetch product images, embed with CLIP, build + persist FAISS index."""
        image_base_url  = settings.image_base_url
        index_path      = settings.image_index_path
        ids_path        = settings.image_ids_path
        json_path       = settings.image_json_path

        if not index_path or not ids_path or not json_path or not image_base_url:
            logger.warning("No image index or id, json or image paths specified in settings")
            raise ValueError("No image index or id, json or image paths specified in settings")
    

        self.load_product_data(json_path)
        products = list(self.products_map.values())
        if limit:
            products = products[:limit]

        embeddings: list[np.ndarray] = []
        indexed_ids: list[str] = []
        failed: list[dict] = []

        for i, product in enumerate(products):
            product_id = product.get("product_id", f"unknown_{i}")
            image_filename = product.get("product_image", "")

            if not image_filename:
                failed.append({"product_id": product_id, "reason": "no image field"})
                continue

            try:
                url = image_base_url + image_filename
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                image = Image.open(BytesIO(response.content)).convert("RGB")
                emb = self._get_clip_embedding(image)

                embeddings.append(emb)
                indexed_ids.append(product_id)
                logger.info("[%d/%d] ✅ %s", i + 1, len(products), product_id)

            except Exception as e:
                failed.append({"product_id": product_id, "reason": str(e)})
                logger.warning("[%d/%d] ❌ %s: %s", i + 1, len(products), product_id, e)

        if not embeddings:
            raise ValueError("No images could be indexed.")

        matrix = np.array(embeddings).astype("float32")
        dimension = matrix.shape[1]  # 512 for CLIP ViT-B/32
        index = faiss.IndexFlatIP(dimension)  # cosine similarity on normalized vectors
        index.add(matrix)  # type: ignore[call-arg]

        Path(index_path).parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, index_path)
        with open(ids_path, "wb") as f:
            pickle.dump(indexed_ids, f)

        self.index = index
        self.index_ids = indexed_ids

        return {
            "indexed": len(indexed_ids),
            "failed": len(failed),
            "failed_details": failed[:10],
            "dimension": dimension,
        }

    # ────────────────────────────────────────────────
    # Embedding helper
    # ────────────────────────────────────────────────
    def _get_clip_embedding(self, pil_image: Image.Image) -> np.ndarray:
        if not self._model_loaded:
            raise RuntimeError("CLIP model not loaded. Call load_model() first.")

        inputs = self.clip_processor(images=pil_image, return_tensors="pt")  # type: ignore
        with torch.no_grad():
            emb = self.clip_model.get_image_features(**inputs)  # type: ignore
        emb = emb / emb.norm(dim=-1, keepdim=True)
        return emb.squeeze().numpy().astype("float32")

    # ────────────────────────────────────────────────
    # Search
    # ────────────────────────────────────────────────
    def apiSearch(
        self,
        pil_image: Image.Image,
        top_k: int = 5,
    ) -> list[ImageSearchResult]:
        
        image_base_url = settings.image_base_url
        min_score = settings.image_min_similarity
        
        if not image_base_url:
            logger.warning("No image base url specified in settings")
            raise ValueError("No image base url specified in settings")

        if self.index is None or len(self.index_ids) == 0:
            raise RuntimeError("No image index built yet. Call build_index() first.")

        query_vector = self._get_clip_embedding(pil_image).reshape(1, -1)
        scores, indices = self.index.search(query_vector, top_k)  # type: ignore[call-arg]

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            score = float(score)
            if score < min_score:
                continue
            product_id = self.index_ids[idx]
            product = self.products_map.get(product_id, {})

            attrs = product.get("attributes", {})
            results.append(ImageSearchResult(
                rank=len(results) + 1,
                score=round(float(score), 4),
                product_id=product_id,
                product_name=product.get("product_name"),
                product_url=product.get("product_url"),
                product_image=product.get("product_image", ""),
                department=product.get("department"),
                category=product.get("category"),
                price=product.get("price"),
                currency=product.get("currency", "GBP"),
                discount_price=product.get("discount_price"),
                discount_percent=product.get("discount_percent"),
                has_discount=product.get("has_discount", False),
                in_stock=product.get("in_stock", True),
                rating=product.get("rating"),
                total_reviews=product.get("total_reviews"),
                colors=attrs.get("colors", []),
                sizes=attrs.get("sizes", []),
                fabric=attrs.get("fabric"),
                fit=attrs.get("fit"),
                sleeve=attrs.get("sleeve"),
                neckline=attrs.get("neckline"),
                season=attrs.get("season"),
                occasion=attrs.get("occasion", []),
            ))
        return results

    def _filter_and_format(self, scores, indices, top_k) -> list[dict]:
        min_score = settings.image_min_similarity
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            score = float(score)
            if score < min_score:
                continue
            product_id = self.index_ids[idx]
            product = self.products_map.get(product_id)
            if not product:
                logger.warning("Indexed product_id %s missing from products_map", product_id)
                continue
            attrs = product.get("attributes", {})
            results.append({
                "rank": len(results) + 1,
                "score": round(score, 4),
                "product_id": product["product_id"],
                "product_name": product.get("product_name"),
                "product_url": product.get("product_url"),
                "product_image": product.get("product_image", ""),
                "category": product.get("category"),
                "department": product.get("department"),
                "price": product.get("price"),
                "currency": product.get("currency", "GBP"),
                "discount_price": product.get("discount_price"),
                "discount_percent": product.get("discount_percent"),
                "has_discount": product.get("has_discount", False),
                "in_stock": product.get("in_stock", True),
                "rating": product.get("rating"),
                "total_reviews": product.get("total_reviews"),
                "colors": attrs.get("colors", []),
                "sizes": attrs.get("sizes", []),
                "fabric": attrs.get("fabric"),
                "fit": attrs.get("fit"),
                "sleeve": attrs.get("sleeve"),
                "season": attrs.get("season"),
                "occasion": attrs.get("occasion", []),
                "neckline": attrs.get("neckline"),
            })
        return results

    def agentSearch(self, pil_image: str, top_k: int = 5) -> list[dict]:
        image_base_url = settings.image_base_url
        if not image_base_url:
            raise ValueError("No image base url specified in settings")
        if self.index is None or len(self.index_ids) == 0:
            raise RuntimeError("No image index built yet. Call build_index() first.")

        b64 = pil_image.split(",")[1] if "," in pil_image else pil_image
        image = Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
        query_vector = self._get_clip_embedding(image).reshape(1, -1)

        # search wider than top_k so filtering doesn't starve you of results
        scores, indices = self.index.search(query_vector, top_k * 3)  # type: ignore[call-arg]
        results = self._filter_and_format(scores, indices, top_k)
        return results[:top_k]
    
    # ────────────────────────────────────────────────
    # Status helpers (mirrors rag_engine.total_docs style)
    # ────────────────────────────────────────────────
    @property
    def total_products(self) -> int:
        return len(self.index_ids)

    @property
    def is_ready(self) -> bool:
        return self.index is not None and len(self.index_ids) > 0


# Singleton instance — imported across the app, same pattern as rag_engine
product_image_engine = ProductImageRAGEngine()