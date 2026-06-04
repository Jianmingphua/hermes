import oracledb, os, json

with open(os.path.expanduser('~/.hermes/config/oracle_adb.json')) as f:
    c = json.load(f)

dsn = (
    f'(description=(address=(protocol=tcps)(port={c["port"]})'
    f'(host={c["host"]}))'
    f'(connect_data=(service_name={c["db_service_name"]}))'
    f'(security=(ssl_server_dn_match=no)))'
)
conn = oracledb.connect(user=c['user'], password=os.environ['ORACLE_ADMIN_PASSWORD'], dsn=dsn)
conn.cursor().execute('ALTER SESSION SET CURRENT_SCHEMA = VECTOR_STORE')
cur = conn.cursor()

# embedding_models
try:
    cur.execute('SELECT model_name, table_name, status FROM embedding_models')
    print('=== embedding_models ===')
    for r in cur.fetchall():
        print(r)
except Exception as e:
    print(f'embedding_models: {e}')

# emb_384 columns
try:
    cur.execute('SELECT column_name, data_type FROM all_tab_columns WHERE owner=\'VECTOR_STORE\' AND table_name=\'EMB_384\' ORDER BY column_id')
    print('\n=== emb_384 columns ===')
    for r in cur.fetchall():
        print(r)
except Exception as e:
    print(f'emb_384: {e}')

# memory_vectors columns
try:
    cur.execute('SELECT column_name, data_type FROM all_tab_columns WHERE owner=\'VECTOR_STORE\' AND table_name=\'MEMORY_VECTORS\' ORDER BY column_id')
    print('\n=== memory_vectors columns ===')
    for r in cur.fetchall():
        print(r)
except Exception as e:
    print(f'memory_vectors: {e}')

# Check all tables in VECTOR_STORE
try:
    cur.execute("SELECT table_name FROM all_tables WHERE owner='VECTOR_STORE' ORDER BY table_name")
    print('\n=== all VECTOR_STORE tables ===')
    for r in cur.fetchall():
        print(r[0])
except Exception as e:
    print(f'tables: {e}')

cur.close(); conn.close()
