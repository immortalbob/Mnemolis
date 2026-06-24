from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    """
    All settings grouped by source/feature, not by when they were added —
    found via a deliberate config-completeness audit (after the battle-
    testing and bulletproofing passes) that searched every file in app/
    for hardcoded values a real homelab deployment might genuinely want to
    tune. Some hardcoded values were deliberately left out of this audit's
    additions: LLM max_tokens values (internal sizing for a specific
    prompt, not a real user preference), the 3-disambiguation-candidates
    count in kiwix.py (tightly coupled to the actual prompt wording —
    changing the count without rewriting the prompt would produce
    inconsistent behavior), and home_assistant.py's minute/hour/day
    formatting thresholds (structural facts about time, not deployment
    preferences).
    """

    model_config = ConfigDict(env_file=".env")

    # -------------------------------------------------------------------
    # Kiwix
    # -------------------------------------------------------------------
    kiwix_url: str = "http://kiwix:8080"

    # Results requested per book per search. Higher values give the
    # scoring function more candidates to find the right answer among
    # when common terms are crowded out by brand-name collisions (e.g.
    # "galaxy" returning dozens of Samsung phone articles). Increases
    # Kiwix request size.
    kiwix_search_limit: int = 15

    # Maximum number of books the LLM can select for a single query.
    # Raise this if you want broader multi-book fusion (e.g. querying
    # Python, Raspberry Pi, and Unix Stack Exchange together) at the cost
    # of more concurrent Kiwix requests per search and more candidates to
    # score.
    kiwix_max_books: int = 2

    # How many characters of a fetched article's body to keep before
    # scoring/fusion ever sees it. Found via a deliberate config-
    # completeness audit: every real call site relied on this same
    # hardcoded default, with no override anywhere — distinct from
    # FUSION_MAX_CHARS_PER_SOURCE, which truncates the already-combined
    # multi-source response, not an individual Kiwix article on its own.
    kiwix_article_max_chars: int = 3000

    # The actual, central "should a second book be fused in, or dropped
    # as noise" decision: a second book's best result must score at
    # least this fraction of the leading book's top score to be included
    # in a multi-book fusion response. Found hardcoded via the same
    # audit — documented in the README and wiki as the real mechanism,
    # but previously impossible to tune. Lower this for more aggressive
    # fusion (more books included more often); raise it for more
    # conservative fusion (only fuse when a second book is genuinely
    # competitive).
    kiwix_multi_book_fusion_threshold_pct: float = 0.5

    # -------------------------------------------------------------------
    # FreshRSS
    # -------------------------------------------------------------------
    freshrss_url: str = "http://freshrss"
    freshrss_user: str = ""
    freshrss_api_password: str = ""
    freshrss_max_articles: int = 10

    # -------------------------------------------------------------------
    # SearXNG / Web Search
    # -------------------------------------------------------------------
    searxng_url: str = "http://searxng:8080"

    # How long Mnemolis itself waits for a SearXNG response, separate
    # from SearXNG's own server-side request_timeout setting. Found
    # hardcoded via a deliberate config-completeness audit: this client-
    # side timeout was fixed at 10s regardless of what SearXNG's own
    # config allowed, meaning the documented fix for "Error reaching
    # SearXNG" (raising SearXNG's max_request_timeout to 20s — see The
    # SearXNG Timeout Lesson) wouldn't fully work, since Mnemolis's own
    # client would still cut the connection at 10s first. Set this to
    # match or exceed whatever you've configured on the SearXNG side.
    searxng_request_timeout_seconds: int = 10

    # How many raw, unscored results to pull from each of the (up to two)
    # SearXNG searches before confidence-aware scoring filters them down.
    # This is the scoring pipeline's INPUT budget, distinct from
    # WEB_NEWS_TOP_N below, which caps the OUTPUT after scoring. Raise
    # this if you want scoring to consider a deeper pool of SearXNG's own
    # results per search; lower it to reduce how much raw data gets
    # pulled and scored per query.
    web_news_raw_result_budget: int = 25

    # Web/News confidence-aware fusion — results scoring at or below this
    # threshold are dropped as irrelevant before formatting. Survivors are
    # capped at top_n to bound response size.
    web_news_score_threshold: int = 0
    web_news_top_n: int = 10

    # Minimum query length (in words) for web search query expansion to
    # trigger — below this, a query is assumed simple enough that a
    # second, LLM-generated alternate phrasing wouldn't meaningfully
    # improve results, and isn't worth the extra LLM call. Documented in
    # the README as "3+ words"; found hardcoded with no way to adjust it.
    query_expansion_min_words: int = 3

    # -------------------------------------------------------------------
    # Forecast (Open-Meteo)
    # -------------------------------------------------------------------
    forecast_latitude: float = 0.0
    forecast_longitude: float = 0.0
    forecast_location_name: str = ""
    forecast_timezone: str = "UTC"

    # When to mention precipitation/wind in the summary, and when a
    # snapshot-to-snapshot temperature shift counts as a meaningful
    # "change" — deployment-specific preferences, not algorithm tuning.
    forecast_precip_threshold_pct: int = 20
    forecast_wind_threshold_mph: int = 15
    forecast_temp_change_threshold: float = 5.0

    # -------------------------------------------------------------------
    # Uptime Kuma
    # -------------------------------------------------------------------
    uptime_kuma_url: str = ""
    uptime_kuma_username: str = ""
    uptime_kuma_password: str = ""

    # -------------------------------------------------------------------
    # Home Assistant
    # -------------------------------------------------------------------
    ha_url: str = ""
    ha_token: str = ""

    # Battery level (%) below which a snapshot diff reports "low"
    battery_low_threshold_pct: float = 20.0

    # -------------------------------------------------------------------
    # Snapshot Engine — time-window phrase defaults and job health
    # -------------------------------------------------------------------
    # Used to resolve phrases like "this morning" or "while at work" into
    # hour windows.
    morning_start_hour: int = 6   # "this morning" looks back to this hour, local time
    work_start_hour: int = 9      # "while at work" / "since work" looks back to this hour, local time

    # How many multiples of a job's own expected interval can pass before
    # /health flags it as "stale" rather than "ok" — generous enough by
    # default to absorb normal scheduler jitter without false-alarming on
    # a slightly delayed tick. Found hardcoded via a deliberate config-
    # completeness audit: lower this for tighter alerting on flakier
    # hardware where you want to know sooner; raise it if normal jitter
    # on your own hardware is wider than 3x the expected interval.
    snapshot_stale_grace_multiplier: int = 3

    # -------------------------------------------------------------------
    # LLM backend — for intelligent source and Kiwix book selection
    # -------------------------------------------------------------------
    # Leave LLM_URL blank to disable LLM routing and fall back to keyword
    # matching + Wikipedia.
    llm_url: str = ""
    llm_model: str = "qwen3:8b"
    llm_api_type: str = "ollama"  # "ollama" (native) or "openai" (OpenAI-compatible)

    # -------------------------------------------------------------------
    # Fusion — concurrency, payload size, and timeout limits
    # -------------------------------------------------------------------
    fusion_max_sources: int = 4
    fusion_max_chars_per_source: int = 1500
    fusion_timeout_seconds: int = 15

    # -------------------------------------------------------------------
    # Caching — result cache, routing cache, and per-source TTLs
    # -------------------------------------------------------------------
    # Result cache — max entries before oldest-eviction kicks in.
    # Lower this on memory-constrained hardware (e.g. an N100 with
    # limited RAM).
    cache_max_size: int = 500

    # Routing cache — max entries before oldest-eviction kicks in.
    # Separate from cache_max_size since the routing cache key space is
    # genuinely larger: every unique conditional query, discourse-framing
    # phrase, and disambiguation candidate set gets its own entry, on top
    # of plain source-routing decisions. Found via real usage — this
    # cache had NO size limit at all until this setting was added, unlike
    # the result cache, which could grow unboundedly over sustained
    # real-world usage.
    routing_cache_max_size: int = 1000

    # How long a routing decision (which source, which Kiwix book, which
    # disambiguation candidates) stays cached before the LLM gets asked
    # again. Found hardcoded via a deliberate config-completeness audit —
    # presented as a deliberate, reasoned default in the wiki's Caching
    # page, but previously impossible to actually adjust.
    routing_cache_ttl_seconds: int = 3600

    # Per-source result cache TTLs — found hardcoded via the same audit.
    # Each reflects how stale an answer from that specific source is
    # acceptable to be, by design (offline encyclopedic content barely
    # changes within a day; lights and locks change state constantly).
    # Lower a given source's TTL if your own deployment needs fresher
    # answers from it than the default assumes; raise it to reduce real
    # backend load from repeated identical queries.
    cache_ttl_kiwix_seconds: int = 86400      # 24 hours
    cache_ttl_forecast_seconds: int = 1800    # 30 minutes
    cache_ttl_news_seconds: int = 900         # 15 minutes
    cache_ttl_web_seconds: int = 3600         # 1 hour
    cache_ttl_uptime_seconds: int = 60        # 1 minute
    cache_ttl_ha_seconds: int = 30            # 30 seconds
    cache_ttl_changes_seconds: int = 120      # 2 minutes — changes are near-real-time
    cache_ttl_fusion_seconds: int = 1800      # 30 minutes

    # -------------------------------------------------------------------
    # API key authentication — protects /search and /changes
    # -------------------------------------------------------------------
    # Leave blank to disable auth entirely (default, backward compatible).
    # Comma-separated list of valid keys, e.g. "key1,key2,key3".
    api_keys: str = ""


settings = Settings()
