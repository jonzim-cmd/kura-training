"""Embedding providers with robust fallback behavior.

Primary path is local sentence-transformers when available.
Fallback path is deterministic hashing embeddings, which keeps the semantic
pipeline functional in constrained environments.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from collections import Counter

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity. Returns 0 for invalid vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0

    return dot / math.sqrt(norm_a * norm_b)


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def _hashing_embedding(text: str, dimensions: int) -> list[float]:
    """Deterministic token hashing embedding for fallback mode."""
    vec = [0.0] * dimensions
    tokens = _tokenize(text)
    if not tokens:
        return vec

    counts = Counter(tokens)
    for token, count in counts.items():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = -1.0 if digest[4] % 2 else 1.0
        vec[bucket] += sign * float(count)

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _project_dimensions(vec: list[float], dimensions: int) -> list[float]:
    """Project vector to target dimensions by deterministic folding."""
    if len(vec) == dimensions:
        return vec
    if not vec:
        return [0.0] * dimensions

    out = [0.0] * dimensions
    for i, val in enumerate(vec):
        out[i % dimensions] += float(val)

    norm = math.sqrt(sum(v * v for v in out))
    if norm > 0:
        out = [v / norm for v in out]
    return out


class EmbeddingProvider:
    """Embedding provider abstraction with local-first strategy."""

    def __init__(self) -> None:
        self.provider = os.environ.get("KURA_EMBEDDING_PROVIDER", "hashing").strip().lower()
        self.model = os.environ.get(
            "KURA_EMBEDDING_MODEL",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ).strip()
        self.dimensions = int(os.environ.get("KURA_EMBEDDING_DIMENSIONS", "384"))
        self._sentence_model = None
        self._sentence_model_failed = False

    def descriptor(self) -> dict[str, str | int]:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimensions": self.dimensions,
        }

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        if self.provider == "sentence_transformers":
            vecs = self._embed_sentence_transformers(texts)
            if vecs is not None:
                return vecs

        if self.provider == "openai":
            vecs = self._embed_openai(texts)
            if vecs is not None:
                return vecs

        # Safe fallback path.
        return [_hashing_embedding(t, self.dimensions) for t in texts]

    def _embed_sentence_transformers(self, texts: list[str]) -> list[list[float]] | None:
        if self._sentence_model_failed:
            return None

        try:
            if self._sentence_model is None:
                from sentence_transformers import SentenceTransformer

                model_name = self.model
                if model_name.startswith("sentence-transformers/"):
                    model_name = model_name.split("/", 1)[1]
                self._sentence_model = SentenceTransformer(model_name)

            raw = self._sentence_model.encode(texts, normalize_embeddings=True)  # type: ignore[assignment]
            out: list[list[float]] = []
            for row in raw:
                vec = [float(v) for v in row]
                out.append(_project_dimensions(vec, self.dimensions))
            return out
        except Exception as exc:
            self._sentence_model_failed = True
            logger.warning(
                "sentence-transformers unavailable (%s); falling back to hashing embeddings",
                exc,
            )
            return None

    def _embed_openai(self, texts: list[str]) -> list[list[float]] | None:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            logger.warning("OPENAI_API_KEY missing; falling back to hashing embeddings")
            return None

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key)
            response = client.embeddings.create(
                model=self.model,
                input=texts,
                dimensions=self.dimensions,
            )
            out: list[list[float]] = []
            for item in response.data:
                vec = [float(v) for v in item.embedding]
                out.append(_project_dimensions(vec, self.dimensions))
            return out
        except Exception as exc:
            logger.warning("OpenAI embeddings unavailable (%s); falling back to hashing", exc)
            return None


_PROVIDER: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _PROVIDER  # noqa: PLW0603
    if _PROVIDER is None:
        _PROVIDER = EmbeddingProvider()
    return _PROVIDER
