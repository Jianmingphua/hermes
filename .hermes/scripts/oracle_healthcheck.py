#!/opt/hermes/venv/bin/python3
"""
Oracle ADB 26ai Health Check Script
===================================
Performs a comprehensive health assessment of the Oracle Autonomous Database
in OCI us-ashburn-1 (TLS port 1522).

Checks:
  1. Connectivity
  2. Schema status (HERMES_MEMORY, KNOWLEDGE_BASE, VECTOR_STORE)
  3. Long-running queries (v$session / v$sql)
  4. Storage / quota usage
  5. Recent errors (v$alert / awr — best-effort)
  6. Performance — slow queries, blocking locks, hit ratios
  7. Embedding / vector table status
  8. Resource limits (sessions, processes, transactions utilization %)
  9. Memory — SGA/PGA breakdown, cache utilization
 10. I/O stats — file-level read/write, hot files, temp usage
 11. Undo health — undo retention, longest query needing undo
 12. Session insights — active by service, blocking/waiting, ASH top waits
 13. Configuration drift — critical non-default parameters, NLS settings
 14. Corruption check — v$database_block_corruption count
 15. Audit trail — failed login attempts (last 24h)
 16. Archive log — generation rate (GB/day)
 17. Transaction health — long-running transactions
 18. JSON + console report output

Usage:
    ORACLE_ADMIN_PASSWORD="<password>" python3 oracle_healthcheck.py
    ORACLE_ADMIN_PASSWORD="<password>" python3 oracle_healthcheck.py --quiet
    ORACLE_ADMIN_PASSWORD="<password>" python3 oracle_healthcheck.py --output /path/to/report.json

Requirements:
    - oracledb Python package (v4+)
    - ORACLE_ADMIN_PASSWORD environment variable
    - ~/.hermes/config/oracle_adb.json config file
"""

import os
import sys
import json
import time
import datetime
import traceback
from pathlib import Path
from contextlib import contextmanager

# ---------------------------------------------------------------------------#
# Globals                                                                     #
# ---------------------------------------------------------------------------#

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path.home() / ".hermes" / "config" / "oracle_adb.json"
REPORTS_DIR = Path.home() / ".hermes" / "reports"
TIMESTAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
REPORT_FILE = REPORTS_DIR / f"health_{TIMESTAMP}.json"

# Health status levels
STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_CRITICAL = "CRITICAL"
STATUS_SKIP = "SKIP"
STATUS_INFO = "INFO"

report = {
    "timestamp": datetime.datetime.now().isoformat(),
    "host": None,
    "port": None,
    "service_name": None,
    "checks": {},
    "overall_status": STATUS_OK,
    "errors": [],
}

# ---------------------------------------------------------------------------#
# Helpers                                                                     #
# ---------------------------------------------------------------------------#

def load_config():
    """Load Oracle ADB connection config from JSON file."""
    if not CONFIG_PATH.exists():
        print(f"❌ Config file not found: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    report["host"] = cfg.get("host")
    report["port"] = cfg.get("port")
    report["service_name"] = cfg.get("db_service_name", cfg.get("service_name"))
    return cfg


def build_dsn(cfg):
    """Build TLS DSN string (no wallet, ssl_server_dn_match=no)."""
    return (
        f"(description=(address=(protocol=tcps)(port={cfg['port']})"
        f"(host={cfg['host']}))"
        f"(connect_data=(service_name={cfg['db_service_name']}))"
        f"(security=(ssl_server_dn_match=no)))"
    )


@contextmanager
def get_connection(schema=None):
    """Context manager: yields an oracledb connection, optionally sets schema."""
    try:
        import oracledb
    except ImportError:
        print("❌ 'oracledb' package not found. Install with: pip install oracledb")
        sys.exit(1)

    cfg = load_config()
    dsn = build_dsn(cfg)
    pwd = os.environ.get("ORACLE_ADMIN_PASSWORD", "")
    if not pwd:
        print("❌ ORACLE_ADMIN_PASSWORD environment variable is not set.")
        sys.exit(1)

    conn = oracledb.connect(user=cfg["user"], password=pwd, dsn=dsn)
    if schema:
        conn.cursor().execute(f"ALTER SESSION SET CURRENT_SCHEMA = {schema}")
    try:
        yield conn
    finally:
        conn.close()


def execute_query(conn, sql, params=None):
    """Execute a SQL query and return rows as list of dicts."""
    cur = conn.cursor()
    try:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        cols = [c[0].lower() for c in cur.description] if cur.description else []
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return rows
    finally:
        cur.close()


def execute_scalar(conn, sql, params=None):
    """Execute a query returning a single scalar value."""
    cur = conn.cursor()
    try:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def safe_check(label, func, *args, **kwargs):
    """
    Run a check function, catch exceptions, return a check result dict.
    The check function should return a dict with at least a 'status' key.
    """
    result = {
        "label": label,
        "status": STATUS_OK,
        "details": {},
        "duration_ms": 0,
        "error": None,
    }
    start = time.time()
    try:
        check_data = func(*args, **kwargs)
        if isinstance(check_data, dict):
            result.update(check_data)
            if "status" not in check_data:
                result["status"] = STATUS_OK
    except Exception as e:
        result["status"] = STATUS_CRITICAL
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        report["errors"].append(f"{label}: {e}")
    result["duration_ms"] = round((time.time() - start) * 1000, 1)
    return result


def determine_worst_status(current, new):
    """Return the worse of two status levels."""
    order = {STATUS_OK: 0, STATUS_INFO: 1, STATUS_WARN: 2, STATUS_CRITICAL: 3, STATUS_SKIP: 1}
    return new if order.get(new, 0) > order.get(current, 0) else current


# ---------------------------------------------------------------------------#
# Check Functions                                                             #
# ---------------------------------------------------------------------------#

def check_connectivity():
    """1. Can we connect to the database?"""
    with get_connection() as conn:
        db_version = execute_scalar(conn, "SELECT banner FROM v$version WHERE ROWNUM = 1")
        db_name = execute_scalar(conn, "SELECT name FROM v$database")
        uptime = execute_scalar(conn, "SELECT STARTUP_TIME FROM v$instance")
        timezone_db = execute_scalar(conn, "SELECT DBTIMEZONE FROM dual")
        return {
            "status": STATUS_OK,
            "details": {
                "database_name": db_name,
                "db_version": str(db_version)[:200] if db_version else None,
                "instance_startup": str(uptime) if uptime else None,
                "timezone": str(timezone_db) if timezone_db else None,
            },
        }


def check_schema_tables():
    """2. List tables and row counts in each schema."""
    schemas = ["HERMES_MEMORY", "KNOWLEDGE_BASE", "VECTOR_STORE"]
    schema_info = {}
    for schema in schemas:
        try:
            with get_connection(schema) as conn:
                tables = execute_query(
                    conn,
                    "SELECT table_name, num_rows FROM user_tables ORDER BY table_name",
                )
                table_data = []
                for t in tables:
                    tname = t.get("table_name")
                    estimated_rows = t.get("num_rows")
                    # Get exact count for small tables, estimate for large ones
                    try:
                        if estimated_rows is not None and estimated_rows <= 10000:
                            exact = execute_scalar(
                                conn, f"SELECT COUNT(*) FROM {tname}"
                            )
                            row_count = exact
                            count_method = "exact"
                        else:
                            row_count = estimated_rows
                            count_method = "estimated"
                    except Exception:
                        row_count = estimated_rows
                        count_method = "estimated_fallback"
                    table_data.append({
                        "table": tname,
                        "row_count": row_count,
                        "count_method": count_method,
                    })
                schema_info[schema] = {
                    "tables": table_data,
                    "table_count": len(table_data),
                }
        except Exception as e:
            schema_info[schema] = {
                "tables": [],
                "table_count": 0,
                "error": str(e),
            }
    return {
        "status": STATUS_OK,
        "details": schema_info,
    }


def check_long_running_queries():
    """3. Find long-running queries from v$session and v$sql."""
    with get_connection() as conn:
        # Active sessions running for > 60 seconds
        long_sessions = execute_query(
            conn,
            """
            SELECT s.sid, s.serial#, s.username, s.program,
                   s.sql_id, s.event,
                   ROUND(s.last_call_et) AS seconds_running,
                   q.sql_text
            FROM v$session s
            LEFT JOIN v$sql q ON s.sql_id = q.sql_id
            WHERE s.status = 'ACTIVE'
              AND s.type = 'USER'
              AND s.last_call_et > 60
            ORDER BY s.last_call_et DESC
            FETCH FIRST 20 ROWS ONLY
            """,
        )
        # Top SQL by elapsed time
        top_sql = execute_query(
            conn,
            """
            SELECT sql_id, executions,
                   ROUND(elapsed_time / 1e6, 2) AS elapsed_sec,
                   ROUND(cpu_time / 1e6, 2) AS cpu_sec,
                   buffer_gets, disk_reads,
                   SUBSTR(sql_text, 1, 200) AS sql_preview
            FROM v$sql
            WHERE executions > 0
            ORDER BY elapsed_time DESC
            FETCH FIRST 10 ROWS ONLY
            """,
        )
        status = STATUS_WARN if long_sessions else STATUS_OK
        return {
            "status": status,
            "details": {
                "long_running_sessions": long_sessions,
                "long_running_count": len(long_sessions),
                "top_sql_by_elapsed": top_sql,
            },
        }


def check_storage_quota():
    """4. Storage usage and quota."""
    with get_connection() as conn:
        storage_info = {}
        # Tablespace usage
        try:
            tablespaces = execute_query(
                conn,
                """
                SELECT df.tablespace_name,
                       ROUND(SUM(df.bytes) / 1073741824, 2) AS allocated_gb,
                       ROUND(SUM(df.maxbytes) / 1073741824, 2) AS max_gb,
                       ROUND(SUM(df.bytes) / NULLIF(SUM(df.maxbytes), 0) * 100, 1) AS pct_allocated,
                       MAX(CASE WHEN df.autoextensible = 'YES' THEN 1 ELSE 0 END) AS autoextensible
                FROM dba_data_files df
                GROUP BY df.tablespace_name
                ORDER BY pct_allocated DESC NULLS LAST
                """,
            )
            storage_info["tablespaces"] = tablespaces
        except Exception as e:
            storage_info["tablespaces_error"] = str(e)

        # Real used-space metrics (more accurate than dba_data_files allocation)
        try:
            ts_metrics = execute_query(
                conn,
                """
                SELECT tablespace_name,
                       ROUND(used_space * 8192 / 1073741824) AS used_gb,
                       ROUND(tablespace_size * 8192 / 1073741824) AS total_gb,
                       ROUND((used_space / tablespace_size) * 100) AS pct_used
                FROM dba_tablespace_usage_metrics
                ORDER BY pct_used DESC NULLS LAST
                """,
            )
            storage_info["tablespace_metrics"] = ts_metrics
        except Exception as e:
            storage_info["tablespace_metrics_error"] = str(e)

        # ADB-specific: allocated / used storage
        try:
            adb_storage = execute_query(
                conn,
                """
                SELECT
                    allocated_storage_gb,
                    used_storage_gb,
                    ROUND(used_storage_gb / NULLIF(allocated_storage_gb, 0) * 100, 1) AS pct_used
                FROM (
                    SELECT
                        ROUND(SUM(maxbytes) / 1073741824, 2) AS allocated_storage_gb,
                        ROUND(SUM(bytes) / 1073741824, 2) AS used_storage_gb
                    FROM dba_data_files
                )
                """,
            )
            storage_info["adb_storage"] = adb_storage
        except Exception as e:
            storage_info["adb_storage_error"] = str(e)

        # Segment sizes (top 10)
        try:
            top_segments = execute_query(
                conn,
                """
                SELECT owner, segment_name, segment_type,
                       ROUND(bytes / 1048576, 2) AS size_mb
                FROM dba_segments
                ORDER BY bytes DESC
                FETCH FIRST 10 ROWS ONLY
                """,
            )
            storage_info["top_segments_mb"] = top_segments
        except Exception as e:
            storage_info["segments_error"] = str(e)

        # Determine status based on real used-space metrics (not just file allocation)
        status = STATUS_OK
        ts_metrics = storage_info.get("tablespace_metrics", [])
        if not ts_metrics:
            # Fallback to older allocation-based query if metrics view unavailable
            ts_metrics = storage_info.get("tablespaces", [])
        for ts in ts_metrics:
            pct = ts.get("pct_used") or ts.get("pct_allocated") or 0
            autoext = ts.get("autoextensible", 0)
            ts_name = ts.get("tablespace_name", "")
            # Skip UNDOTBS1 (undo) if autoextensible — ADB auto-manages it
            if ts_name == "UNDOTBS1" and autoext == 1:
                if pct > 95:
                    status = determine_worst_status(status, STATUS_WARN)
                continue
            if pct > 95:
                status = determine_worst_status(status, STATUS_CRITICAL)
            elif pct > 85:
                status = determine_worst_status(status, STATUS_WARN)

        return {
            "status": status,
            "details": storage_info,
        }


def check_recent_errors():
    """5. Recent errors from alert log / v$diag_alert_ext (best-effort)."""
    with get_connection() as conn:
        errors = {}
        # Try v$diag_alert_ext (ADR — available in 23ai/26ai)
        try:
            alert_entries = execute_query(
                conn,
                """
                SELECT ORIGINATING_TIMESTAMP, MESSAGE_TYPE, MESSAGE_TEXT
                FROM v$diag_alert_ext
                WHERE MESSAGE_TYPE IN (2, 3)
                  AND ORIGINATING_TIMESTAMP > SYSTIMESTAMP - INTERVAL '24' HOUR
                ORDER BY ORIGINATING_TIMESTAMP DESC
                FETCH FIRST 20 ROWS ONLY
                """,
            )
            errors["alert_log_24h"] = alert_entries
            errors["alert_count"] = len(alert_entries)
        except Exception as e:
            errors["alert_log_error"] = str(e)

        # Try DBA_OUTSTANDING_ALERTS
        try:
            outstanding = execute_query(
                conn,
                """
                SELECT severity, type, object_name, message, creation_time
                FROM dba_outstanding_alerts
                ORDER BY creation_time DESC
                FETCH FIRST 20 ROWS ONLY
                """,
            )
            errors["outstanding_alerts"] = outstanding
            errors["outstanding_count"] = len(outstanding)
        except Exception as e:
            errors["outstanding_alerts_error"] = str(e)

        # Check for ORA- errors in v$sql with high disk_reads (possible bad plans)
        try:
            bad_sql = execute_query(
                conn,
                """
                SELECT sql_id, executions, disk_reads, buffer_gets,
                       ROUND(disk_reads / NULLIF(buffer_gets, 0), 2) AS disk_ratio,
                       SUBSTR(sql_text, 1, 200) AS sql_preview
                FROM v$sql
                WHERE executions > 0
                  AND disk_reads > 10000
                  AND disk_reads / NULLIF(buffer_gets, 0) > 0.5
                ORDER BY disk_reads DESC
                FETCH FIRST 10 ROWS ONLY
                """,
            )
            errors["high_disk_read_sql"] = bad_sql
        except Exception as e:
            errors["bad_sql_error"] = str(e)

        status = STATUS_OK
        if errors.get("outstanding_count", 0) > 0:
            status = STATUS_WARN
        if errors.get("alert_count", 0) > 10:
            status = STATUS_WARN

        return {
            "status": status,
            "details": errors,
        }


def check_performance():
    """6. Performance: slow queries, blocking locks, wait events."""
    with get_connection() as conn:
        perf = {}

        # Blocking locks
        try:
            blocking = execute_query(
                conn,
                """
                SELECT
                    h.sid AS blocking_sid,
                    h.serial# AS blocking_serial,
                    h.username AS blocking_user,
                    w.sid AS waiting_sid,
                    w.event AS wait_event,
                    ROUND(w.seconds_in_wait) AS wait_seconds,
                    h.sql_id AS blocking_sql_id
                FROM v$session w
                JOIN v$session h ON w.blocking_session = h.sid
                WHERE w.blocking_session IS NOT NULL
                ORDER BY w.seconds_in_wait DESC
                FETCH FIRST 20 ROWS ONLY
                """,
            )
            perf["blocking_locks"] = blocking
            perf["blocking_count"] = len(blocking)
        except Exception as e:
            perf["blocking_locks_error"] = str(e)

        # Top wait events
        try:
            waits = execute_query(
                conn,
                """
                SELECT event, total_waits, time_waited_micro,
                       ROUND(time_waited_micro / 1e6, 2) AS time_waited_sec
                FROM v$system_event
                WHERE wait_class != 'Idle'
                ORDER BY time_waited_micro DESC
                FETCH FIRST 15 ROWS ONLY
                """,
            )
            perf["top_wait_events"] = waits
        except Exception as e:
            perf["waits_error"] = str(e)

        # Buffer cache hit ratio
        try:
            bch = execute_scalar(
                conn,
                """
                SELECT ROUND(
                    100 * (1 - (
                        (SELECT value FROM v$sysstat WHERE name = 'physical reads')
                        /
                        NULLIF((SELECT value FROM v$sysstat WHERE name = 'db block gets')
                               + (SELECT value FROM v$sysstat WHERE name = 'consistent gets'), 0)
                    )), 2)
                FROM dual
                """,
            )
            perf["buffer_cache_hit_ratio_pct"] = bch
        except Exception as e:
            perf["bch_error"] = str(e)

        # Library cache hit ratio
        try:
            lch = execute_scalar(
                conn,
                """
                SELECT ROUND(
                    100 * (1 - (
                        (SELECT value FROM v$sysstat WHERE name = 'reloads')
                        /
                        NULLIF((SELECT value FROM v$sysstat WHERE name = 'pins'), 0)
                    )), 2)
                FROM dual
                """,
            )
            perf["library_cache_hit_ratio_pct"] = lch
        except Exception as e:
            perf["lch_error"] = str(e)

        # Determine status
        status = STATUS_OK
        if perf.get("blocking_count", 0) > 0:
            status = STATUS_CRITICAL
        bch_val = perf.get("buffer_cache_hit_ratio_pct")
        if bch_val is not None and bch_val < 80:
            status = determine_worst_status(status, STATUS_WARN)

        return {
            "status": status,
            "details": perf,
        }


def check_vector_tables():
    """7. Embedding / vector table status — row counts per key table."""
    with get_connection("VECTOR_STORE") as conn:
        vector_info = {}

        # Embedding models
        try:
            models = execute_query(
                conn,
                "SELECT model_name, table_name, status, created_at "
                "FROM embedding_models ORDER BY model_name",
            )
            vector_info["embedding_models"] = models
        except Exception as e:
            vector_info["models_error"] = str(e)

        # Detect vector tables and count rows
        vector_tables = []
        try:
            tables = execute_query(
                conn,
                "SELECT table_name FROM user_tables WHERE table_name LIKE 'EMB_%' ORDER BY table_name",
            )
            for t in tables:
                tname = t["table_name"]
                try:
                    count = execute_scalar(conn, f"SELECT COUNT(*) FROM {tname}")
                    # Get column info
                    cols = execute_query(
                        conn,
                        "SELECT column_name, data_type FROM user_tab_columns "
                        "WHERE table_name = :1 ORDER BY column_id",
                        [tname],
                    )
                    vector_tables.append({
                        "table": tname,
                        "row_count": count,
                        "columns": cols,
                    })
                except Exception as e:
                    vector_tables.append({
                        "table": tname,
                        "row_count": None,
                        "error": str(e),
                    })
        except Exception as e:
            vector_info["vector_tables_error"] = str(e)

        vector_info["vector_tables"] = vector_tables

        # Check for any tables with VECTOR columns
        try:
            vector_cols = execute_query(
                conn,
                """
                SELECT table_name, column_name, data_type, data_length
                FROM user_tab_columns
                WHERE data_type LIKE '%VECTOR%'
                ORDER BY table_name, column_id
                """,
            )
            vector_info["vector_columns"] = vector_cols
        except Exception as e:
            vector_info["vector_columns_error"] = str(e)

        return {
            "status": STATUS_OK,
            "details": vector_info,
        }


# ---------------------------------------------------------------------------#
# Check 8: Resource Limits                                                    #
# ---------------------------------------------------------------------------#

def check_resource_limits():
    """8. Resource limits — sessions, processes, transactions utilization %."""
    with get_connection() as conn:
        limits = {}

        # v$resource_limit: utilization of key resources
        try:
            rl_rows = execute_query(
                conn,
                """
                SELECT resource_name,
                       current_utilization,
                       max_utilization,
                       limit_value,
                       ROUND(
                           current_utilization / NULLIF(
                               CASE WHEN limit_value = 'UNLIMITED' OR limit_value = '0'
                                    THEN max_utilization
                                    ELSE TO_NUMBER(limit_value)
                               END, 0
                           ) * 100, 1
                       ) AS pct_used
                FROM v$resource_limit
                WHERE resource_name IN (
                    'sessions', 'processes', 'transactions',
                    'db_files', 'enqueue_locks', 'undo_segments',
                    'max_rollback_segments'
                )
                ORDER BY
                    CASE resource_name
                        WHEN 'sessions'     THEN 1
                        WHEN 'processes'    THEN 2
                        WHEN 'transactions' THEN 3
                        ELSE 4
                    END
                """,
            )
            limits["resource_limits"] = rl_rows
        except Exception as e:
            limits["resource_limits_error"] = str(e)

        # Also get count of active sessions per service
        try:
            service_sessions = execute_query(
                conn,
                """
                SELECT service_name,
                       COUNT(*) AS session_count,
                       SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS active_count
                FROM v$session
                WHERE type = 'USER'
                  AND service_name IS NOT NULL
                GROUP BY service_name
                ORDER BY session_count DESC
                """,
            )
            limits["sessions_by_service"] = service_sessions
        except Exception as e:
            limits["sessions_by_service_error"] = str(e)

        # Determine status
        status = STATUS_OK
        for rl in limits.get("resource_limits", []):
            pct = rl.get("pct_used") or 0
            res_name = rl.get("resource_name", "")
            if pct >= 90:
                status = determine_worst_status(status, STATUS_CRITICAL)
            elif pct >= 75:
                status = determine_worst_status(status, STATUS_WARN)
            # Alert if max_utilization is close to limit for critical resources
            if res_name in ("sessions", "processes"):
                max_util = rl.get("max_utilization", 0) or 0
                cur_util = rl.get("current_utilization", 0) or 0
                if max_util > 0:
                    peak_pct = (cur_util / max_util) * 100
                    if peak_pct >= 85:
                        status = determine_worst_status(status, STATUS_WARN)

        return {
            "status": status,
            "details": limits,
        }


# ---------------------------------------------------------------------------#
# Check 9: Memory                                                             #
# ---------------------------------------------------------------------------#

def check_memory():
    """9. Memory — SGA/PGA breakdown, cache utilization, hit ratios."""
    with get_connection() as conn:
        mem = {}

        # SGA breakdown from v$sgastat
        try:
            sga_stats = execute_query(
                conn,
                """
                SELECT pool, name,
                       ROUND(bytes / 1048576, 2) AS size_mb
                FROM v$sgastat
                ORDER BY bytes DESC
                """,
            )
            # Summarize by pool
            sga_summary = {}
            total_sga = 0
            for row in sga_stats:
                pool = row.get("pool", "shared pool")
                size_mb = row.get("size_mb", 0)
                sga_summary[pool] = sga_summary.get(pool, 0) + size_mb
                total_sga += size_mb
            sga_summary["total_sga_mb"] = round(total_sga, 2)
            mem["sga_breakdown_mb"] = sga_summary
            mem["sga_details"] = sga_stats[:20]  # top 20 entries
        except Exception as e:
            mem["sga_error"] = str(e)

        # PGA stats from v$pgastat
        try:
            pga_stats = execute_query(
                conn,
                """
                SELECT name, value
                FROM v$pgastat
                ORDER BY value DESC
                """,
            )
            pga_summary = {}
            for row in pga_stats:
                pga_summary[row.get("name", "")] = row.get("value")
            mem["pga_stats"] = pga_summary
        except Exception as e:
            mem["pga_error"] = str(e)

        # Library cache hit ratio (pinned vs reloads)
        try:
            lib_hit = execute_scalar(
                conn,
                """
                SELECT ROUND(
                    100 * (
                        (SELECT gets FROM v$librarycache WHERE namespace = 'SQL AREA')
                        /
                        NULLIF(
                            (SELECT gets FROM v$librarycache WHERE namespace = 'SQL AREA')
                            +
                            (SELECT gethits FROM v$librarycache WHERE namespace = 'SQL AREA')
                        , 0)
                    ), 2)
                FROM dual
                """,
            )
            # Actually use the standard pins/reloads method
            lib_hit2 = execute_scalar(
                conn,
                """
                SELECT ROUND(
                    100 * (1 - (
                        (SELECT SUM(reloads) FROM v$librarycache WHERE namespace = 'SQL AREA')
                        /
                        NULLIF((SELECT SUM(pins) FROM v$librarycache WHERE namespace = 'SQL AREA'), 0)
                    )), 2)
                FROM dual
                """,
            )
            mem["library_cache_hit_ratio_pct"] = lib_hit2
        except Exception as e:
            mem["library_cache_hit_ratio_error"] = str(e)

        # Row cache (dictionary cache) hit ratio
        try:
            rowcache_hit = execute_scalar(
                conn,
                """
                SELECT ROUND(
                    100 * (1 - (
                        (SELECT SUM(reloads) FROM v$rowcache)
                        /
                        NULLIF((SELECT SUM(gets) FROM v$rowcache), 0)
                    )), 2)
                FROM dual
                """,
            )
            mem["rowcache_hit_ratio_pct"] = rowcache_hit
        except Exception as e:
            mem["rowcache_hit_error"] = str(e)

        # SGA free memory
        try:
            sga_free = execute_scalar(
                conn,
                "SELECT ROUND(SUM(bytes) / 1048576, 2) FROM v$sgastat WHERE name = 'free memory'",
            )
            mem["sga_free_memory_mb"] = sga_free
        except Exception as e:
            mem["sga_free_error"] = str(e)

        # Determine status
        status = STATUS_OK
        lc = mem.get("library_cache_hit_ratio_pct")
        if lc is not None and lc < 95:
            status = determine_worst_status(status, STATUS_WARN)
        rc = mem.get("rowcache_hit_ratio_pct")
        if rc is not None and rc < 95:
            status = determine_worst_status(status, STATUS_WARN)
        total_sga = mem.get("sga_breakdown_mb", {}).get("total_sga_mb", 0)
        free_sga = mem.get("sga_free_memory_mb", 0) or 0
        if total_sga > 0 and (free_sga / total_sga) < 0.05:
            status = determine_worst_status(status, STATUS_WARN)

        return {
            "status": status,
            "details": mem,
        }


# ---------------------------------------------------------------------------#
# Check 10: I/O Stats                                                         #
# ---------------------------------------------------------------------------#

def check_io_stats():
    """10. I/O stats — file-level read/write, hot files, temp usage."""
    with get_connection() as conn:
        io = {}

        # Data file I/O from v$filestat
        try:
            datafile_io = execute_query(
                conn,
                """
                SELECT f.file#,
                       f.name AS file_name,
                       fs.phyblkrd, fs.phyblkwrt,
                       fs.phyrds, fs.phywrts,
                       ROUND(fs.readtim / NULLIF(fs.phyblkrd, 0), 2) AS avg_read_ms,
                       ROUND(fs.writetim / NULLIF(fs.phyblkwrt, 0), 2) AS avg_write_ms
                FROM v$filestat fs
                JOIN v$datafile f ON fs.file# = f.file#
                ORDER BY (fs.phyrds + fs.phywrts) DESC
                """,
            )
            io["datafile_io"] = datafile_io
        except Exception as e:
            io["datafile_io_error"] = str(e)

        # Temp file I/O from v$tempstat
        try:
            temp_io = execute_query(
                conn,
                """
                SELECT f.file#,
                       f.name AS file_name,
                       fs.phyblkrd, fs.phyblkwrt,
                       fs.phyrds, fs.phywrts,
                       ROUND(fs.readtim / NULLIF(fs.phyblkrd, 0), 2) AS avg_read_ms,
                       ROUND(fs.writetim / NULLIF(fs.phyblkwrt, 0), 2) AS avg_write_ms
                FROM v$tempstat fs
                JOIN v$tempfile f ON fs.file# = f.file#
                ORDER BY (fs.phyrds + fs.phywrts) DESC
                """,
            )
            io["tempfile_io"] = temp_io
        except Exception as e:
            io["tempfile_io_error"] = str(e)

        # Tablespace I/O summary (reads per tablespace)
        try:
            ts_io = execute_query(
                conn,
                """
                SELECT tf.tablespace_name,
                       SUM(fs.phyrds) AS total_reads,
                       SUM(fs.phywrts) AS total_writes,
                       ROUND(SUM(fs.phyblkrd + fs.phyblkwrt) * 8192 / 1048576, 2) AS total_mb
                FROM v$filestat fs
                JOIN v$datafile df ON fs.file# = df.file#
                JOIN v$tablespace tf ON df.ts# = tf.ts#
                GROUP BY tf.tablespace_name
                ORDER BY total_mb DESC
                """,
            )
            io["tablespace_io"] = ts_io
        except Exception as e:
            io["tablespace_io_error"] = str(e)

        # System I/O stats from v$sysstat
        try:
            sys_io = execute_query(
                conn,
                "SELECT name, value FROM v$sysstat "
                "WHERE name LIKE 'physical %' "
                "ORDER BY name",
            )
            io["system_io_stats"] = sys_io
        except Exception as e:
            io["system_io_error"] = str(e)

        # Determine status
        status = STATUS_OK
        # Flag files with high avg read latency (>10ms is concerning)
        for df in io.get("datafile_io", []):
            avg_r = df.get("avg_read_ms") or 0
            if avg_r > 15:
                status = determine_worst_status(status, STATUS_WARN)
                break
            elif avg_r > 10:
                status = determine_worst_status(status, STATUS_WARN)
                break

        return {
            "status": status,
            "details": io,
        }


# ---------------------------------------------------------------------------#
# Check 11: Undo Health                                                       #
# ---------------------------------------------------------------------------#

def check_undo_health():
    """11. Undo health — undo retention, longest query needing undo."""
    with get_connection() as conn:
        undo = {}

        # Undo tablespace size and usage
        try:
            undo_ts = execute_query(
                conn,
                """
                SELECT tablespace_name,
                       ROUND(SUM(bytes) / 1048576, 2) AS size_mb,
                       ROUND(SUM(maxbytes) / 1048576, 2) AS max_mb
                FROM dba_data_files
                WHERE tablespace_name LIKE 'UNDO%'
                GROUP BY tablespace_name
                """,
            )
            undo["undo_tablespace"] = undo_ts
        except Exception as e:
            undo["undo_tablespace_error"] = str(e)

        # v$undostat — undo stats over last 24 entries (typical)
        try:
            undostat = execute_query(
                conn,
                """
                SELECT
                    ROUND(
                        (MAX(tuned_undoretention)) / 86400, 2
                    ) AS tuned_undo_retention_days,
                    ROUND(
                        AVG(tuned_undoretention), 0
                    ) AS avg_tuned_retention_sec,
                    MAX(maxquerylen) AS max_query_len_sec,
                    SUM(undoblks) AS total_undo_blocks,
                    SUM(txncount) AS total_txn_count
                FROM v$undostat
                WHERE begin_time > SYSDATE - 1
                """,
            )
            undo["undostat_summary"] = undostat
        except Exception as e:
            undo["undostat_error"] = str(e)

        # Per-snapshot detail from v$undostat (last 24 snapshots)
        try:
            undostat_detail = execute_query(
                conn,
                """
                SELECT begin_time, end_time,
                       ROUND(tuned_undoretention, 0) AS tuned_retention_sec,
                       ROUND(maxquerylen, 0) AS maxquery_sec,
                       ssolderrcnt AS ora_1555_count,
                       undoblks AS undo_blocks,
                       txncount AS txn_count
                FROM v$undostat
                ORDER BY begin_time DESC
                FETCH FIRST 24 ROWS ONLY
                """,
            )
            undo["undostat_detail"] = undostat_detail
        except Exception as e:
            undo["undostat_detail_error"] = str(e)

        # Active undo usage by session
        try:
            active_undo = execute_query(
                conn,
                """
                SELECT s.sid, s.serial#, s.username,
                       ROUND(t.used_ublk * 8192 / 1048576, 2) AS undo_mb
                FROM v$transaction t
                JOIN v$session s ON t.ses_addr = s.saddr
                ORDER BY t.used_ublk DESC
                FETCH FIRST 10 ROWS ONLY
                """,
            )
            undo["active_undo_by_session"] = active_undo
        except Exception as e:
            undo["active_undo_error"] = str(e)

        # Determine status
        status = STATUS_OK
        # Check for ORA-1555 errors in undostat
        for row in undo.get("undostat_detail", []):
            ora_1555 = row.get("ora_1555_count") or 0
            if ora_1555 > 0:
                status = determine_worst_status(status, STATUS_CRITICAL)
                break

        return {
            "status": status,
            "details": undo,
        }


# ---------------------------------------------------------------------------#
# Check 12: Session Insights                                                  #
# ---------------------------------------------------------------------------#

def check_session_insights():
    """12. Session insights — active by service, blocking, ASH top waits."""
    with get_connection() as conn:
        sessions = {}

        # Active sessions by service and status
        try:
            by_service = execute_query(
                conn,
                """
                SELECT service_name, status, COUNT(*) AS cnt
                FROM v$session
                WHERE type = 'USER'
                GROUP BY service_name, status
                ORDER BY cnt DESC
                """,
            )
            sessions["by_service_status"] = by_service
        except Exception as e:
            sessions["by_service_error"] = str(e)

        # Total session counts
        try:
            session_counts = execute_scalar(
                conn,
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS active_count,
                    SUM(CASE WHEN type = 'USER' THEN 1 ELSE 0 END) AS user_count,
                    SUM(CASE WHEN blocking_session IS NOT NULL THEN 1 ELSE 0 END) AS blocked_count
                FROM v$session
                WHERE type != 'BACKGROUND'
                """,
            )
            sessions["counts"] = dict(
                zip(
                    ["total", "active", "user", "blocked"],
                    session_counts if session_counts else (0, 0, 0, 0),
                )
            )
        except Exception as e:
            sessions["counts_error"] = str(e)

        # Blocking/waiting session details (blocking chains)
        try:
            blocking_chains = execute_query(
                conn,
                """
                SELECT
                    s.sid, s.serial#, s.username, s.program,
                    s.sql_id, s.event,
                    s.blocking_session,
                    w.event AS wait_event,
                    ROUND(w.seconds_in_wait) AS seconds_in_wait
                FROM v$session w
                LEFT JOIN v$session s ON w.blocking_session = s.sid
                WHERE w.blocking_session IS NOT NULL
                  AND w.blocking_session_status = 'VALID'
                ORDER BY w.seconds_in_wait DESC
                FETCH FIRST 20 ROWS ONLY
                """,
            )
            sessions["blocking_chains"] = blocking_chains
        except Exception as e:
            sessions["blocking_error"] = str(e)

        # ASH — top wait events in last 5 minutes
        try:
            ash_waits = execute_query(
                conn,
                """
                SELECT event, wait_class,
                       COUNT(*) AS sample_count,
                       ROUND(COUNT(*) / 60, 2) AS approx_minutes
                FROM v$active_session_history
                WHERE session_state = 'WAITING'
                  AND sample_time > SYSDATE - INTERVAL '5' MINUTE
                GROUP BY event, wait_class
                ORDER BY sample_count DESC
                FETCH FIRST 15 ROWS ONLY
                """,
            )
            sessions["ash_top_waits_5min"] = ash_waits
        except Exception as e:
            sessions["ash_error"] = str(e)

        # ASH — top SQL IDs by DB time
        try:
            ash_sql = execute_query(
                conn,
                """
                SELECT sql_id, COUNT(*) AS sample_count
                FROM v$active_session_history
                WHERE sql_id IS NOT NULL
                  AND sample_time > SYSDATE - INTERVAL '5' MINUTE
                GROUP BY sql_id
                ORDER BY sample_count DESC
                FETCH FIRST 10 ROWS ONLY
                """,
            )
            sessions["ash_top_sql_5min"] = ash_sql
        except Exception as e:
            sessions["ash_sql_error"] = str(e)

        # Process count from v$process
        try:
            process_info = execute_query(
                conn,
                """
                SELECT program, COUNT(*) AS cnt
                FROM v$process
                GROUP BY program
                ORDER BY cnt DESC
                FETCH FIRST 10 ROWS ONLY
                """,
            )
            sessions["processes_by_program"] = process_info
        except Exception as e:
            sessions["process_error"] = str(e)

        # Determine status
        status = STATUS_OK
        blocked = sessions.get("counts", {}).get("blocked", 0)
        if blocked > 0:
            status = determine_worst_status(status, STATUS_CRITICAL)

        return {
            "status": status,
            "details": sessions,
        }


# ---------------------------------------------------------------------------#
# Check 13: Configuration Drift                                               #
# ---------------------------------------------------------------------------#

def check_config_drift():
    """13. Configuration drift — critical non-default parameters, NLS."""
    with get_connection() as conn:
        config = {}

        # Critical v$parameter values (non-default checks)
        try:
            params = execute_query(
                conn,
                """
                SELECT name, value, isdefault, description
                FROM v$parameter
                WHERE isdefault = 'FALSE'
                  AND name IN (
                    'processes', 'sessions', 'transactions',
                    'open_cursors', 'cursor_sharing',
                    'undo_management', 'undo_tablespace',
                    'undo_retention', 'db_block_size',
                    'memory_target', 'memory_max_target',
                    'sga_target', 'sga_max_target',
                    'pga_aggregate_target', 'pga_aggregate_limit',
                    'parallel_max_servers', 'parallel_degree_policy',
                    'optimizer_mode', 'optimizer_features_enable',
                    'remote_login_passwordfile', 'sec_case_sensitive_logon',
                    'resource_manager_plan',
                    'listener_networks', 'db_domain', 'service_names',
                    'audit_trail', 'compatible', 'nls_language',
                    'nls_territory', 'nls_characterset'
                  )
                ORDER BY name
                """,
            )
            config["non_default_params"] = params
            config["non_default_count"] = len(params)
        except Exception as e:
            config["params_error"] = str(e)

        # NLS session and instance settings
        try:
            nls = execute_query(
                conn,
                "SELECT * FROM v$nls_parameters ORDER BY parameter",
            )
            config["nls_parameters"] = nls
        except Exception as e:
            config["nls_error"] = str(e)

        # Timezone file version
        try:
            tz_info = execute_query(
                conn,
                "SELECT version, filename FROM v$timezone_file",
            )
            config["timezone_file"] = tz_info
        except Exception as e:
            config["timezone_error"] = str(e)

        # PDB info (important for ADB which uses PDBs)
        try:
            pdb_info = execute_query(
                conn,
                "SELECT con_id, name, open_mode, restricted FROM v$pdbs",
            )
            config["pdbs"] = pdb_info
        except Exception as e:
            config["pdb_error"] = str(e)

        # Service names
        try:
            services = execute_query(
                conn,
                "SELECT name, enabled, goal, commit_outcome FROM v$services",
            )
            config["services"] = services
        except Exception as e:
            config["services_error"] = str(e)

        # Feature usage (important for ADB licensing/cost)
        try:
            features = execute_query(
                conn,
                """
                SELECT name, detected_usages, currently_used,
                       first_usage_date, last_usage_date
                FROM dba_feature_usage_statistics
                WHERE detected_usages > 0
                ORDER BY detected_usages DESC
                FETCH FIRST 20 ROWS ONLY
                """,
            )
            config["feature_usage"] = features
        except Exception as e:
            config["features_error"] = str(e)

        # OS stats (CPU count, load)
        try:
            osstat = execute_query(
                conn,
                "SELECT stat_name, value FROM v$osstat WHERE stat_name IN "
                "('NUM_CPUS', 'NUM_CPU_CORES', 'NUM_CPU_SOCKETS', 'LOAD', 'NUM_VCPUS')",
            )
            config["os_stats"] = osstat
        except Exception as e:
            config["osstat_error"] = str(e)

        # Determine status — configuration is INFO unless something looks wrong
        status = STATUS_OK
        # Warn if undo_retention is very low (<300s)
        for p in config.get("non_default_params", []):
            if p.get("name") == "undo_retention":
                val = int(p.get("value", 0) or 0)
                if val < 300:
                    status = determine_worst_status(status, STATUS_WARN)

        return {
            "status": status,
            "details": config,
        }


# ---------------------------------------------------------------------------#
# Check 14: Corruption Check                                                  #
# ---------------------------------------------------------------------------#

def check_corruption():
    """14. Corruption check — v$database_block_corruption count."""
    with get_connection() as conn:
        corrupt = {}

        # Block corruption from v$database_block_corruption
        try:
            corruption_rows = execute_query(
                conn,
                """
                SELECT corruption_type, block#,
                       file#, tablespace_name, segment_name,
                       segment_type
                FROM v$database_block_corruption
                LEFT JOIN dba_extents ON (file# = file_id AND block# = block_id)
                ORDER BY file#, block#
                """,
            )
            corrupt["corrupt_blocks"] = corruption_rows
            corrupt["corrupt_block_count"] = len(corruption_rows)
        except Exception as e:
            corrupt["corruption_error"] = str(e)

        # Also check for failed RMAN backups / recovery area issues
        try:
            recovery_usage = execute_query(
                conn,
                """
                SELECT ROUND(space_limit / 1073741824, 2) AS space_limit_gb,
                       ROUND(space_used / 1073741824, 2) AS space_used_gb,
                       ROUND(space_reclaimable / 1073741824, 2) AS reclaimable_gb,
                       number_of_files
                FROM v$recovery_area_usage
                """,
            )
            corrupt["recovery_area"] = recovery_usage
        except Exception as e:
            corrupt["recovery_error"] = str(e)

        # Redo log groups with issues
        try:
            redo_issues = execute_query(
                conn,
                """
                SELECT group#, thread#, status, bytes / 1048576 AS size_mb
                FROM v$log
                WHERE status IN ('INVALID', 'CORRUPT')
                """,
            )
            corrupt["problem_redo_logs"] = redo_issues
        except Exception as e:
            corrupt["redo_error"] = str(e)

        # Determine status
        status = STATUS_OK
        if corrupt.get("corrupt_block_count", 0) > 0:
            status = STATUS_CRITICAL
        if corrupt.get("problem_redo_logs"):
            status = determine_worst_status(status, STATUS_CRITICAL)

        return {
            "status": status,
            "details": corrupt,
        }


# ---------------------------------------------------------------------------#
# Check 15: Audit Trail                                                       #
# ---------------------------------------------------------------------------#

def check_audit_trail():
    """15. Audit trail — failed login attempts and suspicious activity."""
    with get_connection() as conn:
        audit = {}

        # Failed login attempts in last 24 hours
        try:
            failed_logins = execute_query(
                conn,
                """
                SELECT username, userhost, os_username,
                       terminal, action_name,
                       returncode, timestamp
                FROM dba_audit_trail
                WHERE action_name = 'LOGON'
                  AND returncode != 0
                  AND timestamp > SYSDATE - 1
                ORDER BY timestamp DESC
                FETCH FIRST 50 ROWS ONLY
                """,
            )
            audit["failed_logins_24h"] = failed_logins
            audit["failed_login_count"] = len(failed_logins)
        except Exception as e:
            audit["failed_logins_error"] = str(e)

        # Unique usernames with failed logins
        try:
            failed_users = execute_query(
                conn,
                """
                SELECT username, COUNT(*) AS fail_count,
                       MIN(timestamp) AS first_attempt,
                       MAX(timestamp) AS last_attempt
                FROM dba_audit_trail
                WHERE action_name = 'LOGON'
                  AND returncode != 0
                  AND timestamp > SYSDATE - 1
                GROUP BY username
                ORDER BY fail_count DESC
                """,
            )
            audit["failed_login_by_user"] = failed_users
        except Exception as e:
            audit["failed_users_error"] = str(e)

        # Successful logins by unique users in last 24h
        try:
            successful_logins = execute_query(
                conn,
                """
                SELECT username, COUNT(*) AS login_count,
                       MAX(timestamp) AS last_login
                FROM dba_audit_trail
                WHERE action_name = 'LOGON'
                  AND returncode = 0
                  AND timestamp > SYSDATE - 1
                GROUP BY username
                ORDER BY login_count DESC
                """,
            )
            audit["successful_logins_24h"] = successful_logins
        except Exception as e:
            audit["successful_logins_error"] = str(e)

        # Recent DDL operations in last 24h
        try:
            recent_ddl = execute_query(
                conn,
                """
                SELECT username, action_name, object_name,
                       object_schema, timestamp
                FROM dba_audit_trail
                WHERE action_name IN (
                    'CREATE TABLE', 'DROP TABLE', 'ALTER TABLE',
                    'CREATE INDEX', 'DROP INDEX',
                    'CREATE USER', 'DROP USER', 'ALTER USER',
                    'CREATE ROLE', 'DROP ROLE', 'GRANT', 'REVOKE'
                )
                AND timestamp > SYSDATE - 1
                ORDER BY timestamp DESC
                FETCH FIRST 30 ROWS ONLY
                """,
            )
            audit["recent_ddl_24h"] = recent_ddl
        except Exception as e:
            audit["ddl_error"] = str(e)

        # Determine status
        status = STATUS_OK
        fail_count = audit.get("failed_login_count", 0)
        if fail_count > 20:
            status = STATUS_CRITICAL
        elif fail_count > 5:
            status = determine_worst_status(status, STATUS_WARN)

        # Check for DDL on key schemas
        for ddl in audit.get("recent_ddl_24h", []):
            schema = (ddl.get("object_schema") or "").upper()
            if schema in ("HERMES_MEMORY", "KNOWLEDGE_BASE", "VECTOR_STORE"):
                action = ddl.get("action_name", "")
                if action.startswith("DROP") or action.startswith("ALTER"):
                    status = determine_worst_status(status, STATUS_WARN)

        return {
            "status": status,
            "details": audit,
        }


# ---------------------------------------------------------------------------#
# Check 16: Archive Log                                                       #
# ---------------------------------------------------------------------------#

def check_archive_log():
    """16. Archive log — generation rate (GB/day), recent switches."""
    with get_connection() as conn:
        arch = {}

        # Archive log generation in last 24 hours
        try:
            arch_24h = execute_query(
                conn,
                """
                SELECT
                    ROUND(SUM(blocks * block_size) / 1073741824, 3) AS generated_gb,
                    COUNT(*) AS log_count,
                    MIN(first_time) AS earliest,
                    MAX(next_time) AS latest
                FROM v$archived_log
                WHERE first_time > SYSDATE - 1
                  AND dest_id = 1
                """,
            )
            arch["archive_24h"] = arch_24h
        except Exception as e:
            arch["archive_error"] = str(e)

        # Archive log history by day (last 7 days)
        try:
            arch_daily = execute_query(
                conn,
                """
                SELECT TRUNC(first_time) AS day,
                       ROUND(SUM(blocks * block_size) / 1073741824, 3) AS gb_per_day,
                       COUNT(*) AS log_count
                FROM v$archived_log
                WHERE first_time > SYSDATE - 7
                  AND dest_id = 1
                GROUP BY TRUNC(first_time)
                ORDER BY day DESC
                """,
            )
            arch["archive_daily_7d"] = arch_daily
        except Exception as e:
            arch["archive_daily_error"] = str(e)

        # Current redo log status
        try:
            redo_status = execute_query(
                conn,
                """
                SELECT group#, thread#, sequence#,
                       bytes / 1048576 AS size_mb,
                       status, archived
                FROM v$log
                ORDER BY group#
                """,
            )
            arch["redo_log_status"] = redo_status
        except Exception as e:
            arch["redo_error"] = str(e)

        # Archive destinations
        try:
            arch_dest = execute_query(
                conn,
                """
                SELECT dest_id, destination, status,
                       target, schedule, binding
                FROM v$archive_dest
                WHERE status != 'INACTIVE'
                """,
            )
            arch["archive_destinations"] = arch_dest
        except Exception as e:
            arch["arch_dest_error"] = str(e)

        # Determine status
        status = STATUS_OK
        # Check if all redo logs are archived and not stuck
        for redo in arch.get("redo_log_status", []):
            if redo.get("status") in ("INVALID", "CORRUPT", "STALE"):
                status = determine_worst_status(status, STATUS_CRITICAL)

        return {
            "status": status,
            "details": arch,
        }


# ---------------------------------------------------------------------------#
# Check 17: Transaction Health                                                #
# ---------------------------------------------------------------------------#

def check_transaction_health():
    """17. Transaction health — long-running transactions, space usage."""
    with get_connection() as conn:
        txn = {}

        # Active transactions from v$transaction
        try:
            txns = execute_query(
                conn,
                """
                SELECT
                    s.sid, s.serial#, s.username, s.program,
                    t.start_time,
                    t.used_ublk AS undo_blocks,
                    t.used_urec AS undo_records,
                    ROUND(t.used_ublk * 8192 / 1048576, 2) AS undo_mb,
                    ROUND(
                        (SYSDATE - TO_DATE(t.start_time, 'MM/DD/RR HH24:MI:SS')) * 86400
                    ) AS duration_sec,
                    s.sql_id,
                    t.status AS txn_status
                FROM v$transaction t
                JOIN v$session s ON t.ses_addr = s.saddr
                ORDER BY t.used_ublk DESC
                FETCH FIRST 20 ROWS ONLY
                """,
            )
            txn["active_transactions"] = txns
            txn["transaction_count"] = len(txns)
        except Exception as e:
            txn["transactions_error"] = str(e)

        # Transaction summary by status
        try:
            txn_summary = execute_query(
                conn,
                """
                SELECT status, COUNT(*) AS cnt,
                       ROUND(SUM(used_ublk) * 8192 / 1048576, 2) AS total_undo_mb
                FROM v$transaction
                GROUP BY status
                """,
            )
            txn["status_summary"] = txn_summary
        except Exception as e:
            txn["summary_error"] = str(e)

        # Distributed transactions (two-phase commit)
        try:
            dist_txns = execute_query(
                conn,
                """
                SELECT local_tran_id, global_tran_id, state, host,
                       commit# AS commit_scn
                FROM dba_2pc_pending
                """,
            )
            txn["pending_distributed"] = dist_txns
        except Exception as e:
            txn["distributed_error"] = str(e)

        # In-doubt transactions
        try:
            indoubt_txns = execute_query(
                conn,
                """
                SELECT * FROM dba_2pc_neighbors
                FETCH FIRST 20 ROWS ONLY
                """,
            )
            txn["in_doubt_neighbors"] = indoubt_txns
        except Exception as e:
            txn["indoubt_error"] = str(e)

        # Determine status
        status = STATUS_OK
        # Flag long-running transactions (>30 minutes with significant undo)
        for t in txn.get("active_transactions", []):
            dur = t.get("duration_sec") or 0
            undo_mb = t.get("undo_mb") or 0
            if dur > 1800 and undo_mb > 10:
                status = determine_worst_status(status, STATUS_WARN)
                break
            elif dur > 3600:
                status = determine_worst_status(status, STATUS_WARN)
                break

        # Pending distributed transactions are always a concern
        if txn.get("pending_distributed"):
            status = determine_worst_status(status, STATUS_WARN)

        return {
            "status": status,
            "details": txn,
        }


# ---------------------------------------------------------------------------#
# Report Generation                                                           #
# ---------------------------------------------------------------------------#

def print_report():
    """Print a human-readable console report."""
    print()
    print("=" * 72)
    print("  ORACLE ADB 26ai HEALTH REPORT")
    print("=" * 72)
    print(f"  Timestamp : {report['timestamp']}")
    print(f"  Host      : {report['host']}")
    print(f"  Port      : {report['port']}")
    print(f"  Service   : {report['service_name']}")
    print(f"  Status    : {_status_emoji(report['overall_status'])} {report['overall_status']}")
    print("=" * 72)

    for check_name, check_data in report["checks"].items():
        status = check_data.get("status", STATUS_INFO)
        duration = check_data.get("duration_ms", 0)
        label = check_data.get("label", check_name)
        print()
        print(f"  {_status_emoji(status)} {label}  [{status}]  ({duration}ms)")
        print(f"  {'-' * 60}")

        if check_data.get("error"):
            print(f"    ❌ Error: {check_data['error']}")

        details = check_data.get("details", {})
        _print_details(details, indent=4)

    print()
    print("=" * 72)
    if report["errors"]:
        print(f"  ⚠️  {len(report['errors'])} error(s) encountered during checks:")
        for err in report["errors"]:
            print(f"     • {err}")
    else:
        print("  ✅ No errors encountered.")
    print("=" * 72)
    print()
    print(f"  📄 JSON report saved to: {REPORT_FILE}")
    print()


def _status_emoji(status):
    return {
        STATUS_OK: "✅",
        STATUS_WARN: "⚠️ ",
        STATUS_CRITICAL: "🔴",
        STATUS_SKIP: "⏭️ ",
        STATUS_INFO: "ℹ️ ",
    }.get(status, "❓")


def _print_details(details, indent=4):
    """Recursively print detail dicts/lists in a readable format."""
    prefix = " " * indent
    if isinstance(details, dict):
        for k, v in details.items():
            if isinstance(v, (dict, list)):
                print(f"{prefix}{k}:")
                _print_details(v, indent + 2)
            elif v is None:
                print(f"{prefix}{k}: (null)")
            else:
                print(f"{prefix}{k}: {v}")
    elif isinstance(details, list):
        if not details:
            print(f"{prefix}(empty)")
        for i, item in enumerate(details):
            if isinstance(item, dict):
                # Print first few items inline
                parts = [f"{k}={v}" for k, v in item.items()]
                print(f"{prefix}[{i}] {', '.join(parts[:5])}")
            else:
                print(f"{prefix}[{i}] {item}")


def save_json_report():
    """Save the full report as JSON."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2, default=str)
    return REPORT_FILE


# ---------------------------------------------------------------------------#
# Main                                                                        #
# ---------------------------------------------------------------------------#

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Oracle ADB 26ai Health Check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress console output; only save JSON report.",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Custom output path for JSON report (default: ~/hermes/reports/health_<timestamp>.json)",
    )
    args = parser.parse_args()

    global REPORT_FILE
    if args.output:
        REPORT_FILE = Path(args.output)

    if not args.quiet:
        print("🔍 Oracle ADB Health Check starting...")
        print(f"   Config: {CONFIG_PATH}")
        print(f"   Report: {REPORT_FILE}")

    # Pre-flight: check oracledb is importable
    try:
        import oracledb
    except ImportError:
        print("❌ 'oracledb' Python package is not installed.")
        print("   Install with: pip install oracledb")
        sys.exit(1)

    # Pre-flight: check password is set
    if not os.environ.get("ORACLE_ADMIN_PASSWORD"):
        print("❌ ORACLE_ADMIN_PASSWORD environment variable is not set.")
        sys.exit(1)

    # Run all checks
    checks = [
        ("connectivity", "1. Connectivity", check_connectivity),
        ("schema_status", "2. Schema Status", check_schema_tables),
        ("long_queries", "3. Long-Running Queries", check_long_running_queries),
        ("storage", "4. Storage / Quota", check_storage_quota),
        ("errors", "5. Recent Errors", check_recent_errors),
        ("performance", "6. Performance", check_performance),
        ("vector_tables", "7. Vector / Embedding Tables", check_vector_tables),
        ("resource_limits", "8. Resource Limits", check_resource_limits),
        ("memory", "9. Memory", check_memory),
        ("io_stats", "10. I/O Stats", check_io_stats),
        ("undo_health", "11. Undo Health", check_undo_health),
        ("session_insights", "12. Session Insights", check_session_insights),
        ("config_drift", "13. Configuration Drift", check_config_drift),
        ("corruption", "14. Corruption Check", check_corruption),
        ("audit_trail", "15. Audit Trail", check_audit_trail),
        ("archive_log", "16. Archive Log", check_archive_log),
        ("transaction_health", "17. Transaction Health", check_transaction_health),
    ]

    for check_key, check_label, check_func in checks:
        if not args.quiet:
            print(f"   Running: {check_label}...", end=" ", flush=True)
        result = safe_check(check_label, check_func)
        report["checks"][check_key] = result
        report["overall_status"] = determine_worst_status(
            report["overall_status"], result["status"]
        )
        if not args.quiet:
            print(f"{_status_emoji(result['status'])} {result['status']} ({result['duration_ms']}ms)")

    # Save JSON report
    save_json_report()

    # Print console report
    if not args.quiet:
        print_report()
    else:
        print(f"Report saved to: {REPORT_FILE}")

    # Exit with appropriate code
    if report["overall_status"] == STATUS_CRITICAL:
        sys.exit(2)
    elif report["overall_status"] == STATUS_WARN:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
