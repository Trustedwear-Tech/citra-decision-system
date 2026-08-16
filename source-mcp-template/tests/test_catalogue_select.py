"""select_datasets: a source with more tables than the cap must be RANKED
(cosine + optional reranker) within the source, not dumped wholesale into the
NL→query prompt. Small sources skip ranking."""
from __future__ import annotations

import asyncio

import catalogue_index as ci
import router


def _make_source(n: int):
    datasets = [
        {"id": f"db.t{i}", "source_id": "db", "kind": "sql", "columns": []}
        for i in range(n)
    ]
    return {"source_id": "db", "org_id": "acme", "datasets": datasets}


def test_large_source_is_ranked_within_source(monkeypatch):
    monkeypatch.setattr(router, "_sources", {"db": _make_source(12)})

    captured = {}

    async def fake_vs(question, *, max_results, candidates=None, scope="ALL"):
        captured["n_candidates"] = len(candidates or [])
        captured["max_results"] = max_results
        captured["scope"] = scope
        return (candidates or [])[:max_results]

    monkeypatch.setattr(ci, "_vector_search", fake_vs)

    out = asyncio.run(ci.select_datasets("find t3", source_id="db", max_results=8))

    # ranked across the source's 12 tables, capped at 8
    assert captured["n_candidates"] == 12
    assert captured["max_results"] == 8
    assert captured["scope"] == "db"   # within-source ranking is scoped to the source_id
    assert len(out) == 8


def test_vector_search_uses_precomputed_embeddings(monkeypatch):
    """Datasets carrying a precomputed `embedding` (from ingestion) are NOT
    re-embedded; only the ones lacking one hit the embed service."""
    import sys
    import types
    import catalogue_index as ci

    cands = [
        {"id": "a", "source_id": "s", "kind": "sql", "columns": [],
         "embedding": [1.0, 0.0, 0.0]},          # precomputed, matches query
        {"id": "b", "source_id": "s", "kind": "sql", "columns": []},  # needs embed
    ]

    fake = types.ModuleType("rag.embed")
    embed_calls = {"texts": None}

    async def embed_query(_q):
        return [1.0, 0.0, 0.0]

    async def embed_texts_direct(texts):
        embed_calls["texts"] = list(texts)
        return [[0.0, 1.0, 0.0] for _ in texts]

    fake.embed_query = embed_query
    fake.embed_texts_direct = embed_texts_direct
    monkeypatch.setitem(sys.modules, "rag.embed", fake)

    out = asyncio.run(ci._vector_search("find a", max_results=2, candidates=cands))

    # only "b" (no precomputed vec) was embedded on the fly
    assert embed_calls["texts"] is not None and len(embed_calls["texts"]) == 1
    # "a" (precomputed vec == query vec) ranks first
    assert out[0]["id"] == "a"


def test_small_source_skips_ranking(monkeypatch):
    monkeypatch.setattr(router, "_sources", {"db": _make_source(3)})

    called = {"n": 0}

    async def fake_vs(question, *, max_results, candidates=None, scope="ALL"):
        called["n"] += 1
        return candidates or []

    monkeypatch.setattr(ci, "_vector_search", fake_vs)

    out = asyncio.run(ci.select_datasets("anything", source_id="db", max_results=8))

    assert called["n"] == 0          # 3 <= cap 8 → no ranking
    assert len(out) == 3             # all returned
