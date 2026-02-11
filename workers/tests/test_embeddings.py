"""Tests for embedding provider fallback + similarity helpers."""

from kura_workers.embeddings import EmbeddingProvider, cosine_similarity


def test_hashing_embeddings_are_deterministic(monkeypatch):
    monkeypatch.setenv("KURA_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("KURA_EMBEDDING_DIMENSIONS", "64")

    provider = EmbeddingProvider()
    vec_a1 = provider.embed_many(["Kniebeuge"])[0]
    vec_a2 = provider.embed_many(["Kniebeuge"])[0]
    vec_b = provider.embed_many(["Bench Press"])[0]

    assert vec_a1 == vec_a2
    assert len(vec_a1) == 64
    assert vec_a1 != vec_b


def test_cosine_similarity_basic_behavior():
    assert abs(cosine_similarity([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-6
    assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-6
    assert cosine_similarity([], []) == 0.0
