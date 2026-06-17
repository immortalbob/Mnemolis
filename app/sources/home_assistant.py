"""
MiniSearch Home Assistant Source
Queries Home Assistant entity states for analytical and summary queries
that go beyond HA's built-in single-entity intent handling.

Useful for:
- Multi-entity summaries ("house status", "which lights are on")
- Environmental summaries ("indoor air quality", "outdoor conditions")
- Security status ("are the doors locked", "any recent motion")
- Battery status ("which devices need charging")
- Power consumption ("how much power are the lights using")
"""
import logging
import requests
from datetime import datetime, timezone
from app.config import settings

_LOGGER = logging.getLogger(__name__)

# Entity IDs to always exclude — internal HA entities, unavailable sensors, segments
_EXCLUDE_PATTERNS = [
    "light.backyard_light_2",  # duplicate sub-entity
    "light.shed_light_2",      # duplicate sub-entity
    "processor_temperature",
    "livingroomlilygo_esp32_temperature",
    "living_room_voice_va_temperature",
    "master_bedroom_voice_assistant_va_temperature",
    "bedroom_voice_va_temperature",
    "living_room_voice_motion",
    "master_bedroom_voice_assistant_motion",
    "bedroom_voice_motion",
    "maindash",
    "led_ring",
    "_segment_",  # TV backlight segments
    "_ding",       # doorbell ring events
    "va_temperature",
]

# Query keyword → filter spec
_QUERY_MAP = {
    # Lights
    "light": {"domains": ["light"], "state_filter": None},
    "lights": {"domains": ["light"], "state_filter": None},
    "lamp": {"domains": ["light"], "state_filter": None},
    "bulb": {"domains": ["light"], "state_filter": None},
    "on": {"domains": ["light"], "state_filter": "on"},
    "lights on": {"domains": ["light"], "state_filter": "on"},
    "lights off": {"domains": ["light"], "state_filter": "off"},
    # Locks and doors
    "lock": {"domains": ["lock"], "device_classes": ["door"]},
    "locked": {"domains": ["lock"], "device_classes": ["door"]},
    "door": {"domains": ["lock"], "device_classes": ["door"]},
    "secure": {"domains": ["lock"], "device_classes": ["door"]},
    "security status": {"domains": ["lock"], "device_classes": ["door", "motion", "occupancy"], "strict": True, "include_motion": True},
    "security": {"domains": ["lock"], "device_classes": ["door", "motion", "occupancy"], "strict": True},
    # Motion
    "motion": {"domains": ["event"], "event_keywords": ["motion"]},
    "camera": {"domains": ["event"], "event_keywords": ["motion"]},
    "activity": {"domains": ["event"], "event_keywords": ["motion"]},
    # Battery
    "battery": {"device_classes": ["battery"], "strict": True},
    "charging": {"device_classes": ["battery"], "strict": True},
    "low battery": {"device_classes": ["battery"], "strict": True},
    "battery levels": {"device_classes": ["battery"], "strict": True},
    "battery status": {"device_classes": ["battery"], "strict": True},
    # Environmental — indoor only
    "temperature": {"device_classes": ["temperature"], "exclude_entity_keywords": ["cotech", "processor", "esp32", "va_temperature"]},
    "temp": {"device_classes": ["temperature"], "exclude_entity_keywords": ["cotech", "processor", "esp32", "va_temperature"]},
    "humidity": {"device_classes": ["humidity"], "exclude_entity_keywords": ["cotech"]},
    "co2": {"device_classes": ["carbon_dioxide"]},
    "carbon": {"device_classes": ["carbon_dioxide"]},
    "air quality": {"device_classes": ["carbon_dioxide", "humidity", "temperature"], "exclude_entity_keywords": ["cotech", "processor", "esp32", "va_temperature"]},
    "air": {"device_classes": ["carbon_dioxide", "humidity", "temperature"], "exclude_entity_keywords": ["cotech", "processor", "esp32", "va_temperature"]},
    "indoor": {"device_classes": ["carbon_dioxide", "humidity", "temperature"], "exclude_entity_keywords": ["cotech", "processor", "esp32", "va_temperature"]},
    # Outdoor / weather station
    "outdoor conditions": {"entity_keywords": ["cotech"], "strict": True},
    "outside conditions": {"entity_keywords": ["cotech"], "strict": True},
    "outdoor": {"entity_keywords": ["cotech"]},
    "outside": {"entity_keywords": ["cotech"]},
    "weather station": {"entity_keywords": ["cotech"]},
    "wind": {"entity_keywords": ["wind"]},
    "rain": {"entity_keywords": ["rain"]},
    # Power
    "power": {"entity_keywords": ["consumption"], "strict": True},
    "consumption": {"entity_keywords": ["consumption"], "strict": True},
    "energy": {"device_classes": ["energy", "power"], "strict": True},
    # House summary
    "status": {"domains": ["light", "lock"], "device_classes": ["door", "battery", "motion"], "strict": True},
    "summary": {"domains": ["light", "lock"], "device_classes": ["door", "battery", "carbon_dioxide", "humidity", "temperature"], "exclude_entity_keywords": ["cotech", "processor", "esp32", "va_temperature"], "include_motion": True},
    "house": {"domains": ["light", "lock"], "device_classes": ["door", "battery", "carbon_dioxide", "humidity", "temperature"], "exclude_entity_keywords": ["cotech", "processor", "esp32", "va_temperature"], "include_motion": True},
    "home": {"domains": ["light", "lock"], "device_classes": ["door", "battery", "carbon_dioxide", "humidity", "temperature"], "exclude_entity_keywords": ["cotech", "processor", "esp32", "va_temperature"], "include_motion": True},
}


def _get_states() -> list[dict] | None:
    """Fetch all entity states from HA REST API."""
    if not settings.ha_url or not settings.ha_token:
        return None
    try:
        resp = requests.get(
            f"{settings.ha_url}/api/states",
            headers={"Authorization": f"Bearer {settings.ha_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _LOGGER.error("Failed to fetch HA states: %s", e)
        return None


def _friendly_name(entity: dict) -> str:
    return entity.get("attributes", {}).get("friendly_name", entity["entity_id"])


def _unit(entity: dict) -> str:
    return entity.get("attributes", {}).get("unit_of_measurement", "")


def _format_value(state: str, unit: str) -> str:
    """Round numeric values for cleaner display."""
    try:
        val = float(state)
        if unit in ("°F", "°C"):
            return f"{round(val, 1)} {unit}"
        if unit in ("%", "ppm", "mph", "in"):
            return f"{round(val, 1)} {unit}"
        if unit == "W":
            return f"{round(val, 1)} {unit}"
        return f"{val} {unit}"
    except ValueError:
        return f"{state} {unit}".strip()


def _format_entity(entity: dict) -> str:
    name = _friendly_name(entity)
    state = entity["state"]
    unit = _unit(entity)
    if unit:
        return f"{name}: {_format_value(state, unit)}"
    return f"{name}: {state}"


def _format_motion_event(entity: dict) -> str:
    """Format a motion event with how long ago it occurred."""
    name = _friendly_name(entity)
    state = entity["state"]
    try:
        dt = datetime.fromisoformat(state.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            age = "just now"
        elif minutes < 60:
            age = f"{minutes} minutes ago"
        elif minutes < 1440:
            hours = minutes // 60
            age = f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = minutes // 1440
            age = f"{days} day{'s' if days != 1 else ''} ago"
        return f"{name}: {age}"
    except Exception:
        return f"{name}: {state}"


def _is_excluded(entity: dict) -> bool:
    """Return True if this entity should always be excluded."""
    entity_id = entity["entity_id"]
    state = entity["state"]
    if state in ("unavailable", "unknown"):
        return True
    if any(p in entity_id for p in _EXCLUDE_PATTERNS):
        return True
    return False


def _build_filter(query: str) -> dict:
    """Build a merged filter spec from all matching query keywords."""
    query_lower = query.lower()
    domains = set()
    device_classes = set()
    entity_keywords = set()
    event_keywords = set()
    exclude_entity_keywords = set()
    state_filter = None
    strict = False
    include_motion = False

    # Match longest phrases first to avoid partial matches
    matched_any = False
    consumed_positions = set()

    for keyword in sorted(_QUERY_MAP.keys(), key=len, reverse=True):
        pos = query_lower.find(keyword)
        if pos == -1:
            continue
        # Skip if this keyword position is already covered by a longer match
        positions = set(range(pos, pos + len(keyword)))
        if positions & consumed_positions:
            continue
        consumed_positions |= positions
        spec = _QUERY_MAP[keyword]
        domains.update(spec.get("domains", []))
        device_classes.update(spec.get("device_classes", []))
        entity_keywords.update(spec.get("entity_keywords", []))
        event_keywords.update(spec.get("event_keywords", []))
        exclude_entity_keywords.update(spec.get("exclude_entity_keywords", []))
        if spec.get("state_filter"):
            state_filter = spec["state_filter"]
        if spec.get("strict"):
            strict = True
        if spec.get("include_motion"):
            include_motion = True
        matched_any = True

    # No match — return summary
    if not matched_any:
        return _build_filter("summary")

    return {
        "domains": domains,
        "device_classes": device_classes,
        "entity_keywords": entity_keywords,
        "event_keywords": event_keywords,
        "exclude_entity_keywords": exclude_entity_keywords,
        "state_filter": state_filter,
        "strict": strict,
        "include_motion": include_motion,
    }


def _matches_filter(entity: dict, f: dict) -> bool:
    """Return True if entity matches the filter spec."""
    entity_id = entity["entity_id"]
    domain = entity_id.split(".")[0]
    dc = entity.get("attributes", {}).get("device_class", "")
    state = entity["state"]

    if _is_excluded(entity):
        return False

    # State filter (e.g. only "on" lights)
    if f["state_filter"] and state != f["state_filter"]:
        return False

    # Exclude entity keywords (indoor sensors should exclude cotech etc)
    if f["exclude_entity_keywords"] and any(kw in entity_id for kw in f["exclude_entity_keywords"]):
        return False

    # Strict mode — only match domain OR device_class, not entity keywords bleeding in
    if f["strict"]:
        if domain == "event" and f["event_keywords"]:
            return any(kw in entity_id for kw in f["event_keywords"])
        if f["entity_keywords"] and any(kw in entity_id for kw in f["entity_keywords"]):
            return True
        if domain in f["domains"]:
            return True
        if dc in f["device_classes"]:
            return True
        return False

    # Event domain — match by entity name keywords
    if domain == "event" and f["event_keywords"]:
        return any(kw in entity_id for kw in f["event_keywords"])

    # Entity keyword match
    if f["entity_keywords"] and any(kw in entity_id for kw in f["entity_keywords"]):
        return True

    # Domain match
    if domain in f["domains"]:
        return True

    # Device class match
    if dc in f["device_classes"]:
        return True

    return False


def search(query: str) -> str:
    """Query Home Assistant entity states for analytical summaries."""
    if not settings.ha_url or not settings.ha_token:
        return "Home Assistant is not configured. Set HA_URL and HA_TOKEN."

    states = _get_states()
    if states is None:
        return "Could not connect to Home Assistant. Check HA_URL and HA_TOKEN."

    if not states:
        return "No entity states returned from Home Assistant."

    f = _build_filter(query)

    # Deduplicate by entity_id
    seen_ids = set()
    matched = []
    for e in states:
        if e["entity_id"] not in seen_ids and _matches_filter(e, f):
            seen_ids.add(e["entity_id"])
            matched.append(e)

    # Add motion events for summary queries
    if f["include_motion"]:
        for e in states:
            if e["entity_id"] not in seen_ids and e["entity_id"].split(".")[0] == "event" and "motion" in e["entity_id"]:
                if not _is_excluded(e):
                    seen_ids.add(e["entity_id"])
                    matched.append(e)

    if not matched:
        return "No matching entities found in Home Assistant for that query."

    _LOGGER.info("HA source: %d entities matched for query '%s'", len(matched), query[:50])

    # Track motion event entity names to avoid double-counting with binary_sensors
    motion_event_names = {
        e["entity_id"].replace("event.", "").replace("_motion", "")
        for e in matched
        if e["entity_id"].split(".")[0] == "event"
    }

    # Group by domain
    groups: dict[str, list[str]] = {}
    for entity in matched:
        domain = entity["entity_id"].split(".")[0]
        dc = entity.get("attributes", {}).get("device_class", "")

        # Skip motion binary_sensors if we have event-based motion data
        if domain == "binary_sensor" and dc == "motion" and motion_event_names:
            continue

        label = {
            "light": "Lights",
            "lock": "Locks",
            "binary_sensor": "Door Sensors",
            "sensor": "Sensors",
            "event": "Motion",
            "switch": "Switches",
        }.get(domain, domain.replace("_", " ").title())

        if label not in groups:
            groups[label] = []

        if domain == "event":
            groups[label].append(_format_motion_event(entity))
        else:
            groups[label].append(_format_entity(entity))

    parts = []
    for label, items in groups.items():
        parts.append(f"**{label}:**\n" + "\n".join(f"- {item}" for item in items))

    return "\n\n".join(parts)
