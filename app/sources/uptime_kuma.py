import logging
from uptime_kuma_api import UptimeKumaApi
from app.config import settings

_LOGGER = logging.getLogger(__name__)

STATUS_LABELS = {
    0: "DOWN",
    1: "UP",
    2: "PENDING",
    3: "MAINTENANCE",
}


def _get_status_from_heartbeats(heartbeats: dict, monitor_id: int) -> int:
    """Extract most recent status from heartbeats for a given monitor."""
    hb = heartbeats.get(monitor_id, [])
    if not isinstance(hb, list):
        hb = []
    last = None
    for item in hb:
        if isinstance(item, dict):
            last = item
    return last.get("status", 3) if last else 3


def search(query: str) -> str:
    """
    Query Uptime Kuma for monitor status.
    Returns a summary of any down services, or confirmation that all are up.
    """
    if not settings.uptime_kuma_url or not settings.uptime_kuma_username:
        return "Uptime Kuma is not configured. Set UPTIME_KUMA_URL and UPTIME_KUMA_USERNAME."

    try:
        with UptimeKumaApi(settings.uptime_kuma_url, timeout=30) as api:
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
    up_count = 0

    for monitor in monitors:
        mid = monitor.get("id")
        name = monitor.get("name", f"Monitor {mid}")
        status = _get_status_from_heartbeats(heartbeats, mid)

        if status == 1:
            up_count += 1
        elif status == 0:
            down.append(name)
        elif status == 2:
            pending.append(name)
        elif status == 3:
            maintenance.append(name)

    total = len(monitors)
    _LOGGER.info(
        "Uptime Kuma: %d up, %d down, %d pending, %d maintenance of %d total",
        up_count, len(down), len(pending), len(maintenance), total
    )

    if not down and not pending:
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
    parts.append(f"{up_count} of {total} services are up.")

    return " ".join(parts)
