"""
Mnemolis Snapshot Engine
Periodically captures source states and diffs them to detect meaningful changes.

Phase 1 sources: uptime, forecast, news
Phase 2: HA structured entity snapshots
"""
import sqlite3
import logging
import time
from datetime import datetime, timezone, timedelta

_LOGGER = logging.getLogger(__name__)

SNAPSHOT_DB = "/app/data/snapshots.db"

# How many snapshots to retain per source
MAX_SNAPSHOTS_PER_SOURCE = 288  # 24 hours at 5-minute intervals


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and busy timeout to reduce lock contention."""
    con = sqlite3.connect(db_path, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    return con


def init_snapshot_db():
    """Create snapshot tables if they don't exist."""
    try:
        con = _connect(SNAPSHOT_DB)
        con.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_source_time ON snapshots (source, timestamp DESC)")
        con.commit()
        con.close()
        _LOGGER.info("Snapshot DB initialized")
    except Exception as e:
        _LOGGER.warning("Could not initialize snapshot DB: %s", e)


def _store_snapshot(source: str, content: str):
    """Store a snapshot and prune old entries."""
    try:
        con = _connect(SNAPSHOT_DB)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "INSERT INTO snapshots (timestamp, source, content) VALUES (?, ?, ?)",
            (now, source, content)
        )
        # Prune old snapshots — keep only the most recent N per source
        con.execute("""
            DELETE FROM snapshots WHERE source = ? AND id NOT IN (
                SELECT id FROM snapshots WHERE source = ?
                ORDER BY id DESC LIMIT ?
            )
        """, (source, source, MAX_SNAPSHOTS_PER_SOURCE))
        con.commit()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not store snapshot for '%s': %s", source, e)


def _get_last_snapshots(source: str, limit: int = 2) -> list[str]:
    """Return the most recent N snapshots for a source."""
    try:
        con = _connect(SNAPSHOT_DB)
        rows = con.execute(
            "SELECT content FROM snapshots WHERE source = ? ORDER BY id DESC LIMIT ?",
            (source, limit)
        ).fetchall()
        con.close()
        return [r[0] for r in rows]
    except Exception as e:
        _LOGGER.warning("Could not fetch snapshots for '%s': %s", source, e)
        return []


def _get_snapshots_since(source: str, since_hours: int = 24) -> list[tuple[str, str]]:
    """Return all snapshots for a source since N hours ago."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        con = _connect(SNAPSHOT_DB)
        rows = con.execute(
            "SELECT timestamp, content FROM snapshots WHERE source = ? AND timestamp >= ? ORDER BY id ASC",
            (source, since)
        ).fetchall()
        con.close()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        _LOGGER.warning("Could not fetch snapshots since %dh for '%s': %s", since_hours, source, e)
        return []


# ---------------------------------------------------------------------------
# Diff engines per source
# ---------------------------------------------------------------------------

def _diff_uptime(old: str, new: str) -> list[str]:
    """Detect service status changes between two uptime snapshots."""
    changes = []
    if old == new:
        return changes

    # Both "all up" — no change
    if "all" in old.lower() and "up" in old.lower() and "all" in new.lower() and "up" in new.lower():
        return changes

    # Service went down
    if "all" in old.lower() and "up" in old.lower() and ("down" in new.lower() or "all" not in new.lower()):
        changes.append(f"Service outage detected: {new.strip()}")

    # Service came back up
    elif ("down" in old.lower() or "all" not in old.lower()) and "all" in new.lower() and "up" in new.lower():
        changes.append("All services restored — previously reported outage resolved")

    # Different outage states
    elif old != new:
        changes.append(f"Service status changed: {new.strip()}")

    return changes


def _diff_forecast(old: str, new: str) -> list[str]:
    """Detect meaningful weather changes between two forecast snapshots."""
    import re
    changes = []
    if old == new:
        return changes

    def extract_high(text: str) -> float | None:
        m = re.search(r"high of (?:about )?(\d+)", text)
        return float(m.group(1)) if m else None

    def extract_low(text: str) -> float | None:
        m = re.search(r"low of (\d+)", text)
        return float(m.group(1)) if m else None

    old_high = extract_high(old)
    new_high = extract_high(new)
    old_low = extract_low(old)
    new_low = extract_low(new)

    if old_high and new_high and abs(new_high - old_high) >= 5:
        direction = "up" if new_high > old_high else "down"
        changes.append(f"Forecast high changed {direction} to {int(new_high)}°")

    if old_low and new_low and abs(new_low - old_low) >= 5:
        direction = "up" if new_low > old_low else "down"
        changes.append(f"Forecast low changed {direction} to {int(new_low)}°")

    # Check for precipitation appearing or disappearing
    old_has_rain = any(w in old.lower() for w in ["rain", "storm", "thunder", "shower", "precipitation"])
    new_has_rain = any(w in new.lower() for w in ["rain", "storm", "thunder", "shower", "precipitation"])
    if not old_has_rain and new_has_rain:
        changes.append("Precipitation now in forecast")
    elif old_has_rain and not new_has_rain:
        changes.append("Precipitation removed from forecast")

    return changes


def _diff_news(old: str, new: str) -> list[str]:
    """Detect new articles between two news snapshots."""
    changes = []
    if old == new:
        return changes

    def extract_headlines(text: str) -> set[str]:
        headlines = set()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("**") and line.endswith("**"):
                headlines.add(line.strip("*").strip())
            elif line.startswith("**") and "**" in line[2:]:
                headline = line[2:line.index("**", 2)].strip()
                if headline:
                    headlines.add(headline)
        return headlines

    old_headlines = extract_headlines(old)
    new_headlines = extract_headlines(new)
    new_stories = new_headlines - old_headlines

    for story in sorted(new_stories)[:5]:  # cap at 5 new stories
        changes.append(f"New article: {story}")

    return changes


def _diff_ha(old: str, new: str) -> list[str]:
    """Detect meaningful entity state changes between two HA snapshots.

    Focuses on:
    - Lock state changes (locked/unlocked)
    - Door sensor state changes (open/closed)
    - Battery level crossing below 20%
    - New motion events

    Ignores lights and switches — too noisy for a "what changed" summary.
    """
    import json
    changes = []
    if old == new:
        return changes

    try:
        old_entities = {e["entity_id"]: e for e in json.loads(old)}
        new_entities = {e["entity_id"]: e for e in json.loads(new)}
    except (json.JSONDecodeError, TypeError, KeyError):
        return changes

    for entity_id, new_e in new_entities.items():
        old_e = old_entities.get(entity_id)
        if old_e is None:
            continue  # new entity, not a state change

        name = new_e.get("friendly_name", entity_id)
        domain = entity_id.split(".")[0]
        dc = new_e.get("device_class", "")

        # Lock state changes
        if domain == "lock" and old_e["state"] != new_e["state"]:
            changes.append(f"{name} {new_e['state']}")

        # Door sensor state changes
        elif dc == "door" and old_e["state"] != new_e["state"]:
            state_label = "opened" if new_e["state"] == "on" else "closed"
            changes.append(f"{name} {state_label}")

        # Battery crossing below 20%
        elif dc == "battery":
            try:
                old_val = float(old_e["state"])
                new_val = float(new_e["state"])
                if old_val >= 20 and new_val < 20:
                    changes.append(f"{name} battery low: {new_val:.0f}%")
            except (ValueError, TypeError):
                pass

    return changes


# ---------------------------------------------------------------------------
# Snapshot jobs — called by scheduler
# ---------------------------------------------------------------------------

def snapshot_uptime():
    """Capture uptime status snapshot."""
    try:
        from app.sources.uptime_kuma import search
        result = search("are all services up")
        _store_snapshot("uptime", result)
        _LOGGER.debug("Uptime snapshot stored")
    except Exception as e:
        _LOGGER.warning("Uptime snapshot failed: %s", e)


def snapshot_forecast():
    """Capture forecast snapshot."""
    try:
        from app.sources.forecast import search
        result = search("what is the weather forecast")
        _store_snapshot("forecast", result)
        _LOGGER.debug("Forecast snapshot stored")
    except Exception as e:
        _LOGGER.warning("Forecast snapshot failed: %s", e)


def snapshot_news():
    """Capture news snapshot."""
    try:
        from app.sources.freshrss import search
        result = search("latest news headlines")
        _store_snapshot("news", result)
        _LOGGER.debug("News snapshot stored")
    except Exception as e:
        _LOGGER.warning("News snapshot failed: %s", e)


def snapshot_ha():
    """Capture raw HA entity state snapshot as JSON for structured diffing."""
    try:
        import json
        import requests
        from app.config import settings

        if not settings.ha_url or not settings.ha_token:
            return

        resp = requests.get(
            f"{settings.ha_url}/api/states",
            headers={"Authorization": f"Bearer {settings.ha_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        states = resp.json()

        # Only store fields relevant to diffing — keep snapshot size small
        relevant = []
        for e in states:
            domain = e["entity_id"].split(".")[0]
            dc = e.get("attributes", {}).get("device_class", "")
            is_lock = domain == "lock"
            is_relevant_binary_sensor = domain == "binary_sensor" and dc in ("door", "motion", "window", "opening")
            is_battery = dc == "battery"
            if is_lock or is_relevant_binary_sensor or is_battery:
                relevant.append({
                    "entity_id": e["entity_id"],
                    "state": e["state"],
                    "friendly_name": e.get("attributes", {}).get("friendly_name", e["entity_id"]),
                    "device_class": dc,
                })

        _store_snapshot("ha", json.dumps(relevant))
        _LOGGER.debug("HA snapshot stored — %d relevant entities", len(relevant))
    except Exception as e:
        _LOGGER.warning("HA snapshot failed: %s", e)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

_DIFF_FNS = {
    "uptime": _diff_uptime,
    "forecast": _diff_forecast,
    "news": _diff_news,
    "ha": _diff_ha,
}


def get_changes(since_hours: int = 24) -> dict[str, list[str]]:
    """
    Return meaningful changes detected across all snapshot sources
    within the last N hours.

    Returns dict of {source: [change_description, ...]}
    """
    changes = {}

    for source, diff_fn in _DIFF_FNS.items():
        snapshots = _get_snapshots_since(source, since_hours=since_hours)
        if len(snapshots) < 2:
            continue

        source_changes = []
        # Walk consecutive pairs looking for changes
        seen_changes = set()
        for i in range(len(snapshots) - 1):
            ts_old, content_old = snapshots[i]
            ts_new, content_new = snapshots[i + 1]
            diffs = diff_fn(content_old, content_new)
            for diff in diffs:
                if diff not in seen_changes:
                    source_changes.append({"timestamp": ts_new, "change": diff})
                    seen_changes.add(diff)

        if source_changes:
            changes[source] = source_changes

    return changes


def format_changes(changes: dict, since_hours: int = 24) -> str:
    """Format changes dict into a human-readable summary."""
    if not changes:
        return f"No significant changes detected in the last {since_hours} hours."

    parts = []
    source_labels = {"uptime": "Services", "forecast": "Weather", "news": "News", "ha": "Home"}

    for source, items in changes.items():
        label = source_labels.get(source, source.upper())
        lines = [f"**{label}:**"]
        for item in items:
            ts = item["timestamp"].replace("T", " ").replace("Z", " UTC")
            lines.append(f"- {item['change']} ({ts})")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)
