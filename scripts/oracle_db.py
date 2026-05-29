"""
Oracle ADB Helper Module
========================
Provides connection management, memory operations, knowledge base operations,
and vector embeddings using OpenRouter API.

Embedding provider: OpenRouter (nvidia/llama-nemotron-embed-vl-1b-v2:free)
Vector store: Oracle ADB VECTOR_STORE.emb_2048 (2048-dim FLOAT32)

Usage:
    from oracle_db import get_connection, embed_text, store_embedding, search_similar

Environment:
    ORACLE_ADMIN_PASSWORD - from /opt/hermes/.hermes/.env (auto-loaded)
    OPENROUTER_API_KEY    - from /opt/hermes/.hermes/.env (auto-loaded)
"""

import os
import json
import pathlib
import urllib.request
import urllib.error

try:
    import oracledb
except ImportError:
    raise ImportError("oracledb not installed. Use /opt/hermes/venv/bin/python3.12")

# ── Config ──────────────────────────────────────────────────────────────────

ENV_FILE = pathlib.Path("/opt/hermes/.hermes/.env")
_DSN = (
    "(description=(retry_count=20)(retry_delay=3)"
    "(address=(protocol=tcps)(port=1522)(host=adb.us-ashburn-1.oraclecloud.com))"
    "(connect_data=(service_name=g6a7b480616ec3a_n4z3kpiqi4nucu7o_high.adb.oraclecloud.com))"
    "(security=(ssl_server_dn_match=no)))"
)

EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2"
EMBED_DIM = 2048
EMBED_TABLE = "emb_2048"
OPENROUTER_EMBED_URL = "https://openrouter.ai/api/v1/embeddings"

_EMBEDDING_TABLE_MAP = {
    "nvidia/llama-nemotron-embed-vl-1b-v2": "emb_2048",
    "nvidia/llama-nemotron-embed-vl-1b-v2:free": "emb_2048",
}

# ── Env helpers ─────────────────────────────────────────────────────────────

def _load_env():
    """Load env vars from .env if not already set."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _get_openrouter_key():
    """Get OpenRouter API key from env."""
    _load_env()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY not set. "
            "Add it to /opt/hermes/.hermes/.env"
        )
    return key


def _get_oracle_password():
    """Get Oracle password from env."""
    _load_env()
    pwd = os.environ.get("ORACLE_ADMIN_PASSWORD")
    if not pwd:
        raise ValueError(
            "ORACLE_ADMIN_PASSWORD not set. "
            "Check /opt/hermes/.hermes/.env"
        )
    return pwd


# ── Connection ──────────────────────────────────────────────────────────────

def get_connection(schema=None):
    """
    Get an Oracle connection. Optionally switch to a schema.

    Args:
        schema: 'HERMES_MEMORY', 'KNOWLEDGE_BASE', 'VECTOR_STORE', or None

    Returns:
        oracledb.Connection
    """
    pwd = _get_oracle_password()
    conn = oracledb.connect(user="ADMIN", password=pwd, dsn=_DSN)
    if schema:
        cur = conn.cursor()
        cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {schema}")
        cur.close()
    return conn


# ── OpenRouter Embedding ────────────────────────────────────────────────────

def embed_text(text: str, input_type: str = "passage") -> list:
    """
    Generate a 2048-dim embedding via OpenRouter API.

    Args:
        text: Text to embed
        input_type: 'passage' (for storing documents) or 'query' (for searching)

    Returns:
        List of 2048 floats (the embedding vector)
    """
    key = _get_openrouter_key()
    payload = json.dumps({
        "model": EMBED_MODEL,
        "input": text,
        "input_type": input_type,
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_EMBED_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if "data" in data and len(data["data"]) > 0:
        return data["data"][0]["embedding"]
    raise ValueError(f"Unexpected OpenRouter response: {data}")


def embed_texts(texts: list, input_type: str = "passage") -> list:
    """
    Embed multiple texts. Calls OpenRouter one at a time.

    Args:
        texts: List of strings
        input_type: 'passage' or 'query'

    Returns:
        List of embedding vectors (each a list of 2048 floats)
    """
    return [embed_text(t, input_type) for t in texts]


# ── Vector serialization ───────────────────────────────────────────────────

def to_vector_str(embedding_list: list) -> str:
    """Convert a list of floats to an Oracle VECTOR string literal.

    Example: [0.1, 0.2, 0.3] -> '[0.100000,0.200000,0.300000]'
    """
    parts = []
    for v in embedding_list:
        parts.append(f"{float(v):.6f}")
    return "[" + ",".join(parts) + "]"


# ── Memory operations ───────────────────────────────────────────────────────

def search_memory(type_filter=None, key=None, limit=10):
    """Search HERMES_MEMORY.agent_memory table."""
    conn = get_connection("HERMES_MEMORY")
    cur = conn.cursor()
    if type_filter and key:
        cur.execute(
            "SELECT memory_type, memory_key, memory_value, json_value, source, confidence "
            "FROM agent_memory WHERE memory_type = :1 AND memory_key = :2",
            [type_filter, key]
        )
    elif type_filter:
        cur.execute(
            "SELECT memory_type, memory_key, memory_value, json_value, source, confidence "
            "FROM agent_memory WHERE memory_type = :1 FETCH FIRST :2 ROWS ONLY",
            [type_filter, limit]
        )
    else:
        cur.execute(
            "SELECT memory_type, memory_key, memory_value, json_value, source, confidence "
            "FROM agent_memory FETCH FIRST :1 ROWS ONLY",
            [limit]
        )
    rows = []
    for r in cur.fetchall():
        rows.append({
            "memory_type": r[0], "memory_key": r[1],
            "memory_value": r[2], "json_value": r[3],
            "source": r[4], "confidence": r[5],
        })
    cur.close()
    conn.close()
    return rows


def get_memory(memory_type, key):
    """Get a single memory entry."""
    conn = get_connection("HERMES_MEMORY")
    cur = conn.cursor()
    cur.execute(
        "SELECT memory_type, memory_key, memory_value, json_value, source, confidence "
        "FROM agent_memory WHERE memory_type = :1 AND memory_key = :2",
        [memory_type, key]
    )
    r = cur.fetchone()
    cur.close()
    conn.close()
    if r is None:
        return None
    return {
        "memory_type": r[0], "memory_key": r[1],
        "memory_value": r[2], "json_value": r[3],
        "source": r[4], "confidence": r[5],
    }


def set_memory(memory_type, key, value, source=None, confidence=1.0):
    """Upsert a memory entry. Handles MERGE bind-uniqueness requirement."""
    conn = get_connection("HERMES_MEMORY")
    cur = conn.cursor()

    json_val = None
    try:
        json_val = json.dumps(value)
    except (TypeError, ValueError):
        pass

    # MERGE requires unique bind positions: :1-:6 for MATCHED, :7-:12 for NOT MATCHED
    binds = [
        memory_type, key, value, json_val, source, confidence,       # MATCHED :1-:6
        memory_type, key, value, json_val, source, confidence,       # NOT MATCHED :7-:12
    ]

    cur.execute("""
        MERGE INTO agent_memory t
        USING (SELECT :1 AS mt, :2 AS mk FROM dual) s
        ON (t.memory_type = s.mt AND t.memory_key = s.mk)
        WHEN MATCHED THEN UPDATE SET
            memory_value = :3, json_value = :4, source = :5, confidence = :6
        WHEN NOT MATCHED THEN INSERT
            (memory_type, memory_key, memory_value, json_value, source, confidence)
        VALUES (:7, :8, :9, :10, :11, :12)
    """, binds)

    conn.commit()
    cur.close()
    conn.close()


# ── Vector store operations ────────────────────────────────────────────────

def get_embedding_table(model_name=None):
    """Get the vector table name for a given model."""
    if model_name is None:
        model_name = EMBED_MODEL
    return _EMBEDDING_TABLE_MAP.get(model_name, f"emb_{EMBED_DIM}")


def store_embedding(chunk_id, doc_id, embedding_list, text=None, model_name=None):
    """
    Store an embedding vector in the appropriate table.

    Args:
        chunk_id: Unique chunk identifier
        doc_id: Document identifier
        embedding_list: List of floats (the vector)
        text: Optional source text (stored in chunk_text CLOB)
        model_name: Model identifier (default: EMBED_MODEL)

    Returns:
        The new row ID
    """
    if model_name is None:
        model_name = EMBED_MODEL
    table = get_embedding_table(model_name)
    vec_str = to_vector_str(embedding_list)

    conn = get_connection("VECTOR_STORE")
    cur = conn.cursor()

    # Use MERGE for upsert by chunk_id
    cur.execute(f"""
        MERGE INTO {table} t
        USING (SELECT :1 AS cid FROM dual) s
        ON (t.chunk_id = s.cid)
        WHEN MATCHED THEN UPDATE SET
            doc_id = :2, chunk_text = :3, embedding = VECTOR(:4, {EMBED_DIM}, FLOAT32),
            model_name = :5, created_at = CURRENT_TIMESTAMP
        WHEN NOT MATCHED THEN INSERT
            (chunk_id, doc_id, chunk_text, embedding, model_name)
        VALUES (:6, :7, :8, VECTOR(:9, {EMBED_DIM}, FLOAT32), :10)
    """, [chunk_id, doc_id, text, vec_str, model_name,
          chunk_id, doc_id, text, vec_str, model_name])

    # Get the ID
    cur.execute(f"SELECT id FROM {table} WHERE chunk_id = :1", [chunk_id])
    row_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()
    return row_id


def store_text_embedding(chunk_id, doc_id, text, model_name=None):
    """
    Embed text via OpenRouter and store in one step.

    Args:
        chunk_id: Unique chunk identifier
        doc_id: Document identifier
        text: Text to embed and store
        model_name: Model identifier (default: EMBED_MODEL)

    Returns:
        The new row ID
    """
    embedding = embed_text(text, input_type="document")
    return store_embedding(chunk_id, doc_id, embedding, text, model_name)


def search_similar(query_embedding, model_name=None, top_k=5, max_distance=None):
    """
    Find similar vectors by cosine distance.

    Args:
        query_embedding: List of floats (query vector)
        model_name: Model identifier (default: EMBED_MODEL)
        top_k: Max results
        max_distance: Optional distance threshold (0-2 for cosine)

    Returns:
        List of dicts with chunk_id, doc_id, distance, chunk_text
    """
    if model_name is None:
        model_name = EMBED_MODEL
    table = get_embedding_table(model_name)
    vec_str = to_vector_str(query_embedding)

    conn = get_connection("VECTOR_STORE")
    cur = conn.cursor()

    distance_clause = ""
    binds = [vec_str, top_k]
    if max_distance is not None:
        distance_clause = "AND VECTOR_DISTANCE(embedding, VECTOR(:1, :2, FLOAT32), COSINE) <= :3"
        binds.insert(2, EMBED_DIM)
        binds.insert(3, max_distance)

    cur.execute(f"""
        SELECT chunk_id, doc_id,
               VECTOR_DISTANCE(embedding, VECTOR(:1, {EMBED_DIM}, FLOAT32), COSINE) AS dist
        FROM {table}
        WHERE embedding IS NOT NULL
        {distance_clause}
        ORDER BY dist ASC
        FETCH FIRST :{len(binds)} ROWS ONLY
    """, binds)

    results = []
    for r in cur.fetchall():
        results.append({
            "chunk_id": r[0],
            "doc_id": r[1],
            "distance": r[2],
        })

    cur.close()
    conn.close()
    return results


def search_similar_by_text(query_text, model_name=None, top_k=5, max_distance=None):
    """
    Embed a query text and find similar vectors.

    Args:
        query_text: Text to search for
        model_name: Model identifier (default: EMBED_MODEL)
        top_k: Max results
        max_distance: Optional distance threshold

    Returns:
        List of dicts with chunk_id, doc_id, distance
    """
    query_embedding = embed_text(query_text, input_type="query")
    return search_similar(query_embedding, model_name, top_k, max_distance)


# ── Knowledge base operations ───────────────────────────────────────────────

def kb_ingest(source_type, title, content, source_url=None, tags=None):
    """Ingest a document into KNOWLEDGE_BASE.kb_documents."""
    conn = get_connection("KNOWLEDGE_BASE")
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO kb_documents (source_type, title, content, source_url, tags)
        VALUES (:1, :2, :3, :4, :5)
        RETURNING doc_id INTO :6
    """, [source_type, title, content, source_url,
          json.dumps(tags) if tags else None,
          cur.var(oracledb.NUMBER)])

    doc_id = int(cur.fetchone()[0])
    conn.commit()
    cur.close()
    conn.close()
    return doc_id


def kb_search_fulltext(query, limit=10):
    """Full-text search in KNOWLEDGE_BASE.kb_documents."""
    conn = get_connection("KNOWLEDGE_BASE")
    cur = conn.cursor()
    cur.execute("""
        SELECT doc_id, title, source_type
        FROM kb_documents
        WHERE CONTAINS(content, :1) > 0
        ORDER BY CONTAINS(content, :1) DESC
        FETCH FIRST :2 ROWS ONLY
    """, [query, limit])
    results = [{"doc_id": r[0], "title": r[1], "source_type": r[2]} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return results
