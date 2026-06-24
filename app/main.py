import asyncio
import logging
import os
import sqlite3
import time
import requests
from contextlib import asynccontextmanager, AsyncExitStack
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.routing import Mount
from pydantic import BaseModel

from app.router import (
    route_with_source,
    SOURCE_MAP,
    FALLBACK_CHAIN,
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
from app.mcp_server import mcp_app, get_mcp_app
from app.sources.kiwix import get_books, refresh_catalog
from app.snapshots import (
    init_snapshot_db,
    snapshot_uptime,
    snapshot_forecast,
    snapshot_news,
    snapshot_ha,
    get_changes,
    format_changes,
    get_snapshot_job_health,
)
from app.config import settings

# Configure logging at startup — without this, the root logger defaults to
# WARNING with no attached handler, which silently swallows every
# _LOGGER.info() call across the entire codebase (router.py's decomposition
# logging, kiwix.py's disambiguation/article selection logging, snapshots.py's
# job logging, etc). Only uvicorn's own access logger (a separate logger with
# its own handler) was ever visible in `docker logs`, making it look like
# requests were processed silently with no application-level diagnostic
# output at all — found via real debugging where expected INFO log lines
# never appeared despite the underlying code paths definitely running.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# API key authentication — protects /search and /changes only
# ---------------------------------------------------------------------------

def _valid_api_keys() -> set[str]:
    """Parse the comma-separated API_KEYS setting into a set of valid keys."""
    if not settings.api_keys:
        return set()
    return {k.strip() for k in settings.api_keys.split(",") if k.strip()}


async def require_api_key(x_api_key: str | None = Header(default=None)):
    """
    FastAPI dependency enforcing API key auth on protected endpoints.
    No-op (always passes) if API_KEYS is unset — auth is opt-in and
    backward compatible with existing deployments.
    """
    valid_keys = _valid_api_keys()
    if not valid_keys:
        return  # auth disabled
    if not x_api_key or x_api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")

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
    """Create query log table if it doesn't exist, and migrate existing
    tables to add columns introduced after the table was first created.

    CREATE TABLE IF NOT EXISTS only affects fresh installs — an existing
    deployment's table already exists and won't gain new columns just
    because the CREATE statement changed, so any new column needs an
    explicit ALTER TABLE migration here, run defensively (existing
    databases that already have the column will hit a harmless,
    caught exception on the ALTER).
    """
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
                latency_ms INTEGER NOT NULL,
                fallback_occurred INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Migration for tables created before fallback_occurred existed —
        # ALTER TABLE ADD COLUMN fails harmlessly if the column is already
        # present (fresh installs created with the CREATE TABLE above
        # already have it), so this is safe to run unconditionally
        try:
            con.execute("ALTER TABLE query_log ADD COLUMN fallback_occurred INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # column already exists
        con.commit()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not initialize query log db: %s", e)


def _log_query(query: str, source_requested: str, source_used: str, cached: bool, success: bool, latency_ms: int, fallback_occurred: bool = False):
    """Write a query log entry."""
    try:
        con = _connect(_LOG_DB)
        con.execute(
            "INSERT INTO query_log (timestamp, query, source_requested, source_used, cached, success, latency_ms, fallback_occurred) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), query, source_requested, source_used, int(cached), int(success), latency_ms, int(fallback_occurred))
        )
        con.commit()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not write query log: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load Kiwix catalog, cache, and start snapshot scheduler on startup.

    Also rebuilds and re-mounts the MCP Streamable HTTP app fresh on every
    startup, then enters its lifespan — a real, currently-open issue
    across the broader MCP/FastMCP ecosystem (not specific to how
    Mnemolis is built) made this more involved than a simple "enter the
    sub-app's lifespan too":

    `FastMCP.streamable_http_app()` lazily creates ONE session manager
    and caches it on the FastMCP instance — calling it again still
    returns the same cached manager wrapped in a NEW Starlette app, but
    `StreamableHTTPSessionManager.run()` can only ever be entered once
    per instance. A module-level `mcp_app` built once at import time
    means every independent app lifecycle (every container restart in
    production; every `with TestClient(app) as client:` block in this
    test suite) tries to re-run the same already-exhausted session
    manager, raising a hard RuntimeError on the second attempt — this
    surfaced as several `test_security.py` tests failing only when run
    after `test_main.py`, since both files spin up their own TestClient
    against the same imported `app`.

    Verified the first attempt at fixing this was genuinely incomplete:
    resetting `mcp._session_manager` to None before calling
    `streamable_http_app()` again does create a fresh session manager,
    but the ALREADY-MOUNTED route from module-import time still holds a
    reference to the OLD app object's lifespan closure and request
    handler — resetting the FastMCP instance's cached attribute doesn't
    retroactively change what an already-built Starlette app's router
    closure points to. The real fix rebuilds the app fresh AND finds the
    actual `/mcp` Mount route in app.router.routes to reassign its `.app`
    reference, so the object whose lifespan gets entered is genuinely the
    same object that serves real requests during that lifecycle — not
    two different objects that happen to share an FastMCP instance.
    """
    fresh_mcp_app = get_mcp_app()
    for r in app.router.routes:
        if isinstance(r, Mount) and r.path == "/mcp":
            r.app = fresh_mcp_app
            break

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(fresh_mcp_app.router.lifespan_context(fresh_mcp_app))

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
    version="3.31.1",
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
    Health check. Returns container status, Kiwix books loaded, result and
    routing cache entry counts (with their configured max sizes, so growth
    toward the bound is visible without needing to dig through logs),
    background snapshot job health (each job's status compared against
    its expected interval, since every snapshot job already catches its
    own exceptions and silently logs a warning rather than surfacing
    failure anywhere externally visible), and connectivity status for
    every configured source.
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
        "cache_max_size": settings.cache_max_size,
        "routing_cache_entries": len(get_routing_cache_stats()),
        "routing_cache_max_size": settings.routing_cache_max_size,
        "snapshot_jobs": get_snapshot_job_health(),
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


@app.post("/search", response_model=SearchResponse, dependencies=[Depends(require_api_key)])
def search(request: SearchRequest):
    intent = None
    if request.source == "auto":
        intent = detect_intent(request.query)
        if isinstance(intent, list):
            was_cached = check_cached("fusion", f"fusion[{','.join(sorted(intent))}]:{request.query}")
        else:
            was_cached = check_cached(intent, request.query)
    else:
        was_cached = check_cached(request.source, request.query)

    start = time.monotonic()
    try:
        # route_with_source returns the ACTUAL source that produced the
        # result, not just the originally-intended one — a query routed
        # to 'kiwix' that returns nothing usable can silently fall back to
        # 'web' internally, and source_used must reflect that real outcome
        # rather than echoing back whatever intent detection guessed
        # before route() ran. Found via real usage where a GPIO
        # troubleshooting query's response claimed source_used="kiwix"
        # while the actual content was a web search result.
        result, resolved_source = route_with_source(request.query, request.source, request.fusion_sources)
        latency_ms = int((time.monotonic() - start) * 1000)

        # Detect fallback occurrence without changing route_with_source()'s
        # return signature at all — that function already recurses into
        # itself at 4 internal call sites (conditional detection, remainder
        # handling), so widening its return tuple would touch every one of
        # those, a much larger and riskier change than this comparison.
        # 'intent' (or request.source for explicit requests) is what was
        # decided BEFORE route_with_source ran; comparing it against
        # FALLBACK_CHAIN's known mapping for resolved_source tells us
        # whether a fallback actually happened, using only data that
        # already existed at this call site.
        intended_source = intent if intent is not None else request.source
        fallback_occurred = (
            not isinstance(intended_source, list)
            and intended_source in FALLBACK_CHAIN
            and resolved_source == FALLBACK_CHAIN[intended_source]
        )

        _log_query(request.query, request.source, resolved_source, was_cached, True, latency_ms, fallback_occurred)
        return SearchResponse(
            query=request.query,
            source_used=resolved_source,
            result=result,
            success=True,
            cached=was_cached,
        )
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        _log_query(request.query, request.source, request.source, was_cached, False, latency_ms)
        _LOGGER.error("Search failed for query '%s': %s", request.query, e)
        return SearchResponse(
            query=request.query,
            source_used=request.source,
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


@app.get("/areas")
def areas():
    """
    List all Home Assistant areas detected via the area registry, with
    entity counts and the natural-language phrases that resolve to each
    one (e.g. "living room", "master bath").

    Returns not_configured if HA_URL/HA_TOKEN are unset, or error if the
    HA area registry could not be reached.
    """
    from app.sources.home_assistant import list_areas
    return list_areas()


@app.get("/changes", dependencies=[Depends(require_api_key)])
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


@app.get("/logs/stats")
def query_log_stats():
    """
    Query log statistics — observability into Mnemolis usage patterns.

    Returns:
    - Total queries, cache hit rate, success rate
    - Fallback count and rate — how often a result was empty enough to
      trigger FALLBACK_CHAIN (e.g. kiwix -> web). Detected via a single
      boolean column (fallback_occurred) computed by comparing the
      pre-route intended source against the actual resolved source,
      rather than changing route_with_source()'s return signature —
      that function already recurses into itself at 4 internal call
      sites, so widening its return tuple would be much more invasive
      than this comparison needed to be.
    - Fallback breakdown by TARGET, not original source — when multiple
      sources share the same fallback target (kiwix and news both fall
      back to web), the boolean alone can't distinguish which one
      triggered a given fallback, so this is reported as a combined,
      honestly-labeled count (e.g. "kiwix_or_news_fallback_to_web")
      rather than guessing at an attribution the data doesn't support
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
                AVG(latency_ms) as avg_latency_ms,
                SUM(fallback_occurred) as fallbacks
            FROM query_log
        """).fetchone()

        total = totals[0] or 0
        cache_hits = totals[1] or 0
        successes = totals[2] or 0
        avg_latency = round(totals[3] or 0, 1)
        fallbacks = totals[4] or 0

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

        # Average latency by source — combined across cold AND warm
        # queries, not warm-only. Found via a deliberate complexity-
        # investigation pass: this comment previously claimed "warm
        # queries only," but the SQL below has no cached filter at all
        # and never did. Verified precisely which behavior is actually
        # more useful before deciding which one was wrong: constructing
        # a realistic two-source comparison (one genuinely slow,
        # network-bound source; one genuinely fast, local one) showed
        # that a TRUE warm-only average would mask almost all of the
        # real difference between them (15ms vs 12ms in the test, vs.
        # a real, honest 3000ms vs 80ms cold-only difference) — cache
        # hits are fast regardless of source, so warm-only averaging
        # tells you almost nothing about which source is actually
        # expensive when it has to do real work. The combined number
        # below at least reflects real, paid latency, even though it's
        # sensitive to cache-hit ratio. A cold-only breakdown would be
        # the genuinely most diagnostic version of this metric, but
        # that's a real, deliberate scope decision for a future change,
        # not something this fix took on — ttfk_ms already covers the
        # cold-specific story in aggregate, just not broken out by
        # source the way this field is.
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

        # Fallback breakdown — reported per FALLBACK_CHAIN TARGET, not per
        # original source. A boolean column can't distinguish which
        # original source a fallback came from when multiple sources
        # share the same target (kiwix and news both fall back to web) —
        # querying "fallback_occurred=1 AND source_used='web'" would
        # double-count under separate 'kiwix' and 'news' labels if we
        # tried to attribute it to one or the other, since the same rows
        # would match both. Reporting it honestly as a combined count
        # against the shared target avoids that, at the cost of not being
        # able to say whether kiwix or news specifically struggled more —
        # a real, accepted limitation of using one boolean column rather
        # than recording the original source as text.
        fallback_by_target = {}
        fallback_targets = set(FALLBACK_CHAIN.values())
        for target in fallback_targets:
            sources_falling_back_here = [s for s, t in FALLBACK_CHAIN.items() if t == target]
            row = con.execute("""
                SELECT COUNT(*) FROM query_log
                WHERE fallback_occurred = 1 AND source_used = ?
            """, (target,)).fetchone()
            fallback_count = row[0] or 0
            if fallback_count > 0:
                label = "_or_".join(sorted(sources_falling_back_here))
                fallback_by_target[f"{label}_fallback_to_{target}"] = fallback_count

        # Top 10 most asked queries
        #
        # Found via a deliberate, precise re-read of this function:
        # selecting the bare `source_used` column directly here is
        # genuinely undefined per SQLite's own documentation — its
        # special "take the bare column from the row that produced the
        # aggregate" guarantee ONLY applies when there is exactly one
        # aggregate function and it's specifically MIN() or MAX(). This
        # query has four different aggregates (COUNT, SUM, MIN, AVG), so
        # that guarantee doesn't apply at all; which row's source_used
        # gets reported when the same query text was answered by
        # different sources at different times (a real, reachable case —
        # routing logic itself has changed multiple times over this
        # project's life) was not even reliably consistent, let alone
        # correct. Fixed with a correlated subquery reporting the MOST
        # RECENT source for each query — chosen over "most frequent"
        # because it stays accurate as routing logic evolves, rather
        # than continuing to report a stale answer from before a real
        # routing fix for as long as old log rows happen to outnumber
        # new ones. Verified this has no meaningful performance cost at
        # realistic homelab log volumes (3ms for 5000 rows / 300
        # distinct queries in direct testing).
        top_queries = []
        for row in con.execute("""
            SELECT
                LOWER(TRIM(q1.query)) as q,
                COUNT(*) as times_asked,
                SUM(q1.cached) as cache_hits,
                MIN(q1.latency_ms) as min_latency_ms,
                AVG(q1.latency_ms) as avg_latency_ms,
                (SELECT q2.source_used FROM query_log q2
                 WHERE LOWER(TRIM(q2.query)) = LOWER(TRIM(q1.query))
                 ORDER BY q2.id DESC LIMIT 1) as most_recent_source
            FROM query_log q1
            GROUP BY LOWER(TRIM(q1.query))
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
            "fallback_count": fallbacks,
            "fallback_rate_pct": round(fallbacks / total * 100, 1) if total > 0 else 0,
            "avg_latency_ms": avg_latency,
            "ttfk_ms": ttfk_ms,
            "latency_by_source": latency_by_source,
            "fallback_by_target": fallback_by_target,
            "top_queries": top_queries,
        }

    except Exception as e:
        return {"error": str(e)}
