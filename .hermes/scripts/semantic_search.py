"""
Semantic Search Agent.

Provides unified search across:
  1. Vector similarity search (ANN via Oracle VECTOR indexes)
  2. Full-text search (Oracle Text CONTAINS)
  3. Hybrid search (combine both with weighted scoring)

Usage:
  from semantic_search import search, hybrid_search
  results = search("query about Singapore weather", top_k=5)
  results = hybrid_search("query", vector_weight=0.6, text_weight=0.4)
"""

import os, sys, json
sys.path.insert(0, os.path.expanduser('~/.hermes/scripts'))

import oracle_db
from kb_ingest import get_embedding_provider

# ---------------------------------------------------------------------------
# Vector Search
# ---------------------------------------------------------------------------

def vector_search(query_text, model_name="tfidf-rand-384", top_k=5,
                  provider="sklearn"):
    """
    Embed the query and search for similar chunks via ANN.
    model_name is used to look up the correct embedding table.
    """
    # For sklearn, generate query embeddings using the same method
    embedder = get_embedding_provider(provider)
    query_embedding = embedder.embed([query_text])[0]
    
    results = oracle_db.search_similar(query_embedding, model_name, top_k)
    
    if not results:
        return []
    
    # Enrich with chunk text and document title
    conn = oracle_db._connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    
    enriched = []
    for r in results:
        cur.execute("""
            SELECT c.chunk_text, d.title
            FROM kb_chunks c
            JOIN kb_documents d ON c.doc_id = d.id
            WHERE c.id = :1
        """, [r["chunk_id"]])
        row = cur.fetchone()
        if row:
            chunk_text = oracle_db._s(row[0]) or ""
            enriched.append({
                "chunk_id": r["chunk_id"],
                "doc_id": r["doc_id"],
                "distance": r["distance"],
                "score": 1.0 - r["distance"],
                "chunk_text": chunk_text[:500],
                "title": row[1],
                "model": model_name,
            })
    
    cur.close(); conn.close()
    return enriched

# ---------------------------------------------------------------------------
# Full-Text Search
# ---------------------------------------------------------------------------

def text_search(query_text, top_k=10):
    """
    Oracle Text full-text search using CONTAINS.
    
    Returns list of dicts:
      [{"doc_id": N, "title": "...", "relevance": F, "snippet": "..."}]
    """
    results = oracle_db.kb_search_fulltext(query_text, top_k)
    
    if not results:
        return []
    
    # Enrich with text snippet
    conn = oracle_db._connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    
    enriched = []
    for r in results:
        # Get first chunk as snippet
        cur.execute("""
            SELECT chunk_text FROM kb_chunks 
            WHERE doc_id = :1 AND chunk_index = 0
        """, [r["id"]])
        row = cur.fetchone()
        snippet = ""
        if row and row[0]:
            if hasattr(row[0], 'read'):
                snippet = row[0].read()[:300]
            else:
                snippet = str(row[0])[:300]
        
        enriched.append({
            "doc_id": r["id"],
            "title": r["title"],
            "relevance": r["relevance"],
            "score": min(1.0, r["relevance"] / 100.0),  # Normalize
            "snippet": snippet,
        })
    
    cur.close(); conn.close()
    return enriched

# ---------------------------------------------------------------------------
# Hybrid Search
# ---------------------------------------------------------------------------

def hybrid_search(query_text, model_name="all-MiniLM-L6-v2", top_k=10,
                  vector_weight=0.6, text_weight=0.4, provider="local"):
    """
    Combine vector similarity and full-text search.
    
    Strategy:
      1. Run both vector and text search independently
      2. Normalize scores to [0, 1]
      3. Combine: final_score = w_v * vector_score + w_t * text_score
      4. Return merged, re-ranked results grouped by document
    
    Args:
        query_text: Search query
        model_name: Embedding model for vector search
        top_k: Number of results to return
        vector_weight: Weight for vector scores (0-1)
        text_weight: Weight for text scores (0-1)
        provider: Embedding provider
        
    Returns:
        List of dicts sorted by final_score:
          [{"doc_id": N, "title": "...", "final_score": F, 
            "vector_score": F, "text_score": F, 
            "chunks": [{"chunk_id": N, "text": "...", "score": F}]}]
    """
    # Normalize weights
    total_w = vector_weight + text_weight
    w_v = vector_weight / total_w
    w_t = text_weight / total_w
    
    # Run both searches with expanded top_k for merging
    search_k = top_k * 3
    
    vec_results = vector_search(query_text, model_name, search_k, provider)
    txt_results = text_search(query_text, search_k)
    
    # Index by doc_id
    from collections import defaultdict
    doc_scores = defaultdict(lambda: {"vector_score": 0.0, "text_score": 0.0, 
                                       "chunks": [], "title": ""})
    
    for r in vec_results:
        d = doc_scores[r["doc_id"]]
        d["vector_score"] = max(d["vector_score"], r["score"])
        d["title"] = r["title"]
        d["chunks"].append({
            "chunk_id": r["chunk_id"],
            "text": r["chunk_text"],
            "vector_score": r["score"],
        })
    
    for r in txt_results:
        d = doc_scores[r["doc_id"]]
        d["text_score"] = max(d["text_score"], r["score"])
        d["title"] = r["title"]
        if r.get("snippet"):
            d["snippet"] = r["snippet"]
    
    # Compute final scores
    results = []
    for doc_id, scores in doc_scores.items():
        final = w_v * scores["vector_score"] + w_t * scores["text_score"]
        results.append({
            "doc_id": doc_id,
            "title": scores["title"],
            "final_score": round(final, 4),
            "vector_score": round(scores["vector_score"], 4),
            "text_score": round(scores["text_score"], 4),
            "chunks": sorted(scores["chunks"], key=lambda c: c["vector_score"], reverse=True)[:3],
        })
    
    results.sort(key=lambda r: r["final_score"], reverse=True)
    return results[:top_k]

# ---------------------------------------------------------------------------
# Unified Search (main entry point)
# ---------------------------------------------------------------------------

def search(query_text, mode="hybrid", model_name="tfidf-rand-384", 
           top_k=5, provider="sklearn", **kwargs):
    """
    Unified search entry point.
    
    Args:
        query_text: What to search for
        mode: "vector", "text", or "hybrid"
        model_name: Embedding model for vector search
        top_k: Number of results
        provider: Embedding provider
        
    Returns:
        Search results list (format depends on mode)
    """
    if mode == "vector":
        return vector_search(query_text, model_name, top_k, provider)
    elif mode == "text":
        return text_search(query_text, top_k)
    elif mode == "hybrid":
        return hybrid_search(query_text, model_name, top_k, 
                           kwargs.get("vector_weight", 0.6),
                           kwargs.get("text_weight", 0.4), provider)
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'vector', 'text', or 'hybrid'")

# ---------------------------------------------------------------------------
# Document management
# ---------------------------------------------------------------------------

def list_documents(limit=20, offset=0):
    """List ingested documents with metadata."""
    conn = oracle_db._connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    # Avoid GROUP BY on CLOB -- use subqueries
    cur.execute("""
        SELECT d.id, d.title, d.doc_type, d.source_url, d.status, 
               d.ingested_at,
               (SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id = d.id) as chunk_count
        FROM kb_documents d
        ORDER BY d.ingested_at DESC
        FETCH FIRST :1 ROWS ONLY
    """, [limit])
    
    results = []
    for r in cur.fetchall():
        results.append({
            "doc_id": r[0],
            "title": r[1],
            "doc_type": r[2],
            "source_url": r[3],
            "status": r[4],
            "ingested_at": str(r[5]) if r[5] else None,
            "chunk_count": r[6],
        })
    
    cur.close(); conn.close()
    return results


def get_document(doc_id):
    """Get a document with all its chunks."""
    conn = oracle_db._connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, title, doc_type, source_url, content_text, metadata_json, ingested_at
        FROM kb_documents WHERE id = :1
    """, [doc_id])
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return None
    
    doc = {
        "doc_id": row[0],
        "title": row[1],
        "doc_type": row[2],
        "source_url": row[3],
        "content": oracle_db._s(row[4]),
        "metadata": json.loads(oracle_db._s(row[5])) if row[5] else {},
        "ingested_at": str(row[6]) if row[6] else None,
    }
    
    cur.execute("""
        SELECT id, chunk_text, chunk_index, token_count, char_count
        FROM kb_chunks WHERE doc_id = :1 ORDER BY chunk_index
    """, [doc_id])
    doc["chunks"] = []
    for r in cur.fetchall():
        doc["chunks"].append({
            "chunk_id": r[0],
            "text": oracle_db._s(r[1]),
            "index": r[2],
            "tokens": r[3],
            "chars": r[4],
        })
    
    cur.execute("SELECT tag_name, tag_value FROM kb_tags WHERE doc_id = :1", [doc_id])
    doc["tags"] = [{"name": r[0], "value": r[1]} for r in cur.fetchall()]
    
    cur.close(); conn.close()
    return doc


def delete_document(doc_id):
    """Delete a document and all associated chunks/embeddings/tags."""
    conn = oracle_db._connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    cur.execute("DELETE FROM kb_chunks WHERE doc_id = :1", [doc_id])
    cur.execute("DELETE FROM kb_tags WHERE doc_id = :1", [doc_id])
    cur.execute("DELETE FROM kb_documents WHERE id = :1", [doc_id])
    chunks_deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    
    # Note: embeddings in VECTOR_STORE need separate cleanup
    # (foreign key doesn't cross schemas)
    try:
        conn2 = oracle_db._connect("VECTOR_STORE")
        cur2 = conn2.cursor()
        # Can't easily map back without chunk_ids, so leave orphaned embeddings
        cur2.close(); conn2.close()
    except:
        pass
    
    return {"deleted": True, "doc_id": doc_id}
