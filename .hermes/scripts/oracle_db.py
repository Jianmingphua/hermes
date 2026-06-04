"""
Oracle ADB connection helper for Hermes agents.
Usage:
    import os; os.environ['ORACLE_ADMIN_PASSWORD'] = '...'
    from oracle_db import get_connection, search_memory, get_memory, set_memory
    from oracle_db import store_embedding, search_similar, get_embedding_table
"""

import os, json, oracledb

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _config():
    path = os.path.expanduser("~/.hermes/config/oracle_adb.json")
    with open(path) as f:
        return json.load(f)

def _connect(schema=None):
    """Open a connection (TLS, no wallet). Schema optional."""
    c = _config()
    dsn = (
        f"(description=(address=(protocol=tcps)(port={c['port']})"
        f"(host={c['host']}))"
        f"(connect_data=(service_name={c['db_service_name']}))"
        f"(security=(ssl_server_dn_match=no)))"
    )
    pwd = os.environ.get("ORACLE_ADMIN_PASSWORD", "")
    conn = oracledb.connect(user=c["user"], password=pwd, dsn=dsn)
    if schema:
        conn.cursor().execute(f"ALTER SESSION SET CURRENT_SCHEMA = {schema}")
    return conn

# alias for public use
get_connection = _connect

def _s(val):
    """Safely convert LOB/CLOB/None to string while connection is open."""
    if val is None:
        return None
    if hasattr(val, "read"):
        try:
            return val.read()
        except Exception:
            return None
    return str(val)

# ---------------------------------------------------------------------------
# Agent Memory (HERMES_MEMORY schema)
# ---------------------------------------------------------------------------

def search_memory(memory_type=None, key_pattern=None, limit=20):
    """Return list of dicts from agent_memory."""
    conn = _connect("HERMES_MEMORY")
    cur = conn.cursor()
    if memory_type and key_pattern:
        cur.execute(
            "SELECT key_name,value_text,value_json,source,confidence,updated_at "
            "FROM agent_memory WHERE memory_type=:1 AND UPPER(key_name) LIKE UPPER(:2) "
            "ORDER BY updated_at DESC FETCH FIRST :3 ROWS ONLY",
            [memory_type, f"%{key_pattern}%", limit],
        )
    elif memory_type:
        cur.execute(
            "SELECT key_name,value_text,value_json,source,confidence,updated_at "
            "FROM agent_memory WHERE memory_type=:1 "
            "ORDER BY updated_at DESC FETCH FIRST :2 ROWS ONLY",
            [memory_type, limit],
        )
    else:
        cur.execute(
            "SELECT memory_type,key_name,value_text,value_json,source,confidence "
            "FROM agent_memory ORDER BY memory_type,key_name "
            "FETCH FIRST :1 ROWS ONLY",
            [limit],
        )
    rows = [
        dict(key=r[0], value=_s(r[1]), json=_s(r[2]),
             source=r[3], confidence=r[4], updated_at=str(r[5]) if r[5] else None)
        for r in cur.fetchall()
    ]
    cur.close(); conn.close()
    return rows

def get_memory(memory_type, key):
    """Return dict or None."""
    conn = _connect("HERMES_MEMORY")
    cur = conn.cursor()
    cur.execute(
        "SELECT value_text,value_json,source,confidence "
        "FROM agent_memory WHERE memory_type=:1 AND key_name=:2",
        [memory_type, key],
    )
    r = cur.fetchone()
    if not r:
        cur.close(); conn.close()
        return None
    # Read LOBs BEFORE closing cursor/connection
    result = dict(value=_s(r[0]), json=_s(r[1]), source=r[2], confidence=r[3])
    cur.close(); conn.close()
    return result

def set_memory(memory_type, key, value=None, source="agent", confidence=1.0, json_value=None):
    """Upsert a memory row. Thin driver requires all bind positions to be unique."""
    conn = _connect("HERMES_MEMORY")
    cur = conn.cursor()
    binds = [memory_type, key, value, json_value, source, confidence,
             memory_type, key, value, json_value, source, confidence]
    cur.execute(
        "MERGE INTO agent_memory m USING DUAL ON (m.memory_type=:1 AND m.key_name=:2) "
        "WHEN MATCHED THEN UPDATE SET value_text=:3, value_json=:4, source=:5, "
        "confidence=:6, updated_at=SYSTIMESTAMP "
        "WHEN NOT MATCHED THEN INSERT (memory_type, key_name, value_text, value_json, source, confidence) "
        "VALUES (:7, :8, :9, :10, :11, :12)",
        binds,
    )
    conn.commit(); cur.close(); conn.close()

# ---------------------------------------------------------------------------
# Vector Store (VECTOR_STORE schema)
# ---------------------------------------------------------------------------

def get_embedding_table(model_name):
    """Return table_name for an active model, or None."""
    conn = _connect("VECTOR_STORE")
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name FROM embedding_models "
        "WHERE model_name=:1 AND status='active'", [model_name],
    )
    r = cur.fetchone()
    cur.close(); conn.close()
    return r[0] if r else None

def to_vector_str(embedding):
    return "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"

def get_onnx_model_name(model_name):
    """Map an embedding model name to its in-DB ONNX mining model name.

    ONNX models are registered via DBMS_VECTOR.LOAD_ONNX_MODEL and appear in
    user_mining_models with algorithm='ONNX'. Returns the Oracle model name
    (e.g. 'ALL_MINILM_L6_V2') or None if not loaded as ONNX.
    """
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT model_name FROM user_mining_models "
        "WHERE UPPER(model_name) = UPPER(:1) AND algorithm = 'ONNX' "
        "AND mining_function = 'EMBEDDING'",
        [model_name],
    )
    r = cur.fetchone()
    cur.close(); conn.close()
    return r[0] if r else None

def embed_text(text, onnx_model=None):
    """Generate an embedding for *text* using the in-DB ONNX model.

    If *onnx_model* is None, tries to auto-detect the ONNX model for the
    default sentence-transformers/all-MiniLM-L6-v2 model.

    Returns a list of floats (384 dims, L2-normalized).
    """
    if onnx_model is None:
        onnx_model = get_onnx_model_name('ALL_MINILM_L6_V2')
    if onnx_model is None:
        raise RuntimeError(
            "No ONNX embedding model loaded in the database. "
            "Load one with: DBMS_VECTOR.LOAD_ONNX_MODEL(...)"
        )
    conn = _connect()
    cur = conn.cursor()
    result = cur.var(oracledb.DB_TYPE_VECTOR)
    cur.execute(
        """
        BEGIN
            :result := DBMS_VECTOR.UTL_TO_EMBEDDING(
                :data, JSON('{"model":"' || :model || '"}')
            );
        END;
        """,
        {"result": result, "data": text, "model": onnx_model},
    )
    vec = list(result.getvalue())
    cur.close(); conn.close()
    return vec

def embed_texts(texts, onnx_model=None):
    """Batch embedding. Returns list of float lists.

    Calls UTL_TO_EMBEDDING once per text in a single connection.
    Each call takes ~70ms. For large batches, consider using
    DBMS_VECTOR_DATABASE.GENERATE_EMBEDDING with JSON_ARRAY_T input.
    """
    if onnx_model is None:
        onnx_model = get_onnx_model_name('ALL_MINILM_L6_V2')
    if onnx_model is None:
        raise RuntimeError("No ONNX embedding model loaded.")
    conn = _connect()
    cur = conn.cursor()
    results = []
    for text in texts:
        result = cur.var(oracledb.DB_TYPE_VECTOR)
        cur.execute(
            """
            BEGIN
                :result := DBMS_VECTOR.UTL_TO_EMBEDDING(
                    :data, JSON('{"model":"' || :model || '"}')
                );
            END;
            """,
            {"result": result, "data": text, "model": onnx_model},
        )
        results.append(list(result.getvalue()))
    cur.close(); conn.close()
    return results

def embed_text_sql(text, onnx_model=None):
    """Generate embedding using VECTOR_EMBEDDING SQL function.

    Uses the SQL-level VECTOR_EMBEDDING function with unquoted model name.
    Model name must be an unquoted identifier (not a string literal).

    Example SQL: SELECT VECTOR_EMBEDDING(ALL_MINILM_L6_V2 USING 'text' AS DATA) FROM dual
    """
    if onnx_model is None:
        onnx_model = get_onnx_model_name('ALL_MINILM_L6_V2')
    if onnx_model is None:
        raise RuntimeError("No ONNX embedding model loaded.")
    conn = _connect()
    cur = conn.cursor()
    # VECTOR_EMBEDDING takes unquoted model name as identifier
    cur.execute(
        f"SELECT VECTOR_EMBEDDING({onnx_model} USING :data AS DATA) FROM dual",
        {"data": text},
    )
    row = cur.fetchone()
    vec = list(row[0]) if row and row[0] else None
    cur.close(); conn.close()
    return vec

def store_embedding(chunk_id, doc_id, embedding, model_name):
    """Upsert an embedding into the correct dimension table."""
    table = get_embedding_table(model_name)
    if not table:
        raise ValueError(f"Unknown or inactive model: {model_name}")
    vs = to_vector_str(embedding)
    conn = _connect("VECTOR_STORE")
    cur = conn.cursor()
    # Thin driver requires all bind positions unique
    cur.execute(
        f"MERGE INTO {table} t USING DUAL ON (t.chunk_id=:1 AND t.model_name=:2) "
        f"WHEN MATCHED THEN UPDATE SET embedding=TO_VECTOR(:3),doc_id=:4 "
        f"WHEN NOT MATCHED THEN INSERT (chunk_id,doc_id,embedding,model_name) "
        f"VALUES (:5,:6,TO_VECTOR(:7),:8)",
        [chunk_id, model_name, vs, doc_id, chunk_id, doc_id, vs, model_name],
    )
    conn.commit(); cur.close(); conn.close()

def store_text_embedding(chunk_id, doc_id, text, onnx_model=None, model_name='all-MiniLM-L6-v2'):
    """Embed *text* using in-DB ONNX model and store the vector.

    Convenience wrapper that calls embed_text() + store_embedding().
    """
    vec = embed_text(text, onnx_model=onnx_model)
    store_embedding(chunk_id, doc_id, vec, model_name)

def search_similar(query_embedding, model_name, top_k=5):
    """ANN search. Returns list of dicts with chunk_id, doc_id, distance."""
    table = get_embedding_table(model_name)
    if not table:
        raise ValueError(f"Unknown or inactive model: {model_name}")
    vs = to_vector_str(query_embedding)
    conn = _connect("VECTOR_STORE")
    cur = conn.cursor()
    cur.execute(
        f"SELECT chunk_id,doc_id,"
        f"VECTOR_DISTANCE(embedding,TO_VECTOR(:1),COSINE) AS dist "
        f"FROM {table} WHERE model_name=:2 "
        f"ORDER BY dist FETCH FIRST :3 ROWS ONLY",
        [vs, model_name, top_k],
    )
    results = [dict(chunk_id=r[0], doc_id=r[1], distance=r[2]) for r in cur.fetchall()]
    cur.close(); conn.close()
    return results

def search_similar_by_text(query_text, model_name='ALL_MINILM_L6_V2', top_k=5):
    """Embed *query_text* in-DB and search for similar vectors.

    Requires an active ONNX model for the query embedding and
    stored vectors in the VECTOR_STORE for comparison.
    """
    query_vec = embed_text(query_text, onnx_model=model_name)
    # Determine which table to search based on the ONNX model's dimension
    # ALL_MINILM_L6_V2 = 384 dims = EMB_384 table
    dim = len(query_vec)
    table_map = {384: 'EMB_384', 768: 'EMB_768', 1024: 'EMB_1024', 1536: 'EMB_1536', 3072: 'EMB_3072'}
    table = table_map.get(dim)
    if not table:
        raise ValueError(f"No vector table for dimension {dim}")
    vs = to_vector_str(query_vec)
    conn = _connect("VECTOR_STORE")
    cur = conn.cursor()
    cur.execute(
        f"SELECT chunk_id,doc_id,"
        f"VECTOR_DISTANCE(embedding,TO_VECTOR(:1),COSINE) AS dist "
        f"FROM {table} "
        f"ORDER BY dist FETCH FIRST :2 ROWS ONLY",
        [vs, top_k],
    )
    results = [dict(chunk_id=r[0], doc_id=r[1], distance=r[2]) for r in cur.fetchall()]
    cur.close(); conn.close()
    return results

# ---------------------------------------------------------------------------
# Knowledge Base (KNOWLEDGE_BASE schema)
# ---------------------------------------------------------------------------

def kb_ingest(doc_type, title, content_text, source_url=None, language="en", meta=None):
    """Insert a document and return its ID."""
    conn = _connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    out_id = cur.var(oracledb.NUMBER)
    cur.execute(
        "INSERT INTO kb_documents (doc_type,title,source_url,content_text,language,metadata_json) "
        "VALUES (:1,:2,:3,:4,:5,:6) RETURNING id INTO :7",
        [doc_type, title, source_url, content_text, language, meta, out_id],
    )
    conn.commit()
    pk = int(out_id.getvalue()[0])
    cur.close(); conn.close()
    return pk

def kb_search_fulltext(term, limit=10):
    """Oracle Text CONTAINS search. Returns list of dicts."""
    conn = _connect("KNOWLEDGE_BASE")
    cur = conn.cursor()
    cur.execute(
        "SELECT id,title,SCORE(1) AS relevance "
        "FROM kb_documents WHERE CONTAINS(content_text,:1,1)>0 "
        "ORDER BY SCORE(1) DESC FETCH FIRST :2 ROWS ONLY",
        [term, limit],
    )
    results = [dict(id=r[0], title=r[1], relevance=r[2]) for r in cur.fetchall()]
    cur.close(); conn.close()
    return results
