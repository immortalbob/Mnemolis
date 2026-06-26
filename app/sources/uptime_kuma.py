import logging
import threading
from uptime_kuma_api import UptimeKumaApi
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# Persistent Socket.IO connection, reused across calls rather than opened
# fresh on every search()/snapshot_uptime() invocation. See
# get_connection() below for why this exists and what was confirmed
# directly against the installed uptime_kuma_api/python-socketio source
# before this was built — the short version: UptimeKumaApi has no
# `.connected` property of its own (an assumption an earlier draft of
# this fix made and would have shipped wrong), but its underlying
# `sio` (a `python-socketio` `Client`) does — `sio.connected` is a real,
# public attribute on `socketio.base_client.BaseClient`, `True` after a
# successful connect, `False` after any disconnect.
_persistent_api: "UptimeKumaApi | None" = None

# uptime_kuma_api's underlying python-socketio client isn't documented
# as thread-safe for concurrent calls from multiple threads, and
# Mnemolis can genuinely call search() from more than one thread at
# once — a live request and the 2-minute snapshot_uptime() scheduler
# tick landing close together. This lock serializes access to the
# single shared connection rather than risk a real, hard-to-debug race
# inside the socketio client.
_connection_lock = threading.Lock()


def _get_status_from_heartbeats(heartbeats: dict, monitor_id: int) -> int | None:
    """Extract most recent status from heartbeats for a given monitor.

    Returns None — not a status code — when no real heartbeat data
    exists at all. Found via a deliberate complexity-investigation pass:
    the previous version defaulted to 3 (MAINTENANCE) when no heartbeat
    existed, silently misreporting a monitor that simply hasn't run its
    first check yet (a brand-new monitor, or one whose check interval
    hasn't fired since Uptime Kuma's own restart) as "in maintenance" —
    a specific, false claim about a deliberately-configured state the
    monitor was never actually in. None is used as the sentinel
    specifically because 0/1/2/3 are all real, valid MonitorStatus
    values (DOWN/UP/PENDING/MAINTENANCE) — reusing any of them to also
    mean "no data" would create the exact same kind of ambiguity this
    fix is meant to close.
    """
    hb = heartbeats.get(monitor_id, [])
    if not isinstance(hb, list):
        hb = []
    last = None
    for item in hb:
        if isinstance(item, dict):
            last = item
    return last.get("status") if last else None


def get_connection() -> "UptimeKumaApi":
    """Returns the persistent connection, creating and logging in on
    first use, reconnecting if a prior call left it in a dead state.

    Never raises on the dead-connection check itself — that's the
    caller's job, the same way it already is for every other failure
    mode in this module. Callers must hold _connection_lock.

    `UptimeKumaApi(...)` connects in its own `__init__` (confirmed
    directly in the installed library source), so constructing it here
    already performs the Socket.IO handshake — no separate `.connect()`
    call is needed on top of it.
    """
    global _persistent_api
    if _persistent_api is None or not _persistent_api.sio.connected:
        if _persistent_api is not None:
            try:
                _persistent_api.disconnect()
            except Exception:
                pass  # already dead; nothing real to clean up
        _persistent_api = UptimeKumaApi(
            settings.uptime_kuma_url, timeout=settings.uptime_kuma_timeout_seconds
        )
        _persistent_api.login(settings.uptime_kuma_username, settings.uptime_kuma_password)
    return _persistent_api


def disconnect() -> None:
    """Cleanly closes the persistent connection, if one exists.

    Safe to call when no connection has ever been opened (e.g. Uptime
    Kuma was never configured) — no-ops rather than raising. Intended
    for app shutdown via main.py's lifespan, mirroring the pattern
    already used there for the snapshot scheduler.
    """
    global _persistent_api
    with _connection_lock:
        if _persistent_api is not None:
            try:
                _persistent_api.disconnect()
            except Exception:
                pass  # shutting down anyway; nothing left to do with the error
            _persistent_api = None


def search(query: str) -> str:
    """
    Query Uptime Kuma for monitor status.
    Returns a summary of any down services, or confirmation that all are up.
    """
    global _persistent_api

    if not settings.uptime_kuma_url or not settings.uptime_kuma_username:
        return "Uptime Kuma is not configured. Set UPTIME_KUMA_URL and UPTIME_KUMA_USERNAME."

    try:
        with _connection_lock:
            api = get_connection()
            monitors = api.get_monitors()
            heartbeats = api.get_heartbeats()
    except Exception as e:
        _LOGGER.error("Failed to connect to Uptime Kuma: %s", e)
        # Force a fresh connection attempt next call — whatever state
        # the current one is in after an exception here is assumed bad.
        with _connection_lock:
            _persistent_api = None
        return f"Could not connect to Uptime Kuma: {e}"

    if not monitors:
        return "No monitors found in Uptime Kuma."

    down = []
    pending = []
    maintenance = []
    no_data = []
    up_count = 0

    for monitor in monitors:
        mid = monitor.get("id")
        name = monitor.get("name", f"Monitor {mid}")
        status = _get_status_from_heartbeats(heartbeats, mid)

        if status is None:
            no_data.append(name)
        elif status == 1:
            up_count += 1
        elif status == 0:
            down.append(name)
        elif status == 2:
            pending.append(name)
        elif status == 3:
            maintenance.append(name)

    total = len(monitors)
    _LOGGER.info(
        "Uptime Kuma: %d up, %d down, %d pending, %d maintenance, %d no data, of %d total",
        up_count, len(down), len(pending), len(maintenance), len(no_data), total
    )

    if not down and not pending and not no_data:
        parts = [f"All {up_count} monitored services are up."]
        if maintenance:
            parts.append(f"In maintenance: {', '.join(maintenance)}.")
        return " ".join(parts)

    parts = []
    if down:
        parts.append(f"DOWN ({len(down)}): {', '.join(down)}.")
    if pending:
        parts.append(f"PENDING ({len(pending)}): {', '.join(pending)}.")
    if maintenance:
        parts.append(f"In maintenance: {', '.join(maintenance)}.")
    if no_data:
        parts.append(f"No heartbeat data yet ({len(no_data)}): {', '.join(no_data)}.")
    parts.append(f"{up_count} of {total} services are up.")

    return " ".join(parts)
