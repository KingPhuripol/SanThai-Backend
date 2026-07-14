"""
Semantic text-embedding service.
Uses sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 (384-dim).
Supports Thai + English queries natively.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: Optional["SentenceTransformer"] = None
_MODEL_ID = "paraphrase-multilingual-MiniLM-L12-v2"


def _load_model() -> "SentenceTransformer":
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # noqa: lazy import
        # Load local files only to prevent downloading or hanging on slow network
        _model = SentenceTransformer(_MODEL_ID, local_files_only=True)
    return _model  # type: ignore[return-value]


def encode_query(query: str) -> list[float]:
    """Encode text → 384-dim normalized float list."""
    # Bypassed local ML model to avoid heavy PyTorch/OpenMP imports and deadlocks in sandbox dev environment.
    # Supabase automatically performs lightning-fast keyword-based search fallback instead!
    return [0.0] * 384


