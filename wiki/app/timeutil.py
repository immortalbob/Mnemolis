"""
Mnemolis shared timezone conversion utility.

Exists because of a real, previously-uncatalogued gap found during research
for two separate, not-yet-built design docs (Predictive Pre-Fetching with
Confidence Calibration, Ambient Intent Disambiguation Through Context): every
timestamp this project writes to a database — query_log.db (app/main.py's
_log_query()), snapshots.db, adversarial_testing.db, temporal_patterns.db —
is hardcoded UTC via time.gmtime()/datetime.now(timezone.utc), confirmed
directly across every one of those call sites. Meanwhile, the project's own
EXISTING "local time" logic — app/router.py's _hours_since(), which resolves
phrases like "this morning" and "while at work" — uses datetime.now() (naive
local time), sourced entirely from the container's OS-level TZ environment
variable (documented in README.md's "Timezone configuration" section), with
no reference to anything in app/config.py at all.

These are two different, previously-unreconciled mechanisms for "what time is
it for this person" already coexisting in this codebase. Any feature that
needs to bucket a STORED, UTC timestamp by local hour-of-day or day-of-week —
which both of the design docs above need to do, for "did you ask this every
morning at 7am"-style pattern mining — was about to either invent its own,
third, independent timezone-handling approach, or (more likely, and far worse)
silently bucket by raw UTC hour-of-day, which is only correct for a deployment
physically in the UTC timezone. For Mnemolis's own real reference deployment
(Kingman, AZ — America/Phoenix, UTC-7, no DST), that mistake would silently
shift every single time-of-day bucket by exactly 7 hours, forever, with no
error or warning anywhere — exactly the class of bug this project's own
bulletproofing-pass culture exists to catch before it ships, not after.

This module is the one, single, shared answer to that gap: settings.local_timezone
(app/config.py) names the SAME timezone concept _hours_since() already
implicitly depends on via the OS's TZ variable — defaulting to read that exact
same environment variable, so a deployment that has already correctly set TZ
per the README gets this conversion capability for free, with zero new
configuration burden. A deployment that explicitly wants this conversion to
use a DIFFERENT zone than whatever TZ happens to be set to can still override
it directly via LOCAL_TIMEZONE, the normal pydantic-settings precedence rule
(confirmed directly: an explicit env var always wins over a Python-level
default expression).

Every future feature that needs to bucket a UTC timestamp by local time
(Predictive Pre-Fetching's mining job, a future query-shape-clustering module
shared with Self-Healing Source Selection, Ambient Intent Disambiguation's
own time-of-day signal) should import from here, not reimplement this.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings

_LOGGER = logging.getLogger(__name__)

# The exact timestamp format every database in this project already writes —
# confirmed identical across app/main.py's _log_query(), app/snapshots.py,
# and app/temporal_patterns.py's own _fmt_ts()/_parse_ts(). Defined once here
# rather than re-typed at every call site that needs it.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _resolve_zone() -> ZoneInfo:
    """Resolve settings.local_timezone into a real ZoneInfo, falling back to
    UTC on any invalid/unrecognized zone name rather than crashing.

    A typo in a deployment's TZ (or an explicit LOCAL_TIMEZONE override) is a
    genuinely real, plausible mistake — the same class of risk
    morning_start_hour's own "% 24" defensive fix (app/router.py's
    _hours_since()) already guards against for a different setting. Falling
    back to UTC here means a misconfigured zone degrades to "every bucket is
    computed in UTC" (the same default behavior every part of this project
    already had before this module existed), not a hard crash — confirmed
    directly: zoneinfo.ZoneInfo() raises ZoneInfoNotFoundError, a real,
    catchable exception, for any unrecognized key.

    Not cached across calls deliberately — settings.local_timezone is a
    single, fixed value for the life of one running process (set once at
    container startup, the same as every other env-var-sourced setting in
    this project), so re-resolving it per call costs a cheap, already-fast
    stdlib lookup rather than meaningfully more than that, and avoids a
    separate cache-invalidation question entirely for a value that's already
    documented to come from a tested-instance-friendly Settings field — the
    same simplicity-over-premature-optimization judgment call this project
    already made for _hours_since() itself, which similarly recomputes
    datetime.now() on every call rather than caching it.
    """
    try:
        return ZoneInfo(settings.local_timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        _LOGGER.warning(
            "Invalid LOCAL_TIMEZONE/TZ value '%s' (%s) — falling back to UTC. "
            "Check for a typo; valid examples: 'America/New_York', 'Europe/London'.",
            settings.local_timezone, e,
        )
        return ZoneInfo("UTC")


def utc_string_to_local(timestamp: str) -> datetime:
    """Convert a stored UTC timestamp string (this project's universal
    TIMESTAMP_FORMAT) into a real, timezone-aware local datetime.

    This is the one function every future time-of-day/day-of-week bucketing
    pass in this project should call — never datetime.strptime() directly
    against a stored timestamp followed by naive .hour/.weekday() access,
    which would silently bucket by UTC hour-of-day rather than the person's
    own actual local hour-of-day. Confirmed via this module's own dedicated
    test suite that a known UTC timestamp at a known real-world offset
    produces the correct local hour — the single most important test this
    utility has, since every consumer's correctness depends entirely on this
    one conversion being right.

    Raises ValueError if `timestamp` doesn't match TIMESTAMP_FORMAT — this is
    deliberately NOT swallowed the way an invalid timezone name is (see
    _resolve_zone()): a malformed timestamp string is a real bug in whatever
    wrote it, not a plausible deployment misconfiguration, and should fail
    loudly at the call site rather than silently producing a wrong bucket
    under a caught exception. Callers iterating over many real rows from a
    table this project already writes (query_log, snapshots, etc.) should
    not normally hit this — every stored row was written via TIMESTAMP_FORMAT
    in the first place — but a caller reading attacker-controlled or
    otherwise unverified input should catch this explicitly at its own call
    site, not rely on this function to do it silently.
    """
    naive_utc = datetime.strptime(timestamp, TIMESTAMP_FORMAT)
    aware_utc = naive_utc.replace(tzinfo=timezone.utc)
    return aware_utc.astimezone(_resolve_zone())


def local_hour_bucket(timestamp: str, bucket_minutes: int = 30) -> int:
    """Convert a stored UTC timestamp string into a local-time bucket index
    for the day, where bucket 0 is local midnight and each bucket spans
    `bucket_minutes` minutes (default 30, matching the granularity
    Predictive Pre-Fetching's own design doc proposed for its own "5 minutes
    before the expected time" framing without over-fitting to single-minute
    noise).

    Returns an int in [0, (1440 // bucket_minutes) - 1]. Deliberately
    returns a plain bucket index rather than an (hour, minute) pair — every
    real consumer of this (pattern mining, clustering) wants a single,
    directly-comparable/groupable key, not a tuple it would just immediately
    flatten back down anyway.
    """
    local_dt = utc_string_to_local(timestamp)
    minutes_since_midnight = local_dt.hour * 60 + local_dt.minute
    return minutes_since_midnight // bucket_minutes


def local_day_of_week(timestamp: str) -> int:
    """Convert a stored UTC timestamp string into a local-time day-of-week
    index (Monday=0 ... Sunday=6, matching Python's own datetime.weekday()
    convention directly, rather than inventing a different numbering this
    project would then need to remember and document separately).
    """
    return utc_string_to_local(timestamp).weekday()
