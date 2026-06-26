import logging
from uptime_kuma_api import UptimeKumaApi
from app.config import settings

_LOGGER = logging.getLogger(__name__)


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


def search(query: str) -> str:
    """
    Query Uptime Kuma for monitor status.
    Returns a summary of any down services, or confirmation that all are up.
    """
    if not settings.uptime_kuma_url or not settings.uptime_kuma_username:
        return "Uptime Kuma is not configured. Set UPTIME_KUMA_URL and UPTIME_KUMA_USERNAME."

    try:
        with UptimeKumaApi(settings.uptime_kuma_url, timeout=settings.uptime_kuma_timeout_seconds) as api:
            api.login(settings.uptime_kuma_username, settings.uptime_kuma_password)
            monitors = api.get_monitors()
            heartbeats = api.get_heartbeats()
    except Exception as e:
        _LOGGER.error("Failed to connect to Uptime Kuma: %s", e)
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
