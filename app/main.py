import asyncio
import logging
import os
import sqlite3
import time
import requests
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

from app.router import (
    route,
    SOURCE_MAP,
    detect_intent,
    check_cached,
    get_cache_stats,
    get_cache_count,
    clear_cache,
    load_cache,
    load_routing_cache,
    get_routing_cache_stats,
    clear_routing_cache,
)
from app.mcp_server import mcp_app
from app.sources.kiwix import get_books, refresh_catalog
from app.snapshots import (
    init_snapshot_db,
    snapshot_uptime,
    snapshot_forecast,
    snapshot_news,
    snapshot_ha,
    get_changes,
    format_changes,
)
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Query logging — SQLite
# ---------------------------------------------------------------------------

_LOG_DB = "/app/data/query_log.db"


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and busy timeout to reduce lock contention."""
    con = sqlite3.connect(db_path, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    return con


def _init_log_db():
    """Create query log table if it doesn't exist."""
    try:
        con = _connect(_LOG_DB)
        con.execute("""
            CREATE TABLE IF NOT EXISTS query_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                query TEXT NOT NULL,
                source_requested TEXT NOT NULL,
                source_used TEXT NOT NULL,
                cached INTEGER NOT NULL,
                success INTEGER NOT NULL,
                latency_ms INTEGER NOT NULL
            )
        """)
        con.commit()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not initialize query log db: %s", e)


def _log_query(query: str, source_requested: str, source_used: str, cached: bool, success: bool, latency_ms: int):
    """Write a query log entry."""
    try:
        con = _connect(_LOG_DB)
        con.execute(
            "INSERT INTO query_log (timestamp, query, source_requested, source_used, cached, success, latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), query, source_requested, source_used, int(cached), int(success), latency_ms)
        )
        con.commit()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not write query log: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load Kiwix catalog, cache, and start snapshot scheduler on startup."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, get_books)
    await loop.run_in_executor(None, load_cache)
    await loop.run_in_executor(None, load_routing_cache)
    await loop.run_in_executor(None, _init_log_db)
    await loop.run_in_executor(None, init_snapshot_db)

    # Start snapshot scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(snapshot_uptime, "interval", minutes=2, id="snapshot_uptime")
    scheduler.add_job(snapshot_forecast, "interval", minutes=30, id="snapshot_forecast")
    scheduler.add_job(snapshot_news, "interval", minutes=60, id="snapshot_news")
    scheduler.add_job(snapshot_ha, "interval", minutes=5, id="snapshot_ha")
    scheduler.start()
    _LOGGER.info("Snapshot scheduler started")

    # Take immediate snapshots on startup so /changes has data right away
    await loop.run_in_executor(None, snapshot_uptime)
    await loop.run_in_executor(None, snapshot_forecast)
    await loop.run_in_executor(None, snapshot_news)
    await loop.run_in_executor(None, snapshot_ha)

    yield

    scheduler.shutdown()
    _LOGGER.info("Snapshot scheduler stopped")


app = FastAPI(
    title="Mnemolis",
    description="Unified local knowledge search API with multi-source fusion. Routes queries to Kiwix, Open-Meteo, FreshRSS, SearXNG, Uptime Kuma, or multiple sources concurrently.",
    version="3.6.1",
    lifespan=lifespan,
)

app.mount("/mcp", mcp_app)


class SearchRequest(BaseModel):
    query: str
    source: str = "auto"
    fusion_sources: list[str] | None = None  # only used when source="fusion"


class SearchResponse(BaseModel):
    query: str
    source_used: str
    result: str
    success: bool
    cached: bool = False
    error: Optional[str] = None


def _check_kiwix() -> dict:
    books = get_books()
    if not settings.kiwix_url:
        return {"status": "not_configured"}
    try:
        resp = requests.get(f"{settings.kiwix_url}/catalog/v2/entries?count=1", timeout=3)
        resp.raise_for_status()
        return {"status": "ok", "books": len(books)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_forecast() -> dict:
    if not settings.forecast_latitude or not settings.forecast_longitude:
        return {"status": "not_configured"}
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": settings.forecast_latitude, "longitude": settings.forecast_longitude, "current": "temperature_2m"},
            timeout=5,
        )
        resp.raise_for_status()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_news() -> dict:
    if not settings.freshrss_url or not settings.freshrss_user:
        return {"status": "not_configured"}
    try:
        resp = requests.get(f"{settings.freshrss_url}/api/greader.php/accounts/ClientLogin", timeout=3)
        # 401 means reachable but needs auth — that's fine, service is up
        if resp.status_code in (200, 401, 400):
            return {"status": "ok"}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_web() -> dict:
    if not settings.searxng_url:
        return {"status": "not_configured"}
    try:
        resp = requests.get(f"{settings.searxng_url}/healthz", timeout=3)
        if resp.status_code in (200, 404):  # 404 means SearXNG is up but no /healthz route
            return {"status": "ok"}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_uptime() -> dict:
    if not settings.uptime_kuma_url or not settings.uptime_kuma_username:
        return {"status": "not_configured"}
    try:
        resp = requests.get(settings.uptime_kuma_url, timeout=3)
        if resp.status_code in (200, 301, 302):
            return {"status": "ok"}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_ha() -> dict:
    if not settings.ha_url or not settings.ha_token:
        return {"status": "not_configured"}
    try:
        resp = requests.get(
            f"{settings.ha_url}/api/",
            headers={"Authorization": f"Bearer {settings.ha_token}"},
            timeout=3,
        )
        if resp.status_code == 200:
            return {"status": "ok"}
        if resp.status_code == 401:
            return {"status": "error", "error": "Invalid token"}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _check_llm() -> dict:
    if not settings.llm_url or not settings.llm_model:
        return {"status": "not_configured"}
    try:
        if settings.llm_api_type == "openai":
            resp = requests.get(f"{settings.llm_url}/v1/models", timeout=3)
        else:
            resp = requests.get(f"{settings.llm_url}/api/tags", timeout=3)
        if resp.status_code == 200:
            return {"status": "ok", "model": settings.llm_model, "api_type": settings.llm_api_type}
        return {"status": "error", "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/health")
def health():
    """
    Health check. Returns container status, Kiwix books loaded, cache entry count,
    and connectivity status for every configured source.
    """
    books = get_books()
    sources = {
        "kiwix": _check_kiwix(),
        "forecast": _check_forecast(),
        "news": _check_news(),
        "web": _check_web(),
        "uptime": _check_uptime(),
        "ha": _check_ha(),
        "llm": _check_llm(),
    }
    # Overall status — ok if container is running regardless of source health
    return {
        "status": "ok",
        "kiwix_books_loaded": len(books),
        "cache_entries": get_cache_count(),
        "sources": sources,
    }


@app.get("/sources")
def list_sources():
    """List all available search sources including 'auto'."""
    return {"sources": list(SOURCE_MAP.keys()) + ["auto"]}


@app.get("/catalog")
def catalog():
    """List all books currently loaded from the Kiwix OPDS catalog."""
    books = get_books()
    return {"count": len(books), "books": books}


@app.post("/catalog/refresh")
def catalog_refresh():
    """
    Force a re-scan of the Kiwix OPDS catalog without restarting the container.
    Use this after adding new ZIM files to Kiwix.
    """
    books = refresh_catalog()
    return {"status": "refreshed", "count": len(books)}


@app.get("/cache")
def cache_stats():
    """
    Show all current result cache entries with age and remaining TTL.
    Result cache stores actual search results keyed by source and query.
    """
    entries = get_cache_stats()
    return {"count": len(entries), "entries": entries}


@app.post("/cache/clear")
def cache_clear():
    """Clear all result cache entries from memory and disk."""
    count = clear_cache()
    return {"status": "cleared", "entries_removed": count}


@app.get("/cache/routing")
def routing_cache_stats():
    """
    Show all current routing cache entries with age and remaining TTL.
    Routing cache stores source and Kiwix book selection decisions to avoid
    redundant Ollama calls for repeated queries.
    """
    entries = get_routing_cache_stats()
    return {"count": len(entries), "entries": entries}


@app.post("/cache/routing/clear")
def routing_cache_clear():
    """Clear all routing cache entries from memory and disk."""
    count = clear_routing_cache()
    return {"status": "cleared", "entries_removed": count}


@app.post("/search", response_model=SearchResponse)
def search(request: SearchRequest):
    if request.source == "auto":
        intent = detect_intent(request.query)
        if isinstance(intent, list):
            resolved_source = "fusion"
            was_cached = check_cached("fusion", f"fusion[{','.join(sorted(intent))}]:{request.query}")
        else:
            resolved_source = intent
            was_cached = check_cached(resolved_source, request.query)
    else:
        resolved_source = request.source
        was_cached = check_cached(resolved_source, request.query)

    start = time.monotonic()
    try:
        result = route(request.query, request.source, request.fusion_sources)
        latency_ms = int((time.monotonic() - start) * 1000)
        _log_query(request.query, request.source, resolved_source, was_cached, True, latency_ms)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result=result,
            success=True,
            cached=was_cached,
        )
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        _log_query(request.query, request.source, resolved_source, was_cached, False, latency_ms)
        _LOGGER.error("Search failed for query '%s': %s", request.query, e)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result="",
            success=False,
            cached=False,
            error=str(e),
        )


@app.get("/logs")
def query_logs(limit: int = 50):
    """
    Show recent query log entries. Returns the most recent queries with
    timestamp, source, cached flag, success, and latency in milliseconds.
    """
    try:
        con = _connect(_LOG_DB)
        rows = con.execute(
            "SELECT timestamp, query, source_requested, source_used, cached, success, latency_ms FROM query_log ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        con.close()
        entries = [
            {
                "timestamp": r[0],
                "query": r[1],
                "source_requested": r[2],
                "source_used": r[3],
                "cached": bool(r[4]),
                "success": bool(r[5]),
                "latency_ms": r[6],
            }
            for r in rows
        ]
        return {"count": len(entries), "entries": entries}
    except Exception as e:
        return {"count": 0, "entries": [], "error": str(e)}


@app.post("/logs/clear")
def logs_clear():
    """Clear all query log entries."""
    try:
        con = _connect(_LOG_DB)
        cur = con.execute("DELETE FROM query_log")
        count = cur.rowcount
        con.commit()
        con.close()
        return {"status": "cleared", "entries_removed": count}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/backup")
def backup():
    """
    Create a backup of all Mnemolis data — result cache, routing cache,
    query log, and snapshot history — and return it as a downloadable tarball.

    Restore by stopping the container, extracting the tarball into the
    /app/data volume, and restarting. See README for full instructions.
    """
    import tarfile
    import tempfile

    data_files = [
        "/app/data/cache.json",
        "/app/data/routing_cache.json",
        "/app/data/query_log.db",
        "/app/data/snapshots.db",
    ]

    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        tmp_path = tmp.name
        tmp.close()

        with tarfile.open(tmp_path, "w:gz") as tar:
            included = []
            for f in data_files:
                if os.path.exists(f):
                    tar.add(f, arcname=os.path.basename(f))
                    included.append(os.path.basename(f))

        timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        filename = f"mnemolis-backup-{timestamp}.tar.gz"

        return FileResponse(
            tmp_path,
            media_type="application/gzip",
            filename=filename,
            background=BackgroundTask(os.unlink, tmp_path),
        )
    except Exception as e:
        _LOGGER.error("Backup failed: %s", e)
        return {"status": "error", "error": str(e)}


@app.get("/backup/info")
def backup_info():
    """
    Show what would be included in a backup without creating one —
    file sizes and last-modified times for each data file.
    """
    data_files = [
        "/app/data/cache.json",
        "/app/data/routing_cache.json",
        "/app/data/query_log.db",
        "/app/data/snapshots.db",
    ]
    info = {}
    for f in data_files:
        name = os.path.basename(f)
        if os.path.exists(f):
            stat = os.stat(f)
            info[name] = {
                "exists": True,
                "size_bytes": stat.st_size,
                "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
            }
        else:
            info[name] = {"exists": False}
    return {"files": info}
def logs_clear():
    """Clear all query log entries."""
    try:
        con = _connect(_LOG_DB)
        cur = con.execute("DELETE FROM query_log")
        count = cur.rowcount
        con.commit()
        con.close()
        return {"status": "cleared", "entries_removed": count}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/changes")
def changes(hours: int = 24):
    """
    Return meaningful changes detected across all snapshot sources
    within the last N hours (default 24).

    Detects:
    - Service outages and recoveries (Uptime Kuma)
    - Meaningful weather forecast changes (Open-Meteo)
    - New news articles (FreshRSS)
    - Lock state changes, door sensor changes, low battery alerts (Home Assistant)
    """
    detected = get_changes(since_hours=hours)
    formatted = format_changes(detected, since_hours=hours)
    return {
        "since_hours": hours,
        "changes_detected": sum(len(v) for v in detected.values()),
        "sources_with_changes": list(detected.keys()),
        "result": formatted,
    }


@app.post("/snapshots/trigger")
def trigger_snapshots():
    """Manually trigger all snapshot jobs immediately."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(snapshot_uptime),
            executor.submit(snapshot_forecast),
            executor.submit(snapshot_news),
            executor.submit(snapshot_ha),
        ]
        concurrent.futures.wait(futures)
    return {"status": "ok", "snapshots_triggered": ["uptime", "forecast", "news", "ha"]}
def logs_clear():
    """Clear all query log entries."""
    try:
        con = _connect(_LOG_DB)
        cur = con.execute("DELETE FROM query_log")
        count = cur.rowcount
        con.commit()
        con.close()
        return {"status": "cleared", "entries_removed": count}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/logs/stats")
def query_log_stats():
    """
    Query log statistics — observability into Mnemolis usage patterns.

    Returns:
    - Total queries, cache hit rate, success rate
    - Time To First Knowledge (TTFK) — average latency for first-seen queries
    - Average latency by source
    - Top 10 most-asked queries
    - Queries per source breakdown
    - Repeated queries with cache hit rate
    """
    try:
        con = _connect(_LOG_DB)

        # Total queries and basic rates
        totals = con.execute("""
            SELECT
                COUNT(*) as total,
                SUM(cached) as cache_hits,
                SUM(success) as successes,
                AVG(latency_ms) as avg_latency_ms
            FROM query_log
        """).fetchone()

        total = totals[0] or 0
        cache_hits = totals[1] or 0
        successes = totals[2] or 0
        avg_latency = round(totals[3] or 0, 1)

        # TTFK — average latency of first-seen queries (cached=0, first occurrence)
        # A query's "cold" cost is its first appearance in the log
        ttfk_rows = con.execute("""
            SELECT AVG(latency_ms) FROM (
                SELECT MIN(id) as first_id, MIN(latency_ms) as latency_ms
                FROM query_log
                WHERE cached = 0
                GROUP BY LOWER(TRIM(query))
            )
        """).fetchone()
        ttfk_ms = round(ttfk_rows[0] or 0, 1)

        # Average latency by source (warm queries only — cached=1 or repeated)
        latency_by_source = {}
        for row in con.execute("""
            SELECT source_used, AVG(latency_ms) as avg_ms, COUNT(*) as count
            FROM query_log
            GROUP BY source_used
            ORDER BY count DESC
        """).fetchall():
            latency_by_source[row[0]] = {
                "avg_latency_ms": round(row[1], 1),
                "query_count": row[2],
            }

        # Top 10 most asked queries
        top_queries = []
        for row in con.execute("""
            SELECT
                LOWER(TRIM(query)) as q,
                COUNT(*) as times_asked,
                SUM(cached) as cache_hits,
                MIN(latency_ms) as min_latency_ms,
                AVG(latency_ms) as avg_latency_ms,
                source_used
            FROM query_log
            GROUP BY LOWER(TRIM(query))
            ORDER BY times_asked DESC
            LIMIT 10
        """).fetchall():
            top_queries.append({
                "query": row[0],
                "times_asked": row[1],
                "cache_hits": row[2],
                "cache_hit_rate": round(row[2] / row[1] * 100, 1) if row[1] > 0 else 0,
                "min_latency_ms": row[3],
                "avg_latency_ms": round(row[4], 1),
                "source": row[5],
            })

        # Queries seen more than once — these are the ones the system has "learned"
        learned = con.execute("""
            SELECT COUNT(*) FROM (
                SELECT LOWER(TRIM(query))
                FROM query_log
                GROUP BY LOWER(TRIM(query))
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0] or 0

        # Unique queries total
        unique = con.execute("""
            SELECT COUNT(DISTINCT LOWER(TRIM(query))) FROM query_log
        """).fetchone()[0] or 0

        con.close()

        return {
            "total_queries": total,
            "unique_queries": unique,
            "learned_queries": learned,
            "cache_hit_rate_pct": round(cache_hits / total * 100, 1) if total > 0 else 0,
            "success_rate_pct": round(successes / total * 100, 1) if total > 0 else 0,
            "avg_latency_ms": avg_latency,
            "ttfk_ms": ttfk_ms,
            "latency_by_source": latency_by_source,
            "top_queries": top_queries,
        }

    except Exception as e:
        return {"error": str(e)}
    """Clear all query log entries."""
    try:
        con = _connect(_LOG_DB)
        cur = con.execute("DELETE FROM query_log")
        count = cur.rowcount
        con.commit()
        con.close()
        return {"status": "cleared", "entries_removed": count}
    except Exception as e:
        return {"status": "error", "error": str(e)}
