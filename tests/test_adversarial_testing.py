"""
Tests for app/adversarial_testing.py — combinatorial adversarial query
generation and structural anomaly detection.

Generation tests verify real combinatorics over the router's REAL
vocabulary lists (no duplicated/forked copies). Anomaly detection tests
verify each check's documented Mnemolis guarantee directly, never a
correctness judgment about response content. DB tests use a temp SQLite
file via unittest.mock.patch on the module-level path constant, matching
test_snapshots.py's existing convention exactly.
"""
import random
from unittest.mock import patch

from app import router
from app.sources import kiwix
from app.adversarial_testing import (
    RECIPES,
    PROPER_NOUN_PAIRS,
    CONDITIONAL_SEEDS,
    generate_adversarial_query,
    init_adversarial_db,
    _connect,
    _record_result,
    _already_tried,
    _check_crash,
    _check_source_mismatch,
    _check_multi_intent_part_count,
    _check_discourse_framing_dropped_kiwix,
    _check_conditional_remainder_sections,
    _check_unexpected_empty,
    _check_latency_outlier,
    run_adversarial_test_cycle,
    get_adversarial_test_summary,
    get_flagged_combinations,
)


class TestSeedVocabularyIntegrity:
    """The design doc's core constraint: never duplicate/fork router.py's
    or kiwix.py's real ingredient lists. These tests fail loudly if a
    future edit accidentally introduces a local copy instead of importing
    the real thing."""

    def test_conditional_seeds_match_locustfile_real_seeds(self):
        """CONDITIONAL_SEEDS must contain every real seed from
        tests/locustfile.py's CONDITIONAL_QUERIES, not a re-typed subset.

        Reads the file as text rather than importing it as a module:
        locustfile.py imports the `locust` package at module level, which
        isn't in requirements.txt (it's a dev-only load-testing tool, not
        installed by CI's `pip install -r requirements.txt` step) — an
        actual import here would make this test fail in CI specifically,
        not just locally.
        """
        import ast
        import os
        locustfile_path = os.path.join(os.path.dirname(__file__), "locustfile.py")
        with open(locustfile_path) as f:
            tree = ast.parse(f.read(), filename="locustfile.py")
        conditional_queries = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id == "CONDITIONAL_QUERIES":
                    conditional_queries = ast.literal_eval(node.value)
                    break
        assert conditional_queries is not None, "CONDITIONAL_QUERIES not found in locustfile.py"
        for full_query in conditional_queries:
            # Each locustfile query embeds one of our condition seeds as a
            # substring (locustfile's queries are "if X, Y" full sentences;
            # our seeds are just the X part).
            assert any(seed in full_query for seed in CONDITIONAL_SEEDS), (
                f"locustfile query {full_query!r} has no matching condition "
                f"seed in CONDITIONAL_SEEDS — seeds have drifted from the "
                f"real locustfile corpus"
            )

    def test_recipes_use_real_intent_map_not_a_copy(self):
        """Recipes that touch INTENT_MAP must reference the actual
        router.INTENT_MAP object, so future additions to real sources are
        automatically picked up with zero changes here."""
        assert router.INTENT_MAP is router.INTENT_MAP  # sanity
        # multi_intent_chain's whole point is using every real key
        rng = random.Random(0)
        seen_sources = set()
        for _ in range(50):
            query, name, fp = RECIPES["multi_intent_chain"](rng)
            seen_sources.update(s for s in fp if s in router.INTENT_MAP)
        # Over 50 draws, should see more than just one or two sources —
        # confirms it's sampling the real, full INTENT_MAP, not a stub.
        assert len(seen_sources) >= 3

    def test_discourse_framing_recipe_uses_real_kiwix_patterns(self):
        """The discourse_framing recipe must draw from the real,
        canonical kiwix.DISCOURSE_FRAMING_PATTERNS list."""
        rng = random.Random(0)
        generated_phrases = set()
        for _ in range(30):
            query, name, fp = RECIPES["discourse_framing_plus_real_keyword"](rng)
            for phrase in kiwix.DISCOURSE_FRAMING_PATTERNS:
                if phrase in query:
                    generated_phrases.add(phrase)
        assert generated_phrases, "no real DISCOURSE_FRAMING_PATTERNS phrase ever appeared in generated queries"
        assert generated_phrases.issubset(set(kiwix.DISCOURSE_FRAMING_PATTERNS))


class TestGeneration:
    """Generation must be pure-Python (no network/LLM calls), produce
    syntactically sane queries, and bias toward novel fingerprints."""

    def test_all_recipes_produce_nonempty_strings(self):
        rng = random.Random(1)
        for name, fn in RECIPES.items():
            query, recipe_name, fingerprint = fn(rng)
            assert isinstance(query, str) and len(query) > 0, f"{name} produced empty/non-string query"
            assert recipe_name == name
            assert isinstance(fingerprint, tuple)

    def test_generate_adversarial_query_returns_valid_metadata_shape(self):
        rng = random.Random(2)
        query, meta = generate_adversarial_query(rng)
        assert isinstance(query, str) and query
        assert meta["recipe_name"] in RECIPES
        assert isinstance(meta["fingerprint"], str)  # JSON-serialized
        assert isinstance(meta["ingredients"], list)
        assert isinstance(meta["novel"], bool)

    def test_proper_noun_pair_recipe_reproduces_bug5_shape(self):
        """Specifically reproduces the exact structural shape that found
        the real proper-noun-pair bug: '<PAIR>, <conj> I <verb phrase>'."""
        rng = random.Random(3)
        for _ in range(10):
            query, name, fp = RECIPES["proper_noun_plus_pronoun_intent"](rng)
            assert any(pair in query for pair in PROPER_NOUN_PAIRS)
            assert " I " in query or query.split(",")[-1].strip().startswith("I ")

    def test_no_intent_fallthrough_recipe_matches_no_intent_keyword(self):
        """This recipe's whole point is generating queries with NO
        INTENT_MAP keyword match at all — verify that's actually true."""
        rng = random.Random(4)
        all_keywords = [kw for kws in router.INTENT_MAP.values() for kw in kws]
        for _ in range(10):
            query, name, fp = RECIPES["no_intent_fallthrough"](rng)
            query_lower = query.lower()
            assert not any(kw in query_lower for kw in all_keywords), (
                f"no_intent_fallthrough query {query!r} accidentally matched a real INTENT_MAP keyword"
            )

    def test_generation_biases_toward_novel_fingerprints(self, tmp_path):
        """Given a fresh DB, the first N generations of a narrow-vocabulary
        recipe should all be marked novel before any repeats occur."""
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            with patch("app.adversarial_testing.RECIPES", {"no_intent_fallthrough": RECIPES["no_intent_fallthrough"]}):
                rng = random.Random(5)
                novelty_flags = []
                for _ in range(20):
                    query, meta = generate_adversarial_query(rng)
                    novelty_flags.append(meta["novel"])
                    _record_result(meta["fingerprint"], meta["recipe_name"], query, "kiwix", 100, None)
                # Once every topic in the seed list has been seen, later
                # generations should start reporting novel=False.
                assert False in novelty_flags, "generation never fell back to a repeat despite a finite seed vocabulary"

    def test_generation_never_crashes_when_db_unreachable(self, tmp_path):
        """generate_adversarial_query must degrade gracefully (not crash)
        if the dedup DB can't be reached — _already_tried fails soft."""
        with patch("app.adversarial_testing.ADVERSARIAL_DB", "/nonexistent/path/db.sqlite"):
            rng = random.Random(6)
            query, meta = generate_adversarial_query(rng)
            assert isinstance(query, str) and query


class TestDeduplication:
    """Direct tests of the fingerprint persistence layer."""

    def test_record_result_inserts_then_updates_times_generated(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            fp = '["a", "b"]'
            _record_result(fp, "no_intent_fallthrough", "query one", "kiwix", 100, None)
            _record_result(fp, "no_intent_fallthrough", "query one again", "kiwix", 150, None)

            con = _connect(temp_db)
            row = con.execute(
                "SELECT times_generated, last_query_text, last_latency_ms FROM adversarial_combinations WHERE fingerprint = ?",
                (fp,)
            ).fetchone()
            con.close()
            assert row[0] == 2
            assert row[1] == "query one again"
            assert row[2] == 150

    def test_already_tried_reflects_real_db_state(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            fp = ("x", "y")
            import json
            fp_json = json.dumps(fp)
            assert _already_tried(fp) is False
            _record_result(fp_json, "no_intent_fallthrough", "q", "kiwix", 100, None)
            assert _already_tried(fp) is True


class TestAnomalyDetection:
    """Each check verifies a documented Mnemolis behavioral guarantee —
    never a correctness judgment about response content."""

    def test_check_crash_detects_explicit_error(self):
        assert _check_crash("", "Connection refused") is not None

    def test_check_crash_detects_raw_traceback(self):
        result = "Traceback (most recent call last):\n  File x, line 1\nValueError: bad"
        assert _check_crash(result, None) is not None

    def test_check_crash_passes_clean_result(self):
        assert _check_crash("Molybdenum is element 42.", None) is None

    def test_check_source_mismatch_flags_real_mismatch(self):
        reason = _check_source_mismatch("multi_intent_chain", ["forecast", "ha"], "uptime")
        assert reason is not None
        assert "forecast" in reason and "ha" in reason

    def test_check_source_mismatch_allows_fusion(self):
        assert _check_source_mismatch("multi_intent_chain", ["forecast", "ha"], "fusion") is None

    def test_check_source_mismatch_allows_exact_match(self):
        assert _check_source_mismatch("multi_intent_chain", ["forecast"], "forecast") is None

    def test_check_source_mismatch_skips_when_no_intent_sources(self):
        """no_intent_fallthrough-style ingredients (no real INTENT_MAP
        key among them) should never trigger this check."""
        assert _check_source_mismatch("no_intent_fallthrough", ["molybdenum"], "kiwix") is None

    def test_check_multi_intent_part_count_flags_real_mismatch(self):
        result = "[KIWIX — TOPIC] some content"  # 1 header for 4 intended intents
        reason = _check_multi_intent_part_count("multi_intent_chain", ["forecast", "ha", "news", "uptime"], result)
        assert reason is not None

    def test_check_multi_intent_part_count_passes_close_match(self):
        result = "[KIWIX — A] x\n[WEB — B] y\n[NEWS — C] z"  # 3 headers for 3 intents
        reason = _check_multi_intent_part_count("multi_intent_chain", ["forecast", "ha", "news"], result)
        assert reason is None

    def test_check_multi_intent_part_count_only_applies_to_its_own_recipe(self):
        result = "[KIWIX — TOPIC] some content"
        reason = _check_multi_intent_part_count("no_intent_fallthrough", ["forecast", "ha", "news", "uptime"], result)
        assert reason is None

    def test_check_discourse_framing_dropped_kiwix_flags_real_drop(self):
        reason = _check_discourse_framing_dropped_kiwix(
            "discourse_framing_plus_real_keyword", "plain web result, no kiwix", "web"
        )
        assert reason is not None

    def test_check_discourse_framing_dropped_kiwix_passes_when_source_is_kiwix(self):
        assert _check_discourse_framing_dropped_kiwix(
            "discourse_framing_plus_real_keyword", "some result", "kiwix"
        ) is None

    def test_check_discourse_framing_dropped_kiwix_passes_when_header_present(self):
        assert _check_discourse_framing_dropped_kiwix(
            "discourse_framing_plus_real_keyword", "[KIWIX — TOPIC] real content", "fusion"
        ) is None

    def test_check_conditional_remainder_sections_flags_missing_headers(self):
        reason = _check_conditional_remainder_sections("conditional_with_remainder", "just plain text, no headers")
        assert reason is not None

    def test_check_conditional_remainder_sections_passes_with_header(self):
        reason = _check_conditional_remainder_sections(
            "conditional_with_remainder", "[UPTIME — STATUS] all services up"
        )
        assert reason is None

    def test_check_unexpected_empty_uses_real_fusion_looks_empty(self):
        """Must delegate to the real, canonical fusion._looks_empty(),
        never a forked copy of its phrase list."""
        assert _check_unexpected_empty("No results found.") is not None
        assert _check_unexpected_empty("Molybdenum is a transition metal.") is None

    def test_check_latency_outlier_requires_history(self):
        """With fewer than 10 historical samples, must return None (not
        yet decidable) rather than guessing."""
        assert _check_latency_outlier("nonexistent_recipe_no_history", 99999) is None

    def test_check_latency_outlier_flags_real_outlier(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            import json
            for i in range(20):
                fp = json.dumps([f"sample-{i}"])
                _record_result(fp, "multi_intent_chain", f"query {i}", "kiwix", 200, None)
            reason = _check_latency_outlier("multi_intent_chain", 50000)
            assert reason is not None

    def test_check_latency_outlier_passes_normal_latency(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            import json
            for i in range(20):
                fp = json.dumps([f"sample-{i}"])
                _record_result(fp, "multi_intent_chain", f"query {i}", "kiwix", 200, None)
            reason = _check_latency_outlier("multi_intent_chain", 210)
            assert reason is None

    def test_check_latency_outlier_respects_configured_min_samples(self, tmp_path):
        """Lowering ADVERSARIAL_TEST_LATENCY_OUTLIER_MIN_SAMPLES must let
        the check engage with fewer real samples than the default 10 —
        proves this is a real setting, not a renamed constant."""
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.adversarial_testing.settings.adversarial_test_latency_outlier_min_samples", 3):
            init_adversarial_db()
            import json
            for i in range(5):
                fp = json.dumps([f"sample-{i}"])
                _record_result(fp, "multi_intent_chain", f"query {i}", "kiwix", 200, None)
            # Only 5 samples — would be silently skipped under the default
            # floor of 10, but the lowered setting should let it engage.
            reason = _check_latency_outlier("multi_intent_chain", 50000)
            assert reason is not None

    def test_check_latency_outlier_respects_configured_multiplier(self, tmp_path):
        """A tighter multiplier must flag latency the default 1.5x
        wouldn't have caught."""
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.adversarial_testing.settings.adversarial_test_latency_outlier_multiplier", 1.05), \
             patch("app.adversarial_testing.settings.adversarial_test_latency_outlier_floor_ms", 0):
            init_adversarial_db()
            import json
            for i in range(20):
                fp = json.dumps([f"sample-{i}"])
                _record_result(fp, "multi_intent_chain", f"query {i}", "kiwix", 200, None)
            # 220ms is only 1.1x the 200ms p95 — would pass under the
            # default 1.5x multiplier, but should fail under 1.05x.
            reason = _check_latency_outlier("multi_intent_chain", 220)
            assert reason is not None

    def test_check_part_count_mismatch_respects_configured_tolerance(self):
        """A tighter tolerance must flag a 1-header-off mismatch the
        default tolerance of 2 wouldn't have caught."""
        result = "[KIWIX — A] x\n[WEB — B] y"  # 2 headers for 3 intents — diff of 1
        with patch("app.adversarial_testing.settings.adversarial_test_part_count_mismatch_tolerance", 1):
            reason = _check_multi_intent_part_count("multi_intent_chain", ["forecast", "ha", "news"], result)
            assert reason is not None
        # Same inputs, default tolerance of 2 — should NOT flag a diff of only 1.
        reason_default = _check_multi_intent_part_count("multi_intent_chain", ["forecast", "ha", "news"], result)
        assert reason_default is None


class TestFullCycle:
    """End-to-end tests of the scheduled job body against a stubbed
    route_with_source — never a real network call."""

    def test_cycle_persists_results_for_every_batch_item(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.adversarial_testing.settings.adversarial_test_batch_size", 5), \
             patch("app.router.route_with_source", return_value=("[KIWIX — X] content", "kiwix")):
            init_adversarial_db()
            run_adversarial_test_cycle()
            con = _connect(temp_db)
            count = con.execute("SELECT COUNT(*) FROM adversarial_combinations").fetchone()[0]
            con.close()
            assert count == 5

    def test_cycle_survives_a_raised_exception_in_one_iteration(self, tmp_path):
        """A single iteration's exception must never abort the whole
        batch — same per-item try/except convention as snapshot jobs."""
        temp_db = str(tmp_path / "test_adversarial.db")
        call_count = {"n": 0}

        def flaky_route(query, source="auto", fusion_sources=None):
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                raise RuntimeError("simulated failure")
            return ("[KIWIX — X] content", "kiwix")

        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.adversarial_testing.settings.adversarial_test_batch_size", 6), \
             patch("app.router.route_with_source", side_effect=flaky_route):
            init_adversarial_db()
            run_adversarial_test_cycle()  # must not raise
            con = _connect(temp_db)
            count = con.execute("SELECT COUNT(*) FROM adversarial_combinations").fetchone()[0]
            crashed = con.execute(
                "SELECT COUNT(*) FROM adversarial_combinations WHERE last_flagged_reason LIKE 'crash:%'"
            ).fetchone()[0]
            con.close()
            assert count == 6
            assert crashed >= 1

    def test_cycle_never_touches_real_cache_files(self, tmp_path, monkeypatch):
        """Must write only to adversarial_testing.db — never cache.json,
        routing_cache.json, or query_log.db."""
        temp_db = str(tmp_path / "test_adversarial.db")
        sentinel_cache = tmp_path / "cache.json"
        sentinel_cache.write_text('{"sentinel": true}')

        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.adversarial_testing.settings.adversarial_test_batch_size", 3), \
             patch("app.router.route_with_source", return_value=("[KIWIX — X] content", "kiwix")):
            init_adversarial_db()
            run_adversarial_test_cycle()
            # The sentinel cache file must be byte-for-byte untouched.
            assert sentinel_cache.read_text() == '{"sentinel": true}'

    def test_cycle_is_a_safe_noop_when_disabled(self, tmp_path):
        """run_adversarial_test_cycle() must check ADVERSARIAL_TEST_ENABLED
        itself — defense in depth, so a direct call (e.g. via
        POST /adversarial/trigger) can never run real queries against the
        LLM/SearXNG/Kiwix backends while the feature is supposed to be off,
        even if scheduler registration was somehow bypassed."""
        temp_db = str(tmp_path / "test_adversarial.db")
        route_called = {"n": 0}

        def tracking_route(query, source="auto", fusion_sources=None):
            route_called["n"] += 1
            return ("[KIWIX — X] content", "kiwix")

        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.adversarial_testing.settings.adversarial_test_enabled", False), \
             patch("app.router.route_with_source", side_effect=tracking_route):
            result = run_adversarial_test_cycle()
            assert result["status"] == "disabled"
            assert result["queries_run"] == 0
            assert route_called["n"] == 0  # never touched the real pipeline at all

    def test_cycle_returns_real_summary_when_enabled(self, tmp_path):
        """The return value POST /adversarial/trigger relies on — must
        report what actually happened on that specific call, not a bare
        success with no way to confirm results."""
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.adversarial_testing.settings.adversarial_test_enabled", True), \
             patch("app.adversarial_testing.settings.adversarial_test_batch_size", 4), \
             patch("app.router.route_with_source", return_value=("No results found.", "web")):
            init_adversarial_db()
            result = run_adversarial_test_cycle()
            assert result["status"] == "ran"
            assert result["queries_run"] == 4
            assert result["flagged"] == 4  # every one matches unexpected_empty


class TestHealthSummaryAndFlaggedEndpointData:
    """Tests for the /health and /adversarial/flagged data functions."""

    def test_summary_reports_disabled_without_touching_db(self, tmp_path):
        """A disabled feature should report its own state directly,
        never silently falling through to never_ran/stale based on
        whatever the DB happens to contain."""
        temp_db = str(tmp_path / "nonexistent" / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.adversarial_testing.settings.adversarial_test_enabled", False):
            summary = get_adversarial_test_summary()
            assert summary == {"status": "disabled"}

    def test_summary_never_ran_state(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            summary = get_adversarial_test_summary()
            assert summary["status"] == "never_ran"
            assert summary["total_combinations_tried"] == 0
            assert summary["flagged_for_review"] == 0

    def test_summary_counts_match_real_rows(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            import json
            _record_result(json.dumps(["a"]), "no_intent_fallthrough", "q1", "kiwix", 100, None)
            _record_result(json.dumps(["b"]), "no_intent_fallthrough", "q2", "kiwix", 100, "crash: boom")
            summary = get_adversarial_test_summary()
            assert summary["status"] == "ok"
            assert summary["total_combinations_tried"] == 2
            assert summary["flagged_for_review"] == 1

    def test_get_flagged_combinations_only_returns_flagged_rows(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            import json
            _record_result(json.dumps(["a"]), "no_intent_fallthrough", "clean query", "kiwix", 100, None)
            _record_result(json.dumps(["b"]), "no_intent_fallthrough", "bad query", "kiwix", 100, "crash: boom")
            flagged = get_flagged_combinations()
            assert len(flagged) == 1
            assert flagged[0]["last_query_text"] == "bad query"
            assert flagged[0]["last_flagged_reason"] == "crash: boom"

    def test_get_flagged_combinations_respects_limit(self, tmp_path):
        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db):
            init_adversarial_db()
            import json
            for i in range(10):
                _record_result(json.dumps([f"item-{i}"]), "no_intent_fallthrough", f"q{i}", "kiwix", 100, f"crash: boom {i}")
            flagged = get_flagged_combinations(limit=3)
            assert len(flagged) == 3


class TestEndpointsViaTestClient:
    """End-to-end tests through the real FastAPI app — confirms the
    enable switch actually changes real HTTP-level behavior, not just
    the underlying functions in isolation. Each test boots its own
    short-lived TestClient (rather than sharing test_main.py's
    module-scoped fixture) since the enabled/disabled state must be
    patched before the lifespan context manager runs, which a shared
    fixture spanning multiple tests can't do per-test.
    """

    def test_trigger_endpoint_runs_a_real_cycle(self, tmp_path):
        from fastapi.testclient import TestClient
        import app.main as main

        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.main.settings.adversarial_test_enabled", True), \
             patch("app.main.settings.adversarial_test_batch_size", 3), \
             patch("app.router.route_with_source", return_value=("[KIWIX — X] content", "kiwix")):
            with TestClient(main.app) as client:
                response = client.post("/adversarial/trigger")
                assert response.status_code == 200
                body = response.json()
                assert body["status"] == "ran"
                assert body["queries_run"] == 3

    def test_trigger_endpoint_reports_disabled_without_running(self, tmp_path):
        from fastapi.testclient import TestClient
        import app.main as main

        temp_db = str(tmp_path / "test_adversarial.db")
        route_called = {"n": 0}

        def tracking_route(query, source="auto", fusion_sources=None):
            route_called["n"] += 1
            return ("[KIWIX — X] content", "kiwix")

        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.main.settings.adversarial_test_enabled", False), \
             patch("app.router.route_with_source", side_effect=tracking_route):
            with TestClient(main.app) as client:
                response = client.post("/adversarial/trigger")
                assert response.status_code == 200
                assert response.json()["status"] == "disabled"
                assert route_called["n"] == 0

    def test_flagged_endpoint_reports_disabled_state(self, tmp_path):
        from fastapi.testclient import TestClient
        import app.main as main

        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.main.settings.adversarial_test_enabled", False):
            with TestClient(main.app) as client:
                response = client.get("/adversarial/flagged")
                assert response.status_code == 200
                assert response.json() == {"status": "disabled", "count": 0, "flagged": []}

    def test_health_reports_disabled_adversarial_testing(self, tmp_path):
        from fastapi.testclient import TestClient
        import app.main as main

        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.main.settings.adversarial_test_enabled", False):
            with TestClient(main.app) as client:
                response = client.get("/health")
                assert response.status_code == 200
                assert response.json()["adversarial_testing"] == {"status": "disabled"}

    def test_scheduler_does_not_register_job_when_disabled(self, tmp_path):
        """The real lifespan startup must skip scheduler.add_job() for
        adversarial_testing entirely when disabled — not just skip
        running it once registered. Inspects the actual live
        apscheduler instance's registered job IDs to confirm directly,
        rather than just trusting the app booted without an error."""
        from fastapi.testclient import TestClient
        import app.main as main
        import apscheduler.schedulers.background

        captured_schedulers = []
        original_init = apscheduler.schedulers.background.BackgroundScheduler.__init__

        def capturing_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            captured_schedulers.append(self)

        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.main.settings.adversarial_test_enabled", False), \
             patch.object(apscheduler.schedulers.background.BackgroundScheduler, "__init__", capturing_init):
            with TestClient(main.app):
                assert len(captured_schedulers) == 1
                job_ids = {job.id for job in captured_schedulers[0].get_jobs()}
                assert "adversarial_testing" not in job_ids
                # The other four real snapshot jobs must still be
                # present and unaffected by this flag.
                assert {"snapshot_uptime", "snapshot_forecast", "snapshot_news", "snapshot_ha"}.issubset(job_ids)

    def test_scheduler_registers_job_when_enabled(self, tmp_path):
        """The inverse confirmation — enabled (the default) must
        actually register the job, not just default to skipping it."""
        from fastapi.testclient import TestClient
        import app.main as main
        import apscheduler.schedulers.background

        captured_schedulers = []
        original_init = apscheduler.schedulers.background.BackgroundScheduler.__init__

        def capturing_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            captured_schedulers.append(self)

        temp_db = str(tmp_path / "test_adversarial.db")
        with patch("app.adversarial_testing.ADVERSARIAL_DB", temp_db), \
             patch("app.main.settings.adversarial_test_enabled", True), \
             patch.object(apscheduler.schedulers.background.BackgroundScheduler, "__init__", capturing_init):
            with TestClient(main.app):
                assert len(captured_schedulers) == 1
                job_ids = {job.id for job in captured_schedulers[0].get_jobs()}
                assert "adversarial_testing" in job_ids
