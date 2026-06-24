"""
Mnemolis Adversarial Self-Testing
Periodically generates structurally-novel queries via combinatorial mutation
over the router's own real ingredient vocabulary, runs them through the real
production pipeline, and flags structural anomalies for human review.

See wiki/Adversarial-Self-Testing.md and the design doc this module
implements for the full rationale. The short version, since it's the single
most load-bearing constraint on everything in this file:

NOTHING HERE EVER JUDGES WHETHER A RESPONSE WAS CORRECT. An LLM-as-judge
approach to this exact task shape (generate a test input AND an expected
answer, then trust the LLM's own judgment) was measured at 6.3% precision in
real research (Liu et al. 2024, arxiv.org/html/2404.10304v1) — 93.7% of
"failures" were the judge's own invented expected-answer being wrong, not
the system under test. This module instead checks Mnemolis's own documented,
stated behavioral guarantees (does a discourse-framing query include kiwix
the way the README says it will; does a query built from N intents produce
something close to N decomposed parts) against what the real pipeline
actually did — a fundamentally different, reliable kind of check than "is
this answer right," requiring no ground truth and no human-replacement
judgment call from a model.

Generation is pure-Python combinatorics over real, already-existing
vocabulary lists (router.INTENT_MAP, router._CONJUNCTIONS,
router._NOSPLIT_PATTERNS, kiwix.DISCOURSE_FRAMING_PATTERNS) plus a small
hardcoded seed corpus of proper-noun pairs and conditional phrases — never
an LLM call per generated query. This mirrors the one part of the AID paper
(the same source above) directly reusable here: generate a script/resource
once, run it many times, rather than paying an LLM call per test case.
"""
import json
import logging
import random
import re
import sqlite3
import time
from datetime import datetime, timezone

from app import router
from app.config import settings
from app.sources import kiwix
from app.sources.fusion import _looks_empty

_LOGGER = logging.getLogger(__name__)

ADVERSARIAL_DB = "/app/data/adversarial_testing.db"


def _connect(db_path: str) -> sqlite3.Connection:
    """Mirrors main.py's _connect() exactly — WAL mode, busy timeout, same
    as every other Mnemolis SQLite database, since this is a genuinely
    separate DB file but should behave identically under concurrent access
    from the scheduler and from GET /adversarial/flagged."""
    con = sqlite3.connect(db_path, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    return con


def init_adversarial_db():
    """Create the adversarial_combinations table if it doesn't exist.
    Mirrors snapshots.init_snapshot_db()'s exact pattern."""
    try:
        con = _connect(ADVERSARIAL_DB)
        con.execute("""
            CREATE TABLE IF NOT EXISTS adversarial_combinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                recipe_name TEXT NOT NULL,
                first_seen_timestamp TEXT NOT NULL,
                times_generated INTEGER NOT NULL DEFAULT 1,
                last_query_text TEXT NOT NULL,
                last_source_used TEXT,
                last_latency_ms INTEGER,
                last_flagged_reason TEXT,
                last_run_timestamp TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_adversarial_flagged
                ON adversarial_combinations (last_flagged_reason)
                WHERE last_flagged_reason IS NOT NULL
        """)
        con.commit()
        con.close()
        _LOGGER.info("Adversarial testing DB initialized")
    except Exception as e:
        _LOGGER.warning("Could not initialize adversarial testing DB: %s", e)


# ---------------------------------------------------------------------------
# Seed vocabulary — section 4.3's "generate a resource once, reuse many
# times" LLM-use boundary. These are hardcoded starting points; periodic
# (weekly, not per-cycle) LLM-assisted expansion of these specific lists is
# the one place an LLM call is worth its cost for this feature — see
# expand_seed_vocabulary() below. Nothing here is itself an LLM call.
# ---------------------------------------------------------------------------

# Real proper-noun pairs — the exact one that found bug 5, plus other
# genuinely capitalized two-entity pairs a person might plausibly ask about
# together. Kept verbatim per the design doc's instruction.
PROPER_NOUN_PAIRS = [
    "Iran and Israel",
    "Mercury and Venus",
    "Python and JavaScript",
    "the Beatles and the Rolling Stones",
]

# Real seed conditions — reused directly from tests/locustfile.py's
# CONDITIONAL_QUERIES / CONDITIONAL_WITH_REMAINDER_QUERIES rather than
# reinvented, per the design doc's explicit instruction not to duplicate
# this list. Verified against the real file: it actually contains FOUR
# conditions, not the three the design doc named — the fourth ("if mercury
# is in retrograde...") is folded in here too, since there's no reason to
# leave a real, already-existing seed on the table.
CONDITIONAL_SEEDS = [
    "if the back door is unlocked",
    "if any services are down",
    "if it is raining",
    "if mercury is in retrograde",
]

# Consequence phrases to pair with conditions — plain text, since Mnemolis
# has no reminder/trigger capability and never acts on these; they only
# exercise detect_conditional()'s parsing of the consequence segment.
CONSEQUENCE_SEEDS = [
    "let me know",
    "let me know right away",
    "remind me to bring an umbrella",
    "I will be careful with communication",
]

# Verb phrases for the proper_noun_plus_pronoun_intent recipe — paired with
# a leading pronoun "I" to reproduce bug 5's exact shape (a proper-noun pair
# immediately followed by a conjunction and the pronoun "I").
PRONOUN_VERB_PHRASES = [
    "I keep getting a weird numpy import error on my raspberry pi",
    "I need to know if any services are down",
    "I was wondering what the deal with sunspots is",
    "I forgot to ask about the weather this weekend",
]

# Generic discourse-bait topics — genuinely encyclopedic things people
# plausibly frame as "everyone's talking about X", used to give the
# discourse_framing_plus_real_keyword recipe a real noun phrase to attach
# the discourse pattern to, rather than letting the pattern run directly
# into an unrelated keyword as one ungrammatical fragment.
_DISCOURSE_TOPICS = [
    "sunspots", "quantum computing", "the James Webb telescope",
    "that volcano in Iceland", "the new tariffs", "black holes",
]


def _stable_hash_ingredients(*parts: str) -> tuple:
    """Build the dedup key for a combination — the tuple of ingredient
    identifiers actually used, not the literal generated string. Sorted so
    ingredient order never produces a spurious distinct fingerprint."""
    return tuple(sorted(parts))


# ---------------------------------------------------------------------------
# Recipes — each one pure Python, no LLM call. Each returns
# (query_text, recipe_name, fingerprint).
# ---------------------------------------------------------------------------

def _recipe_proper_noun_plus_pronoun_intent(rng: random.Random) -> tuple[str, str, tuple]:
    """'<pair>, <conjunction> I <verb phrase>' — the exact bug-5 shape."""
    pair = rng.choice(PROPER_NOUN_PAIRS)
    conj = rng.choice(router._CONJUNCTIONS)
    verb_phrase = rng.choice(PRONOUN_VERB_PHRASES)
    query = f"also whats happening with {pair},{conj}{verb_phrase}"
    fingerprint = _stable_hash_ingredients("proper_noun_plus_pronoun_intent", pair, conj, verb_phrase)
    return query, "proper_noun_plus_pronoun_intent", fingerprint


def _recipe_multi_intent_chain(rng: random.Random) -> tuple[str, str, tuple]:
    """3-5 independent intents joined by DIFFERENT conjunctions, drawn from
    DIFFERENT INTENT_MAP sources each time."""
    sources = list(router.INTENT_MAP.keys())
    n = rng.randint(3, min(5, len(sources)))
    chosen_sources = rng.sample(sources, n)
    phrases = [rng.choice(router.INTENT_MAP[s]) for s in chosen_sources]
    conjunctions = rng.sample(router._CONJUNCTIONS, min(n - 1, len(router._CONJUNCTIONS)))
    # If we need more conjunctions than distinct ones exist, allow repeats
    # for the remainder rather than crashing on a short sample.
    while len(conjunctions) < n - 1:
        conjunctions.append(rng.choice(router._CONJUNCTIONS))

    query_parts = [phrases[0]]
    for i in range(1, n):
        query_parts.append(conjunctions[i - 1])
        query_parts.append(phrases[i])
    query = "".join(query_parts)
    fingerprint = _stable_hash_ingredients(
        "multi_intent_chain", *chosen_sources, *conjunctions
    )
    return query, "multi_intent_chain", fingerprint


def _recipe_conditional_with_remainder(rng: random.Random) -> tuple[str, str, tuple]:
    """'if <condition>, <consequence>, <conjunction> <unrelated second
    intent>' — novel condition/remainder combinations, not the same 2 fixed
    strings tests/locustfile.py already covers."""
    condition = rng.choice(CONDITIONAL_SEEDS)
    consequence = rng.choice(CONSEQUENCE_SEEDS)
    remainder_source = rng.choice(list(router.INTENT_MAP.keys()))
    remainder_phrase = rng.choice(router.INTENT_MAP[remainder_source])
    conj = rng.choice(router._CONJUNCTIONS)
    query = f"{condition}, {consequence},{conj}{remainder_phrase}"
    fingerprint = _stable_hash_ingredients(
        "conditional_with_remainder", condition, consequence, remainder_source, conj
    )
    return query, "conditional_with_remainder", fingerprint


def _recipe_nosplit_adjacent_to_real_conjunction(rng: random.Random) -> tuple[str, str, tuple]:
    """A _NOSPLIT_PATTERNS phrase placed adjacent to a DIFFERENT, unrelated
    real conjunction elsewhere in the query — tests whether the nosplit
    veto is scoped correctly (per-occurrence) rather than vetoing the whole
    query (the real, found, and fixed global-veto bug)."""
    nosplit_phrase = rng.choice(router._NOSPLIT_PATTERNS)
    pair = rng.choice(PROPER_NOUN_PAIRS)
    other_source = rng.choice(list(router.INTENT_MAP.keys()))
    other_phrase = rng.choice(router.INTENT_MAP[other_source])
    conj = rng.choice(router._CONJUNCTIONS)
    query = f"{nosplit_phrase} {pair},{conj}{other_phrase}"
    fingerprint = _stable_hash_ingredients(
        "nosplit_adjacent_to_real_conjunction", nosplit_phrase, pair, other_source, conj
    )
    return query, "nosplit_adjacent_to_real_conjunction", fingerprint


def _recipe_discourse_framing_plus_real_keyword(rng: random.Random) -> tuple[str, str, tuple]:
    """A DISCOURSE_FRAMING_PATTERNS phrase immediately followed by a
    DIFFERENT source's INTENT_MAP keyword — tests whether the
    discourse-escalation bias still adds kiwix even when a clean keyword
    match for a different source exists.

    A topic noun is inserted between the discourse phrase and the
    keyword (e.g. "everyone keeps talking about sunspots, and also
    forecast change") so the keyword genuinely reads as its own clean,
    separate clause rather than running directly into the discourse
    phrase as a single ungrammatical fragment — a run-on like "everyone
    keeps talking going to be hot" wouldn't actually test the thing this
    recipe is meant to test, since a router failing on it could just as
    easily mean "nonsense input handled reasonably" as a real bug.
    """
    discourse_phrase = rng.choice(kiwix.DISCOURSE_FRAMING_PATTERNS)
    topic = rng.choice(_DISCOURSE_TOPICS)
    other_source = rng.choice(list(router.INTENT_MAP.keys()))
    other_phrase = rng.choice(router.INTENT_MAP[other_source])
    conj = rng.choice(router._CONJUNCTIONS)
    query = f"{discourse_phrase} {topic},{conj}{other_phrase}"
    fingerprint = _stable_hash_ingredients(
        "discourse_framing_plus_real_keyword", discourse_phrase, topic, other_source, conj
    )
    return query, "discourse_framing_plus_real_keyword", fingerprint


def _recipe_nested_proper_noun_pairs(rng: random.Random) -> tuple[str, str, tuple]:
    """Two distinct proper-noun pairs joined by a conjunction, with a third
    real intent after — stress-tests whether the per-occurrence proper-
    noun-pair guard protects BOTH pairs independently."""
    pair_a, pair_b = rng.sample(PROPER_NOUN_PAIRS, 2)
    inner_conj = rng.choice(router._CONJUNCTIONS)
    outer_conj = rng.choice(router._CONJUNCTIONS)
    third_source = rng.choice(list(router.INTENT_MAP.keys()))
    third_phrase = rng.choice(router.INTENT_MAP[third_source])
    query = f"whats the deal with {pair_a}{inner_conj}{pair_b},{outer_conj}{third_phrase}"
    fingerprint = _stable_hash_ingredients(
        "nested_proper_noun_pairs", pair_a, pair_b, inner_conj, outer_conj, third_source
    )
    return query, "nested_proper_noun_pairs", fingerprint


def _recipe_no_intent_fallthrough(rng: random.Random) -> tuple[str, str, tuple]:
    """A query with no recognized INTENT_MAP keyword at all — per section
    8's flagged sub-question, this is itself a real, valid test (does it
    correctly fall through to Kiwix/LLM-assisted routing?) and should be a
    named recipe rather than an accidental gap, since kiwix/fusion aren't
    themselves INTENT_MAP keys."""
    topics = [
        "molybdenum", "the history of the printing press", "tectonic plates",
        "how transformers work in machine learning", "the Roman aqueducts",
    ]
    topic = rng.choice(topics)
    query = f"what is the deal with {topic}"
    fingerprint = _stable_hash_ingredients("no_intent_fallthrough", topic)
    return query, "no_intent_fallthrough", fingerprint


# Recipe registry — name -> generator function. Used both for generation
# and for per-recipe latency-history lookups in _check_latency_outlier().
RECIPES = {
    "proper_noun_plus_pronoun_intent": _recipe_proper_noun_plus_pronoun_intent,
    "multi_intent_chain": _recipe_multi_intent_chain,
    "conditional_with_remainder": _recipe_conditional_with_remainder,
    "nosplit_adjacent_to_real_conjunction": _recipe_nosplit_adjacent_to_real_conjunction,
    "discourse_framing_plus_real_keyword": _recipe_discourse_framing_plus_real_keyword,
    "nested_proper_noun_pairs": _recipe_nested_proper_noun_pairs,
    "no_intent_fallthrough": _recipe_no_intent_fallthrough,
}


def _already_tried(fingerprint: tuple) -> bool:
    """Check whether this fingerprint has already been recorded."""
    try:
        con = _connect(ADVERSARIAL_DB)
        row = con.execute(
            "SELECT 1 FROM adversarial_combinations WHERE fingerprint = ?",
            (json.dumps(fingerprint),)
        ).fetchone()
        con.close()
        return row is not None
    except Exception as e:
        _LOGGER.warning("Could not check fingerprint history: %s", e)
        return False


def generate_adversarial_query(rng: random.Random | None = None) -> tuple[str, dict]:
    """Generate one adversarial query, biasing toward fingerprints never
    seen before per section 5's dedup strategy.

    Returns (query_text, metadata) where metadata includes recipe_name,
    fingerprint (as a JSON string, the actual dedup key stored), and the
    raw ingredient tuple for logging/debugging.
    """
    if rng is None:
        rng = random.Random()

    recipe_names = list(RECIPES.keys())
    rng.shuffle(recipe_names)

    # Try each recipe (in shuffled order) up to a few times each, biasing
    # toward a never-seen fingerprint before falling back to whatever was
    # generated last — guarantees termination without an unbounded loop,
    # since the seed vocabulary is finite and combinations can run out.
    attempts_per_recipe = 5
    fallback: tuple[str, str, tuple] | None = None

    for recipe_name in recipe_names:
        recipe_fn = RECIPES[recipe_name]
        for _ in range(attempts_per_recipe):
            query, name, fingerprint = recipe_fn(rng)
            fallback = (query, name, fingerprint)
            if not _already_tried(fingerprint):
                return query, {
                    "recipe_name": name,
                    "fingerprint": json.dumps(fingerprint),
                    "ingredients": list(fingerprint),
                    "novel": True,
                }

    # Every recipe's sampled attempts were already-seen fingerprints —
    # genuinely fine per section 5 ("deprioritize", not "forbid"); return
    # the last one generated rather than failing the whole cycle.
    query, name, fingerprint = fallback
    return query, {
        "recipe_name": name,
        "fingerprint": json.dumps(fingerprint),
        "ingredients": list(fingerprint),
        "novel": False,
    }


# ---------------------------------------------------------------------------
# Section 6 — structural anomaly detection. No correctness judgment, ever.
# Every check here verifies a documented, stated Mnemolis behavioral
# guarantee against what actually happened — never "is this answer right."
# ---------------------------------------------------------------------------

_HEADER_PATTERN = re.compile(r"\[[A-Z0-9_ ]+ — [A-Z0-9_ ,'/]+\]")


def _check_crash(result: str, error: str | None) -> str | None:
    """Highest priority: a literal crash. The only category here that
    genuinely doesn't need human judgment at all."""
    if error:
        return f"crash: {error[:200]}"
    if result and "Traceback (most recent call last)" in result:
        return "crash: raw traceback in result body"
    return None


def _check_source_mismatch(recipe_name: str, ingredients: list, source_used: str) -> str | None:
    """source_used doesn't match any source whose INTENT_MAP keywords or
    recipe-intended source appear in the generated query at all."""
    intended_sources = {
        ing for ing in ingredients if ing in router.INTENT_MAP
    }
    if not intended_sources:
        return None
    if source_used not in intended_sources and source_used != "fusion":
        return f"source_mismatch: intended {sorted(intended_sources)}, got '{source_used}'"
    return None


def _check_multi_intent_part_count(recipe_name: str, ingredients: list, result: str) -> str | None:
    """A multi_intent_chain query with N intended intents resolved to a
    header count significantly different from N — the same signal that
    caught the real proper-noun-pair bug 5."""
    if recipe_name != "multi_intent_chain":
        return None
    intended_sources = [ing for ing in ingredients if ing in router.INTENT_MAP]
    n_intended = len(intended_sources)
    n_headers = len(_HEADER_PATTERN.findall(result or ""))
    # A single-source result legitimately has zero headers (no attribution
    # needed when nothing was merged) — only flag a REAL discrepancy once
    # more than one source was actually expected to appear.
    if n_intended >= 2 and n_headers > 0 and abs(n_headers - n_intended) >= 2:
        return f"part_count_mismatch: intended {n_intended} intents, found {n_headers} headers"
    return None


def _check_discourse_framing_dropped_kiwix(recipe_name: str, result: str, source_used: str) -> str | None:
    """A discourse_framing_plus_real_keyword query did NOT result in kiwix
    being part of the chosen source(s) — direct, mechanical check against
    the documented discourse-framing bias's own stated guarantee.

    Only trusts source_used and the structural "[KIWIX — ..." header
    marker fusion.py actually emits — never a freeform substring search
    for the word "kiwix" anywhere in the response body. A naive substring
    check incorrectly passes a result that explicitly states kiwix was
    NOT used (e.g. "plain web result, no kiwix involved" contains the
    literal substring "kiwix" while describing the exact failure this
    check exists to catch) — found via a real, failing unit test, not a
    hypothetical concern.
    """
    if recipe_name != "discourse_framing_plus_real_keyword":
        return None
    if source_used == "kiwix":
        return None
    if "[KIWIX —" in (result or ""):
        return None
    return f"discourse_framing_dropped_kiwix: source_used='{source_used}'"


def _check_conditional_remainder_sections(recipe_name: str, result: str) -> str | None:
    """A conditional_with_remainder query's response doesn't contain two
    distinct, separately-headered sections."""
    if recipe_name != "conditional_with_remainder":
        return None
    n_headers = len(_HEADER_PATTERN.findall(result or ""))
    if n_headers < 1:
        return "conditional_remainder_missing_sections: no [SOURCE — LABEL] headers found"
    return None


def _check_unexpected_empty(result: str) -> str | None:
    """The response contains one of fusion._looks_empty()'s own canonical
    phrases when the generated query was specifically constructed to have
    a real, answerable intent."""
    if _looks_empty(result or ""):
        return "unexpected_empty: result matched a known empty/error phrase"
    return None


def _check_latency_outlier(recipe_name: str, latency_ms: int) -> str | None:
    """A generated query taking meaningfully longer than the same recipe's
    own historical p95 — independent of content correctness."""
    try:
        con = _connect(ADVERSARIAL_DB)
        rows = con.execute(
            "SELECT last_latency_ms FROM adversarial_combinations "
            "WHERE recipe_name = ? AND last_latency_ms IS NOT NULL "
            "ORDER BY id DESC LIMIT 50",
            (recipe_name,)
        ).fetchall()
        con.close()
    except Exception:
        return None

    samples = [r[0] for r in rows if r[0] is not None]
    if len(samples) < 10:
        # Not enough history yet to call anything an outlier — this is a
        # genuine "not yet decidable" state, not a clean pass.
        return None
    samples_sorted = sorted(samples)
    p95_index = max(0, int(len(samples_sorted) * 0.95) - 1)
    p95 = samples_sorted[p95_index]
    if latency_ms > p95 * 1.5 and latency_ms > 1000:
        return f"latency_outlier: {latency_ms}ms vs recipe p95 of {p95}ms"
    return None


def _detect_anomalies(
    recipe_name: str,
    ingredients: list,
    query: str,
    result: str,
    source_used: str,
    error: str | None,
    latency_ms: int,
) -> str | None:
    """Run every section-6 check in priority order, returning the first
    (highest-priority) match. Crash detection always runs first since it's
    the one category that needs no judgment at all."""
    checks = [
        lambda: _check_crash(result, error),
        lambda: _check_source_mismatch(recipe_name, ingredients, source_used),
        lambda: _check_multi_intent_part_count(recipe_name, ingredients, result),
        lambda: _check_discourse_framing_dropped_kiwix(recipe_name, result, source_used),
        lambda: _check_conditional_remainder_sections(recipe_name, result),
        lambda: _check_unexpected_empty(result),
        lambda: _check_latency_outlier(recipe_name, latency_ms),
    ]
    for check in checks:
        reason = check()
        if reason:
            return reason
    return None


# ---------------------------------------------------------------------------
# Cycle execution and persistence
# ---------------------------------------------------------------------------

def _record_result(
    fingerprint_json: str,
    recipe_name: str,
    query: str,
    source_used: str | None,
    latency_ms: int | None,
    flagged_reason: str | None,
):
    """Upsert one combination's result. INSERT on first sighting, UPDATE
    (incrementing times_generated, overwriting the 'last_*' columns) on
    every subsequent sighting of the same fingerprint — per section 8's
    answer to open question 4, flagged reasons are never auto-cleared by
    a later clean run; a NEW clean run simply overwrites last_flagged_reason
    to NULL only if this run found nothing, which IS the literal behavior
    the design doc's open question leaves as a deliberate, documented
    choice in the opposite direction (no auto-resolve) — so a flag here is
    intentionally sticky only at the human-review layer (GET
    /adversarial/flagged should be reviewed/dismissed by a person), not
    artificially preserved by withholding the latest factual result.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        con = _connect(ADVERSARIAL_DB)
        existing = con.execute(
            "SELECT times_generated FROM adversarial_combinations WHERE fingerprint = ?",
            (fingerprint_json,)
        ).fetchone()
        if existing is None:
            con.execute(
                """INSERT INTO adversarial_combinations
                   (fingerprint, recipe_name, first_seen_timestamp, times_generated,
                    last_query_text, last_source_used, last_latency_ms,
                    last_flagged_reason, last_run_timestamp)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (fingerprint_json, recipe_name, now, query, source_used, latency_ms,
                 flagged_reason, now)
            )
        else:
            con.execute(
                """UPDATE adversarial_combinations
                   SET times_generated = times_generated + 1,
                       last_query_text = ?,
                       last_source_used = ?,
                       last_latency_ms = ?,
                       last_flagged_reason = ?,
                       last_run_timestamp = ?
                   WHERE fingerprint = ?""",
                (query, source_used, latency_ms, flagged_reason, now, fingerprint_json)
            )
        con.commit()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not record adversarial test result: %s", e)


def run_adversarial_test_cycle():
    """The scheduled job body. Generates a small batch of queries, routes
    each through the real route_with_source() pipeline exactly the way a
    real user query would, checks for structural anomalies, and persists
    results. Never touches cache.json, routing_cache.json, query_log.db,
    or any real user-facing state — writes only to adversarial_testing.db.
    """
    rng = random.Random()
    batch_size = settings.adversarial_test_batch_size

    for _ in range(batch_size):
        try:
            query, meta = generate_adversarial_query(rng)
            recipe_name = meta["recipe_name"]
            fingerprint_json = meta["fingerprint"]
            ingredients = meta["ingredients"]

            start = time.monotonic()
            result = None
            source_used = None
            error = None
            try:
                result, source_used = router.route_with_source(query, source="auto")
            except Exception as e:
                error = str(e)
            latency_ms = int((time.monotonic() - start) * 1000)

            flagged_reason = _detect_anomalies(
                recipe_name, ingredients, query, result or "", source_used or "", error, latency_ms
            )

            _record_result(fingerprint_json, recipe_name, query, source_used, latency_ms, flagged_reason)

            if flagged_reason:
                _LOGGER.warning(
                    "Adversarial test flagged: recipe=%s query=%r reason=%s",
                    recipe_name, query[:100], flagged_reason
                )
        except Exception as e:
            # A failure generating or processing ONE query in the batch
            # should never abort the whole cycle — each iteration is
            # independent, same as every snapshot job's own per-source
            # try/except convention.
            _LOGGER.warning("Adversarial test cycle iteration failed: %s", e)


def get_adversarial_test_summary() -> dict:
    """Summary for /health. Mirrors get_snapshot_job_health()'s naming
    convention and overall shape — status, last_run, and counts that make
    growth and review backlog visible without digging through logs."""
    try:
        con = _connect(ADVERSARIAL_DB)
        total_row = con.execute("SELECT COUNT(*) FROM adversarial_combinations").fetchone()
        flagged_row = con.execute(
            "SELECT COUNT(*) FROM adversarial_combinations WHERE last_flagged_reason IS NOT NULL"
        ).fetchone()
        last_run_row = con.execute(
            "SELECT MAX(last_run_timestamp) FROM adversarial_combinations"
        ).fetchone()
        con.close()
    except Exception as e:
        return {"status": "unknown", "error": str(e)}

    total = total_row[0] if total_row else 0
    flagged = flagged_row[0] if flagged_row else 0
    last_run = last_run_row[0] if last_run_row else None

    if last_run is None:
        return {
            "status": "never_ran",
            "total_combinations_tried": 0,
            "flagged_for_review": 0,
        }

    try:
        last_run_dt = datetime.strptime(last_run, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        minutes_since = (datetime.now(timezone.utc) - last_run_dt).total_seconds() / 60
    except Exception:
        minutes_since = None

    interval = settings.adversarial_test_interval_minutes
    is_stale = (
        minutes_since is not None
        and minutes_since > interval * settings.snapshot_stale_grace_multiplier
    )

    return {
        "status": "stale" if is_stale else "ok",
        "last_run": last_run,
        "minutes_since_last_run": round(minutes_since, 1) if minutes_since is not None else None,
        "total_combinations_tried": total,
        "flagged_for_review": flagged,
    }


def get_flagged_combinations(limit: int = 50) -> list[dict]:
    """Return flagged rows for GET /adversarial/flagged — the literal
    'logs results for periodic review' mechanism the roadmap entry called
    for, now with a real, specific, queryable home."""
    try:
        con = _connect(ADVERSARIAL_DB)
        rows = con.execute(
            """SELECT fingerprint, recipe_name, first_seen_timestamp, times_generated,
                      last_query_text, last_source_used, last_latency_ms,
                      last_flagged_reason, last_run_timestamp
               FROM adversarial_combinations
               WHERE last_flagged_reason IS NOT NULL
               ORDER BY last_run_timestamp DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        con.close()
    except Exception as e:
        _LOGGER.warning("Could not fetch flagged adversarial combinations: %s", e)
        return []

    columns = [
        "fingerprint", "recipe_name", "first_seen_timestamp", "times_generated",
        "last_query_text", "last_source_used", "last_latency_ms",
        "last_flagged_reason", "last_run_timestamp",
    ]
    return [dict(zip(columns, row)) for row in rows]
