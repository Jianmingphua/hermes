"""
Knowledge Base Ingestion Pipeline.

Flow:
  URL/text --> chunk --> embed --> store in KB schemas
  
Usage:
  from kb_ingest import ingest_url, ingest_text, ingest_file
  
Pipeline steps:
  1. Content extraction (web, text, PDF)
  2. Chunking (token-aware, overlap)
  3. Embedding generation (sentence-transformers, OpenAI, etc.)
  4. Storage (kb_documents + kb_chunks + document_embeddings)
  5. Tag extraction (auto-generate tags)
"""

import hashlib, json, time, os, sys
from datetime import datetime

# Add parent dir for oracle_db import
sys.path.insert(0, os.path.expanduser('~/.hermes/scripts'))

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text, chunk_size=512, overlap=50, separator="\n\n", min_chunk_size=100):
    """
    Split text into overlapping chunks.
    
    Args:
        text: Source text
        chunk_size: Target chunk size in characters
        overlap: Overlap between chunks in characters
        separator: Preferred split point
        min_chunk_size: Minimum chunk size (smaller trailing chunks are dropped)
        
    Returns:
        List of dicts: [{"text": "...", "start": N, "end": N, "index": N}]
    """
    if not text or not text.strip():
        return []
    
    text = text.strip()
    chunks = []
    start = 0
    index = 0
    
    while start < len(text):
        end = min(start + chunk_size, len(text))
        
        # Try to break at separator near the chunk boundary
        if end < len(text):
            search_start = start + int(chunk_size * 0.7)
            sep_pos = text.rfind(separator, search_start, end)
            if sep_pos > start:
                end = sep_pos + len(separator)
            else:
                for sep in ['. ', '.\n', '? ', '! ', '\n']:
                    sent_pos = text.rfind(sep, search_start, end)
                    if sent_pos > start:
                        end = sent_pos + len(sep)
                        break
        
        chunk_slice = text[start:end].strip()
        
        # Only add if chunk meets minimum size (or it's the first chunk)
        if chunk_slice and (len(chunk_slice) >= min_chunk_size or index == 0):
            chunks.append({
                "text": chunk_slice,
                "start": start,
                "end": end,
                "index": index,
            })
            index += 1
        elif chunk_slice and chunks:
            # Merge small trailing chunk into previous
            chunks[-1]["text"] += " " + chunk_slice
            chunks[-1]["end"] = end
        
        next_start = end - overlap
        if next_start <= start:
            next_start = start + 1
        start = next_start
    
    return chunks

def estimate_tokens(text):
    """Rough token estimate: ~4 chars per token for English."""
    if not text:
        return 0
    return max(1, len(text) // 4)

# ---------------------------------------------------------------------------
# Embedding (pluggable)
# ---------------------------------------------------------------------------

class EmbeddingProvider:
    """Base class for embedding providers."""
    
    def embed(self, texts):
        """Embed a list of texts. Returns list of float lists."""
        raise NotImplementedError
    
    @property
    def dimension(self):
        raise NotImplementedError
    
    @property
    def model_name(self):
        raise NotImplementedError


class SklearnEmbeddingProvider(EmbeddingProvider):
    """
    Lightweight embedding using HashingVectorizer + random projection.
    Deterministic, no model download, fixed 384-dim output.
    Uses a hash-based n-gram vectorizer projected to fixed dimensions.
    """
    
    def __init__(self, n_components=384):
        self._n_components = n_components
        self._projection = None
        self._rng = None
    
    def _get_projection(self, input_dim):
        import numpy as np
        if self._projection is None or self._projection.shape[1] != input_dim:
            rng = np.random.RandomState(42)
            # Achlioptas-style random projection matrix
            self._projection = rng.choice([-1, 1], size=(self._n_components, input_dim)) / np.sqrt(self._n_components)
        return self._projection
    
    def embed(self, texts):
        import numpy as np
        from sklearn.feature_extraction.text import HashingVectorizer
        from sklearn.preprocessing import normalize
        
        vectorizer = HashingVectorizer(
            n_features=2**14, analyzer='word', ngram_range=(1, 2),
            alternate_sign=False, norm='l2'
        )
        X = vectorizer.transform(texts).toarray()
        proj = self._get_projection(X.shape[1])
        reduced = (proj @ X.T).T
        reduced = normalize(reduced, norm='l2', axis=1)
        return [row.tolist() for row in reduced]
    
    @property
    def dimension(self):
        return self._n_components
    
    @property
    def model_name(self):
        return f"tfidf-rand-{self._n_components}"


class SentenceTransformerProvider(EmbeddingProvider):
    """Local sentence-transformers embedding (requires sentence-transformers + torch)."""
    
    def __init__(self, model_name="all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None
    
    def _load_model(self):
        if self._model is None:
            try:
                import torch
                # Force CPU if CUDA not available
                device = 'cuda' if torch.cuda.is_available() else 'cpu'
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name, device=device)
            except ImportError as e:
                raise RuntimeError(
                    f"sentence-transformers or torch not available: {e}. "
                    "Use provider='sklearn' as fallback."
                )
        return self._model
    
    def embed(self, texts):
        model = self._load_model()
        embeddings = model.encode(texts, show_progress_bar=False)
        return [e.tolist() for e in embeddings]
    
    @property
    def dimension(self):
        return self._load_model().get_sentence_embedding_dimension()
    
    @property
    def model_name(self):
        return self._model_name


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI API embedding."""
    
    def __init__(self, model_name="text-embedding-3-small", api_key=None):
        self._model_name = model_name
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client = None
    
    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self._api_key)
            except ImportError:
                raise RuntimeError("openai not installed. Run: pip install openai")
        return self._client
    
    def embed(self, texts):
        client = self._get_client()
        # Batch in groups of 100
        all_embeddings = []
        for i in range(0, len(texts), 100):
            batch = texts[i:i+100]
            resp = client.embeddings.create(input=batch, model=self._model_name)
            all_embeddings.extend([d.embedding for d in resp.data])
        return all_embeddings
    
    @property
    def dimension(self):
        dims = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072,
                "text-embedding-ada-002": 1024}
        return dims.get(self._model_name, 1536)
    
    @property
    def model_name(self):
        return self._model_name


def get_embedding_provider(provider="sklearn", model=None, **kwargs):
    """Factory for embedding providers."""
    if provider == "sklearn":
        # model can be an int dimension or None for default 384
        n = int(model) if model and str(model).isdigit() else 384
        return SklearnEmbeddingProvider(n_components=n)
    elif provider == "local":
        return SentenceTransformerProvider(model or "all-MiniLM-L6-v2")
    elif provider == "openai":
        return OpenAIEmbeddingProvider(model or "text-embedding-3-small", **kwargs)
    else:
        raise ValueError(f"Unknown provider: {provider}")

# ---------------------------------------------------------------------------
# Content Extraction
# ---------------------------------------------------------------------------

def extract_from_url(url, use_firecrawl=True):
    """Extract text content from a URL."""
    if use_firecrawl:
        # Use local Firecrawl instance
        import requests
        fc_url = os.environ.get("FIRECRAWL_API_URL", "http://localhost:3002")
        try:
            resp = requests.post(
                f"{fc_url}/v1/scrape",
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("data", {}).get("markdown", "")
                title = data.get("data", {}).get("metadata", {}).get("title", url)
                return {"title": title, "content": content, "source_url": url, "format": "markdown"}
        except Exception as e:
            print(f"Firecrawl failed ({e}), falling back to web_extract", flush=True)
    
    # Fallback: use web_extract
    try:
        from hermes_tools import web_extract
        result = web_extract(urls=[url])
        if result.get("results"):
            r = result["results"][0]
            return {"title": r.get("title", url), "content": r.get("content", ""), "source_url": url, "format": "markdown"}
    except:
        pass
    
    return None


def extract_from_file(filepath):
    """Extract text from a file (PDF, txt, md)."""
    import os
    ext = os.path.splitext(filepath)[1].lower()
    
    if ext == ".pdf":
        try:
            import pymupdf
            doc = pymupdf.open(filepath)
            text = ""
            for page in doc:
                text += page.get_text() + "\n"
            title = os.path.basename(filepath)
            return {"title": title, "content": text, "source_path": filepath, "format": "pdf"}
        except ImportError:
            raise RuntimeError("pymupdf not installed. Run: pip install pymupdf")
    
    elif ext in (".txt", ".md", ".markdown"):
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        title = os.path.basename(filepath)
        return {"title": title, "content": text, "source_path": filepath, "format": ext.lstrip(".")}
    
    else:
        raise ValueError(f"Unsupported file type: {ext}")

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def ingest_content(content, doc_type="text", title=None, source_url=None,
                   source_path=None, language="en", tags=None,
                   provider="sklearn", model=None, batch_size=32,
                   chunk_size=512, overlap=50):
    """
    Full ingestion pipeline: content -> chunks -> embeddings -> storage.
    
    Args:
        content: Text content to ingest
        doc_type: Type of document (text, web, pdf, markdown)
        title: Document title
        source_url: Original URL if applicable
        source_path: File path if applicable
        language: Content language
        tags: List of tags to attach
        provider: Embedding provider ("local" or "openai")
        model: Model name (None for default)
        batch_size: Embedding batch size
        
    Returns:
        dict with doc_id, chunk_count, embedding_count, duration_seconds
    """
    import oracle_db
    
    start_time = time.time()
    
    # 1. Chunk
    chunks = chunk_text(content, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        return {"error": "no content to chunk", "doc_id": None, "chunk_count": 0}
    
    print(f"  Chunked: {len(chunks)} chunks", flush=True)
    
    # 2. Store document
    meta = json.dumps({
        "char_count": len(content),
        "chunk_count": len(chunks),
        "chunk_size": chunk_size,
        "overlap": overlap,
    })
    doc_id = oracle_db.kb_ingest(
        doc_type=doc_type,
        title=title or "Untitled",
        content_text=content,
        source_url=source_url,
        language=language,
        meta=meta,
    )
    print(f"  Document stored: doc_id={doc_id}", flush=True)
    
    # 3. Store chunks
    conn = oracle_db._connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    for chunk in chunks:
        cur.execute(
            "INSERT INTO kb_chunks (doc_id, chunk_text, chunk_index, token_count, char_count) "
            "VALUES (:1, :2, :3, :4, :5)",
            [doc_id, chunk["text"], chunk["index"], estimate_tokens(chunk["text"]), len(chunk["text"])],
        )
    conn.commit()
    cur.execute("SELECT MAX(id) FROM kb_chunks WHERE doc_id = :1", [doc_id])
    max_chunk_id = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"  Chunks stored: {len(chunks)} rows", flush=True)
    
    # 4. Generate embeddings
    embedder = get_embedding_provider(provider, model)
    model_name = embedder.model_name
    dim = embedder.dimension
    
    chunk_texts = [c["text"] for c in chunks]
    all_embeddings = []
    for i in range(0, len(chunk_texts), batch_size):
        batch = chunk_texts[i:i+batch_size]
        embeddings = embedder.embed(batch)
        all_embeddings.extend(embeddings)
    
    print(f"  Embeddings generated: {len(all_embeddings)} vectors (dim={dim})", flush=True)
    
    # 5. Store embeddings
    # Get the starting chunk_id
    conn = oracle_db._connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    cur.execute("SELECT id FROM kb_chunks WHERE doc_id = :1 ORDER BY chunk_index", [doc_id])
    chunk_ids = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    
    for chunk_db_id, embedding in zip(chunk_ids, all_embeddings):
        try:
            oracle_db.store_embedding(chunk_db_id, doc_id, embedding, model_name)
        except ValueError as e:
            print(f"  Warning: {e}", flush=True)
            # Fallback: store without index
            pass
    
    print(f"  Embeddings stored", flush=True)
    
    # 6. Store tags
    if tags:
        conn = oracle_db._connect("KNOWLEDGE_BASE")
        cur = conn.cursor()
        for tag in tags:
            cur.execute(
                "INSERT INTO kb_tags (doc_id, tag_name) VALUES (:1, :2)",
                [doc_id, tag],
            )
        conn.commit()
        cur.close()
        conn.close()
    
    duration = time.time() - start_time
    
    # Update metadata
    conn = oracle_db._connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    meta_update = json.dumps({
        "char_count": len(content),
        "chunk_count": len(chunks),
        "embedding_model": model_name,
        "embedding_dim": dim,
        "embedding_count": len(all_embeddings),
        "tags": tags or [],
        "ingest_duration_sec": round(duration, 2),
    })
    cur.execute("UPDATE kb_documents SET metadata_json = :1 WHERE id = :2", [meta_update, doc_id])
    conn.commit()
    cur.close()
    conn.close()
    
    result = {
        "doc_id": doc_id,
        "chunk_count": len(chunks),
        "embedding_count": len(all_embeddings),
        "embedding_model": model_name,
        "embedding_dim": dim,
        "duration_seconds": round(duration, 2),
    }
    print(f"  Done: {result}", flush=True)
    return result


def ingest_url(url, provider="local", tags=None, **kwargs):
    """Ingest content from a URL."""
    extracted = extract_from_url(url)
    if not extracted:
        return {"error": f"Failed to extract content from {url}"}
    
    return ingest_content(
        content=extracted["content"],
        doc_type="web",
        title=extracted.get("title"),
        source_url=url,
        tags=tags or [],
        provider=provider,
        **kwargs,
    )


def ingest_file(filepath, provider="local", tags=None, **kwargs):
    """Ingest content from a file."""
    extracted = extract_from_file(filepath)
    return ingest_content(
        content=extracted["content"],
        doc_type=extracted.get("format", "text"),
        title=extracted.get("title"),
        source_path=filepath,
        tags=tags or [],
        provider=provider,
        **kwargs,
    )


def ingest_text(text, title="Text", provider="local", tags=None, **kwargs):
    """Ingest raw text."""
    return ingest_content(
        content=text,
        doc_type="text",
        title=title,
        tags=tags or [],
        provider=provider,
        **kwargs,
    )
