"""
Tests for app/temporal_patterns.py — cross-source temporal pattern
detection: event extraction, non-overlapping occurrence counting,
Bonferroni-corrected significance testing, and out-of-sample
re-validation.
"""
from datetime import datetime, timezone, timedelta


def _t(minutes_from_zero: int) -> datetime:
    """A fixed reference instant plus an offset, for building synthetic
    event timelines without depending on real wall-clock time."""
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes_from_zero)


class TestCountNonoverlappingOccurrences:
    """Tests for _count_nonoverlapping_occurrences — the per-pair
    occurrence counter that the rest of the mining procedure's
    significance test depends on entirely. A real bug was found in
    this function's own first draft via exactly this kind of harder,
    adversarial test: a burst of 3 A's followed by 3 B's within the lag
    window returned 1, not 3, because the scan position was being
    advanced past not-yet-evaluated A occurrences whenever an earlier A
    claimed a distant B. Every test below was constructed specifically
    to exercise a real, distinct failure mode of that shape — this
    class is the permanent regression coverage for that bug, not just
    documentation of it.
    """

    def setup_method(self):
        from app.temporal_patterns import _count_nonoverlapping_occurrences
        self.count = _count_nonoverlapping_occurrences

    def test_simple_pair_within_window(self):
        events = [("A", _t(0)), ("B", _t(10))]
        assert self.count(events, "A", "B", 30) == 1

    def test_b_outside_lag_window_does_not_count(self):
        events = [("A", _t(0)), ("B", _t(40))]
        assert self.count(events, "A", "B", 30) == 0

    def test_no_b_at_all(self):
        events = [("A", _t(0)), ("A", _t(10))]
        assert self.count(events, "A", "B", 30) == 0

    def test_two_well_separated_pairs_both_count(self):
        events = [("A", _t(0)), ("B", _t(5)), ("A", _t(100)), ("B", _t(105))]
        assert self.count(events, "A", "B", 30) == 2

    def test_burst_of_as_then_bs_counts_each_a_once_not_every_combination(self):
        """The exact bug case: 3 A's followed by 3 B's, all mutually
        within the lag window. A naive "every A within range of every
        B" count would be 9; correct non-overlapping counting is 3 —
        each A claims exactly one, distinct B."""
        events = [
            ("A", _t(0)), ("A", _t(1)), ("A", _t(2)),
            ("B", _t(5)), ("B", _t(6)), ("B", _t(7)),
        ]
        assert self.count(events, "A", "B", 30) == 3

    def test_interleaved_a_b_a_b_counts_two_distinct_pairs(self):
        events = [("A", _t(0)), ("B", _t(2)), ("A", _t(3)), ("B", _t(5))]
        assert self.count(events, "A", "B", 30) == 2

    def test_more_as_than_bs_only_counts_as_many_as_bs_allow(self):
        """3 A's, only 1 real B in range — the regression case for the
        original bug, isolated from the 1:1 burst case above. The
        original buggy version would also have failed this differently
        (by skipping evaluation of the 2nd/3rd A entirely rather than
        correctly recognizing there's only one B to go around)."""
        events = [("A", _t(0)), ("A", _t(1)), ("A", _t(2)), ("B", _t(5))]
        assert self.count(events, "A", "B", 30) == 1

    def test_b_before_a_does_not_count_lag_is_directional(self):
        """Only forward lag (B after A) counts — a B that occurred
        before any A exists should never be claimed."""
        events = [("B", _t(0)), ("A", _t(5))]
        assert self.count(events, "A", "B", 30) == 0

    def test_b_between_two_bs_around_an_a_only_the_later_one_counts(self):
        """An A sandwiched between two B's — only the B that's actually
        AFTER the A (within the window) can be claimed by it; the
        earlier B is irrelevant to this A."""
        events = [("B", _t(0)), ("A", _t(5)), ("B", _t(10))]
        assert self.count(events, "A", "B", 30) == 1

    def test_two_as_sharing_one_reachable_b_only_counts_once(self):
        """Two A's both within range of the same single B — only one
        of them can claim it; the count must not double-claim a single
        real B as two separate occurrences."""
        events = [("A", _t(0)), ("A", _t(5)), ("B", _t(10))]
        assert self.count(events, "A", "B", 30) == 1

    def test_first_pair_out_of_range_second_pair_in_range(self):
        """A real-shape mixed case: one A/B pair too far apart to
        count, a second pair close enough — confirms an out-of-range
        miss for one A doesn't somehow affect a later, genuinely
        in-range pair."""
        events = [("A", _t(0)), ("B", _t(50)), ("A", _t(60)), ("B", _t(65))]
        assert self.count(events, "A", "B", 30) == 1

    def test_unrelated_event_types_in_between_are_ignored(self):
        events = [("A", _t(0)), ("C", _t(2)), ("D", _t(4)), ("B", _t(10))]
        assert self.count(events, "A", "B", 30) == 1

    def test_empty_events_list(self):
        assert self.count([], "A", "B", 30) == 0

    def test_lag_window_boundary_is_inclusive(self):
        """B landing exactly at the lag window's edge should still
        count — the comparison is <=, not <."""
        events = [("A", _t(0)), ("B", _t(30))]
        assert self.count(events, "A", "B", 30) == 1

    def test_lag_window_just_past_boundary_does_not_count(self):
        events = [("A", _t(0)), ("B", _t(31))]
        assert self.count(events, "A", "B", 30) == 0


class TestExtractHaEvents:
    """Tests for extract_ha_events — confirms it's a genuine, faithful
    pass-through of _iter_ha_entity_changes() (the same shared core
    _diff_ha() itself uses), not an independent re-implementation that
    could drift from it."""

    def setup_method(self):
        from app.temporal_patterns import extract_ha_events
        self.extract = extract_ha_events

    def _entity(self, entity_id, state, friendly_name=None, device_class=""):
        return {
            "entity_id": entity_id,
            "state": state,
            "friendly_name": friendly_name or entity_id,
            "device_class": device_class,
        }

    def _snapshot(self, entities):
        import json
        return json.dumps(entities)

    def test_lock_event_type_includes_entity_id_and_state(self):
        old = self._snapshot([self._entity("lock.front_door", "locked", "Front Door")])
        new = self._snapshot([self._entity("lock.front_door", "unlocked", "Front Door")])
        events = self.extract(old, new)
        assert len(events) == 1
        assert events[0]["event_type"] == "lock.front_door:unlocked"
        assert "Front Door" in events[0]["raw_detail"]

    def test_door_event_type_uses_opened_closed_label_not_raw_state(self):
        old = self._snapshot([self._entity("binary_sensor.back_door", "off", "Back Door", "door")])
        new = self._snapshot([self._entity("binary_sensor.back_door", "on", "Back Door", "door")])
        events = self.extract(old, new)
        assert len(events) == 1
        assert events[0]["event_type"] == "binary_sensor.back_door:opened"

    def test_window_event_type_uses_opened_closed_label(self):
        """Regression test for a real gap found via review, with a
        real bug found in the first attempted fix: extract_ha_events()'s
        event-type construction had a two-way if/else (lock/door vs.
        everything else, assumed to mean battery_low) that silently
        mislabeled every new kind added to _iter_ha_entity_changes() —
        a real window event was first reported as ":battery_low" rather
        than ":opened" before this was caught."""
        old = self._snapshot([self._entity("binary_sensor.kitchen_window", "off", "Kitchen Window", "window")])
        new = self._snapshot([self._entity("binary_sensor.kitchen_window", "on", "Kitchen Window", "window")])
        events = self.extract(old, new)
        assert len(events) == 1
        assert events[0]["event_type"] == "binary_sensor.kitchen_window:opened"

    def test_motion_event_type_uses_motion_detected_label(self):
        """The same regression as the window test above, for motion
        specifically — the design doc's own headline example ("does a
        front-door lock event reliably precede a motion event") depends
        entirely on this producing a real, distinct, correctly-labeled
        event type, not a mislabeled ":battery_low"."""
        old = self._snapshot([self._entity("binary_sensor.hallway_motion", "off", "Hallway Motion", "motion")])
        new = self._snapshot([self._entity("binary_sensor.hallway_motion", "on", "Hallway Motion", "motion")])
        events = self.extract(old, new)
        assert len(events) == 1
        assert events[0]["event_type"] == "binary_sensor.hallway_motion:motion_detected"

    def test_battery_event_type_is_distinct_from_state_value(self):
        """battery_low events use a fixed ':battery_low' suffix rather
        than encoding the literal percentage — the percentage varies
        every time, which would make every battery-low event for the
        same entity register as a different, never-repeating event
        type and make pattern mining against it meaningless."""
        old = self._snapshot([self._entity("sensor.lock_battery", "25", "Lock Battery", "battery")])
        new = self._snapshot([self._entity("sensor.lock_battery", "15", "Lock Battery", "battery")])
        events = self.extract(old, new)
        assert len(events) == 1
        assert events[0]["event_type"] == "sensor.lock_battery:battery_low"

    def test_two_different_entities_produce_two_distinct_event_types(self):
        """Confirms events are keyed per-entity, not collapsed to a
        generic 'a door opened somewhere' type — distinguishing WHICH
        door matters for finding a genuinely reliable relationship."""
        old = self._snapshot([
            self._entity("binary_sensor.front_door", "off", "Front Door", "door"),
            self._entity("binary_sensor.back_door", "off", "Back Door", "door"),
        ])
        new = self._snapshot([
            self._entity("binary_sensor.front_door", "on", "Front Door", "door"),
            self._entity("binary_sensor.back_door", "on", "Back Door", "door"),
        ])
        events = self.extract(old, new)
        event_types = {e["event_type"] for e in events}
        assert event_types == {"binary_sensor.front_door:opened", "binary_sensor.back_door:opened"}

    def test_no_change_produces_no_events(self):
        snap = self._snapshot([self._entity("lock.front_door", "locked", "Front Door")])
        assert self.extract(snap, snap) == []

    def test_malformed_json_produces_no_events_not_a_crash(self):
        assert self.extract("not json", "also not json") == []

    def test_lights_produce_no_events_same_exclusion_as_diff_ha(self):
        old = self._snapshot([self._entity("light.bedroom", "off", "Bedroom Light")])
        new = self._snapshot([self._entity("light.bedroom", "on", "Bedroom Light")])
        assert self.extract(old, new) == []


class TestExtractUptimeEvents:
    """Tests for extract_uptime_events — confirms it correctly reuses
    _diff_uptime()'s own text rather than re-parsing it independently,
    and correctly classifies the coarse event type from the resulting
    description."""

    def setup_method(self):
        from app.temporal_patterns import extract_uptime_events
        self.extract = extract_uptime_events

    def test_outage_classified_correctly(self):
        old = "All 15 services are up."
        new = "1 service is down: Ollama"
        events = self.extract(old, new)
        assert len(events) == 1
        assert events[0]["event_type"] == "uptime:outage"

    def test_recovery_classified_correctly(self):
        old = "1 service is down: Ollama"
        new = "All 15 services are up."
        events = self.extract(old, new)
        assert len(events) == 1
        assert events[0]["event_type"] == "uptime:recovery"

    def test_pending_classified_correctly(self):
        old = "All 15 services are up."
        new = "1 service check pending (possible outage starting): NewMonitor"
        events = self.extract(old, new)
        assert len(events) == 1
        assert events[0]["event_type"] == "uptime:pending"

    def test_no_change_produces_no_events(self):
        snap = "All 15 services are up."
        assert self.extract(snap, snap) == []


class TestPoissonSf:
    """Tests for _poisson_sf — the right-tailed Poisson survival
    function this module's significance test depends on. Verified
    directly against scipy.stats.poisson.sf during development; these
    tests lock in known-correct values without requiring scipy as a
    runtime or test dependency."""

    def setup_method(self):
        from app.temporal_patterns import _poisson_sf
        self.sf = _poisson_sf

    def test_k_zero_is_always_certain(self):
        """P(X >= 0) is always 1, for any non-negative mean."""
        assert self.sf(0, 2.0) == 1.0
        assert self.sf(0, 0.001) == 1.0

    def test_zero_mean_with_positive_k_is_impossible(self):
        assert self.sf(5, 0.0) == 0.0

    def test_matches_known_value_k_equals_mean(self):
        """P(X >= 2) for Poisson(2.0) is a known closed-form value,
        confirmed against scipy.stats.poisson.sf(1, 2.0) = 0.593994..."""
        result = self.sf(2, 2.0)
        assert abs(result - 0.5939941502901619) < 1e-9

    def test_large_k_relative_to_mean_is_very_small(self):
        """A real, observed count far exceeding the null-hypothesis
        expectation should produce a very small (significant)
        p-value."""
        result = self.sf(10, 2.0)
        assert result < 0.001

    def test_result_always_in_valid_probability_range(self):
        for k, mean in [(0, 0.0), (1, 0.5), (50, 10.0), (3, 3.0), (100, 1.0)]:
            result = self.sf(k, mean)
            assert 0.0 <= result <= 1.0


class TestExpectedCountUnderNull:
    """Tests for _expected_count_under_null — the null-hypothesis
    baseline the real observed count gets compared against."""

    def setup_method(self):
        from app.temporal_patterns import _expected_count_under_null
        self.expected = _expected_count_under_null

    def test_higher_base_rate_of_b_increases_expected_count(self):
        """If B occurs more often overall, more B's are expected to
        land near any given A purely by chance — the null expectation
        must scale with B's real observed rate, not be a flat
        constant."""
        events_rare_b = [("A", _t(0)), ("A", _t(100)), ("B", _t(500))]
        events_common_b = [
            ("A", _t(0)), ("A", _t(100)),
            ("B", _t(10)), ("B", _t(50)), ("B", _t(150)), ("B", _t(300)), ("B", _t(450)),
        ]
        window = 600.0
        rare = self.expected(events_rare_b, "A", "B", 30, window)
        common = self.expected(events_common_b, "A", "B", 30, window)
        assert common > rare

    def test_more_occurrences_of_a_increases_expected_count(self):
        """More A's means more independent chances for a B to land
        nearby purely by chance — expected count should scale with
        how many real A's there are."""
        few_a = [("A", _t(0)), ("B", _t(10)), ("B", _t(200)), ("B", _t(400))]
        many_a = [
            ("A", _t(0)), ("A", _t(50)), ("A", _t(100)), ("A", _t(150)),
            ("B", _t(10)), ("B", _t(200)), ("B", _t(400)),
        ]
        window = 600.0
        assert self.expected(many_a, "A", "B", 30, window) > self.expected(few_a, "A", "B", 30, window)

    def test_zero_a_occurrences_means_zero_expected(self):
        events = [("B", _t(0)), ("B", _t(10))]
        assert self.expected(events, "A", "B", 30, 600.0) == 0.0


class TestMiningCycleStatisticalValidity:
    """The test the design doc's own 'definition of done' explicitly
    requires: construct a known, intentionally-random (non-correlated)
    synthetic event stream and confirm Bonferroni correction genuinely
    suppresses the spurious patterns that WOULD otherwise appear,
    mirroring how Adversarial Self-Testing's own test suite directly
    verified its hard constraints rather than just asserting no
    exception was raised.

    Uses a real temp SQLite DB (not a mock) and the real
    run_temporal_pattern_mining_cycle() / init_temporal_patterns_db()
    functions end to end — this is deliberately an integration test of
    the actual statistical machinery, not just the pure helper
    functions tested in isolation above.
    """

    def setup_method(self):
        import random
        self.random = random.Random(42)  # fixed seed — deterministic, reproducible test

    def _seed_events(self, temp_db, events: list[tuple[str, str]]):
        """events: list of (event_type, timestamp_str) pairs."""
        from app.temporal_patterns import _connect
        con = _connect(temp_db)
        for event_type, ts in events:
            con.execute(
                "INSERT INTO temporal_events (source, event_type, timestamp, raw_detail) VALUES (?, ?, ?, ?)",
                ("test", event_type, ts, ""),
            )
        con.commit()
        con.close()

    def _fmt(self, dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_purely_random_uncorrelated_events_produce_no_candidates_after_correction(self, tmp_path):
        """The actual, required test: many event types, each occurring
        at random, MUTUALLY INDEPENDENT times across a real window —
        by construction, there is no genuine relationship between any
        pair. Confirms the full mining cycle (extraction already
        skipped here since these are inserted directly as already-
        extracted events) finds zero candidates, demonstrating that
        Bonferroni correction is doing real, load-bearing work and
        not just present as inert code.

        Found via review: an earlier version of this test ran exactly
        once, against a single fixed seed, while the changelog
        describing this release claimed "30 independent random seeds...
        zero false-positive candidates in every single trial" — a claim
        that was true when manually re-verified outside the test suite,
        but not actually backed by anything repeatable in the committed
        code. Rewritten as a genuine 30-seed loop so this claim is true
        of what's actually shipped and re-checked on every test run,
        not just true of a one-off manual check that left no permanent
        trace."""
        from app.temporal_patterns import (
            init_temporal_patterns_db, run_temporal_pattern_mining_cycle,
        )
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch
        import random as random_module

        for seed in range(30):
            rng = random_module.Random(seed)
            temp_db = str(tmp_path / f"test_temporal_patterns_seed{seed}.db")
            window_start = datetime.now(timezone.utc) - timedelta(hours=23)

            event_types = [f"type_{i}" for i in range(8)]
            events = []
            # Each type fires a modest, realistic number of times (per
            # the design doc's own real-world volume estimate — tens,
            # not thousands), at uniformly random offsets across the
            # window, independently of every other type.
            for et in event_types:
                for _ in range(15):
                    offset_minutes = rng.uniform(0, 23 * 60)
                    ts = window_start + timedelta(minutes=offset_minutes)
                    events.append((et, self._fmt(ts)))

            with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
                 patch("app.temporal_patterns.run_event_extraction_cycle", return_value=0), \
                 patch("app.config.settings.temporal_pattern_detection_enabled", True), \
                 patch("app.config.settings.temporal_pattern_mining_interval_hours", 24), \
                 patch("app.config.settings.temporal_pattern_min_occurrences", 3):
                init_temporal_patterns_db()
                self._seed_events(temp_db, events)
                result = run_temporal_pattern_mining_cycle()

            assert result["status"] == "ran"
            # The actual, required assertion — correction must
            # genuinely suppress spurious findings from pure noise,
            # not merely run without crashing — re-checked across
            # every one of the 30 independent seeds, not just one.
            assert result["candidates_found"] == 0, (
                f"Bonferroni correction failed to suppress spurious patterns in "
                f"purely random data (seed={seed}): found {result['candidates_found']} "
                f"candidates across {result['comparisons_run']} comparisons"
            )

    def test_genuine_reliable_pattern_is_found_as_a_candidate(self, tmp_path):
        """The complementary case — confirms the mining procedure can
        actually find something real when a real, reliable, repeated
        timing relationship genuinely exists, not just that it's
        conservative. A's reliably precede B's by ~5 minutes, every
        time, with no exceptions — exactly the kind of clean signal the
        statistical floor (min_occurrences) and the corrected
        significance test should both clear easily, alongside a large
        pool of unrelated noise event types that should NOT also
        register as candidates."""
        from app.temporal_patterns import (
            init_temporal_patterns_db, run_temporal_pattern_mining_cycle,
        )
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        window_start = datetime.now(timezone.utc) - timedelta(hours=23)

        events = []
        # A genuinely reliable signal: 12 A->B pairs, every single one
        # exactly 5 minutes apart, spread across the window.
        for i in range(12):
            base = window_start + timedelta(minutes=i * 100)
            events.append(("door.front:opened", self._fmt(base)))
            events.append(("motion.hallway:detected", self._fmt(base + timedelta(minutes=5))))

        # Plus a pool of unrelated noise types at random times, so this
        # test also confirms the real signal doesn't get drowned out
        # or cause unrelated noise types to falsely register.
        noise_types = [f"noise_{i}" for i in range(5)]
        for nt in noise_types:
            for _ in range(15):
                offset_minutes = self.random.uniform(0, 23 * 60)
                ts = window_start + timedelta(minutes=offset_minutes)
                events.append((nt, self._fmt(ts)))

        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.temporal_patterns.run_event_extraction_cycle", return_value=0), \
             patch("app.config.settings.temporal_pattern_detection_enabled", True), \
             patch("app.config.settings.temporal_pattern_mining_interval_hours", 24), \
             patch("app.config.settings.temporal_pattern_min_occurrences", 3):
            init_temporal_patterns_db()
            self._seed_events(temp_db, events)
            result = run_temporal_pattern_mining_cycle()

        assert result["status"] == "ran"
        assert result["candidates_found"] >= 1

        from app.temporal_patterns import get_temporal_patterns
        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db):
            candidates = get_temporal_patterns(status="candidate")
        matching = [
            c for c in candidates
            if c["event_type_a"] == "door.front:opened" and c["event_type_b"] == "motion.hallway:detected"
        ]
        assert len(matching) == 1
        assert matching[0]["raw_count"] == 12
        # Every single candidate row must carry the literal disclaimer
        # — requirement #5 / §8 checklist item, verified at the data
        # layer here (the endpoint-level test below confirms it's also
        # present in the actual HTTP response).
        assert matching[0]["note"] == (
            "This reflects observed timing correlation only and does not establish a causal relationship."
        )


class TestOutOfSampleValidation:
    """Tests for _revalidate_due_candidates — confirms a candidate can
    be observed, mechanically re-checked against later, non-overlapping
    data, and correctly promoted to 'confirmed' or demoted to
    'unconfirmed' depending on whether it actually replicates — not
    just designed on paper, per the design doc's definition of done.
    """

    def _fmt(self, dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _seed_events(self, temp_db, events):
        from app.temporal_patterns import _connect
        con = _connect(temp_db)
        for event_type, ts in events:
            con.execute(
                "INSERT INTO temporal_events (source, event_type, timestamp, raw_detail) VALUES (?, ?, ?, ?)",
                ("test", event_type, ts, ""),
            )
        con.commit()
        con.close()

    def test_pattern_that_replicates_in_later_window_is_confirmed(self, tmp_path):
        from app.temporal_patterns import (
            init_temporal_patterns_db, _connect, _revalidate_due_candidates,
        )
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        now = datetime.now(timezone.utc)
        # Discovery window already closed 25 hours ago (past the
        # default 24h validation window), so it's due for re-check.
        discovery_end = now - timedelta(hours=25)
        discovery_start = discovery_end - timedelta(hours=24)

        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db):
            init_temporal_patterns_db()
            con = _connect(temp_db)
            con.execute(
                """INSERT INTO temporal_patterns (
                    event_type_a, event_type_b, lag_window_minutes, status,
                    raw_count, expected_count_null, p_value, corrected_threshold,
                    num_comparisons_in_pass, discovery_window_start, discovery_window_end,
                    first_found_timestamp, last_checked_timestamp
                ) VALUES ('A', 'B', 30, 'candidate', 10, 1.0, 0.0001, 0.01, 5, ?, ?, ?, ?)""",
                (self._fmt(discovery_start), self._fmt(discovery_end), self._fmt(discovery_end), self._fmt(discovery_end)),
            )
            con.commit()
            con.close()

            # Seed the VALIDATION window (starting right at discovery_end)
            # with the same reliable A->B relationship repeating —
            # genuinely later, non-overlapping data.
            events = []
            for i in range(10):
                base = discovery_end + timedelta(hours=1 + i * 2)
                events.append(("A", self._fmt(base)))
                events.append(("B", self._fmt(base + timedelta(minutes=5))))
            self._seed_events(temp_db, events)

            with patch("app.config.settings.temporal_pattern_validation_window_hours", 24), \
                 patch("app.config.settings.temporal_pattern_min_occurrences", 3):
                _revalidate_due_candidates(now)

            con = _connect(temp_db)
            row = con.execute("SELECT status, validation_raw_count FROM temporal_patterns WHERE event_type_a = 'A'").fetchone()
            con.close()

        assert row[0] == "confirmed"
        assert row[1] == 10

    def test_pattern_that_does_not_replicate_is_marked_unconfirmed_not_deleted(self, tmp_path):
        from app.temporal_patterns import (
            init_temporal_patterns_db, _connect, _revalidate_due_candidates,
        )
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        now = datetime.now(timezone.utc)
        discovery_end = now - timedelta(hours=25)
        discovery_start = discovery_end - timedelta(hours=24)

        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db):
            init_temporal_patterns_db()
            con = _connect(temp_db)
            con.execute(
                """INSERT INTO temporal_patterns (
                    event_type_a, event_type_b, lag_window_minutes, status,
                    raw_count, expected_count_null, p_value, corrected_threshold,
                    num_comparisons_in_pass, discovery_window_start, discovery_window_end,
                    first_found_timestamp, last_checked_timestamp
                ) VALUES ('A', 'B', 30, 'candidate', 10, 1.0, 0.0001, 0.01, 5, ?, ?, ?, ?)""",
                (self._fmt(discovery_start), self._fmt(discovery_end), self._fmt(discovery_end), self._fmt(discovery_end)),
            )
            con.commit()
            con.close()

            # Validation window has NO real A->B relationship at all —
            # this candidate should fail to replicate.
            events = [("A", self._fmt(discovery_end + timedelta(hours=1)))]
            self._seed_events(temp_db, events)

            with patch("app.config.settings.temporal_pattern_validation_window_hours", 24), \
                 patch("app.config.settings.temporal_pattern_min_occurrences", 3):
                _revalidate_due_candidates(now)

            con = _connect(temp_db)
            row = con.execute("SELECT status FROM temporal_patterns WHERE event_type_a = 'A'").fetchone()
            con.close()

        # History is preserved as 'unconfirmed', never deleted — same
        # philosophy as adversarial self-testing's dismiss mechanism.
        assert row[0] == "unconfirmed"

    def test_candidate_not_yet_due_is_left_untouched(self, tmp_path):
        """A candidate whose discovery window closed only 1 hour ago
        (well short of the validation window) must not be re-checked
        yet — re-validating prematurely against an incomplete window
        would be a real, distinct correctness bug, not a stylistic
        choice."""
        from app.temporal_patterns import (
            init_temporal_patterns_db, _connect, _revalidate_due_candidates,
        )
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        now = datetime.now(timezone.utc)
        discovery_end = now - timedelta(hours=1)  # NOT yet due
        discovery_start = discovery_end - timedelta(hours=24)

        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db):
            init_temporal_patterns_db()
            con = _connect(temp_db)
            con.execute(
                """INSERT INTO temporal_patterns (
                    event_type_a, event_type_b, lag_window_minutes, status,
                    raw_count, expected_count_null, p_value, corrected_threshold,
                    num_comparisons_in_pass, discovery_window_start, discovery_window_end,
                    first_found_timestamp, last_checked_timestamp
                ) VALUES ('A', 'B', 30, 'candidate', 10, 1.0, 0.0001, 0.01, 5, ?, ?, ?, ?)""",
                (self._fmt(discovery_start), self._fmt(discovery_end), self._fmt(discovery_end), self._fmt(discovery_end)),
            )
            con.commit()
            con.close()

            with patch("app.config.settings.temporal_pattern_validation_window_hours", 24):
                _revalidate_due_candidates(now)

            con = _connect(temp_db)
            row = con.execute("SELECT status FROM temporal_patterns WHERE event_type_a = 'A'").fetchone()
            con.close()

        assert row[0] == "candidate"


class TestTemporalPatternSummary:
    """Tests for get_temporal_pattern_summary — the /health integration
    point. Confirms the distinct 'insufficient_data' status (design doc
    section 7 open question #1) is genuinely reachable and distinguishable
    from 'never_ran', 'disabled', and 'ok'."""

    def test_disabled_reports_disabled_status_only(self):
        from app.temporal_patterns import get_temporal_pattern_summary
        from unittest.mock import patch

        with patch("app.config.settings.temporal_pattern_detection_enabled", False):
            result = get_temporal_pattern_summary()
        assert result == {"status": "disabled"}

    def test_never_run_reports_never_ran(self, tmp_path):
        from app.temporal_patterns import init_temporal_patterns_db, get_temporal_pattern_summary
        from unittest.mock import patch

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.config.settings.temporal_pattern_detection_enabled", True):
            init_temporal_patterns_db()
            result = get_temporal_pattern_summary()
        assert result["status"] == "never_ran"

    def test_ran_with_few_events_reports_insufficient_data_not_ok(self, tmp_path):
        """The real, expected steady state for the first weeks of this
        feature's life: the job ran successfully, but the real event
        volume in the window was below the floor needed to consider
        ANY pair at all. Must be visibly distinct from a genuine,
        meaningful 'ok' / 'found nothing after a real amount of data'
        result."""
        from app.temporal_patterns import (
            init_temporal_patterns_db, run_temporal_pattern_mining_cycle, get_temporal_pattern_summary,
        )
        from unittest.mock import patch

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.temporal_patterns.run_event_extraction_cycle", return_value=0), \
             patch("app.config.settings.temporal_pattern_detection_enabled", True), \
             patch("app.config.settings.temporal_pattern_min_occurrences", 5):
            init_temporal_patterns_db()
            # Zero events in the DB at all -- well below the floor.
            run_temporal_pattern_mining_cycle()
            result = get_temporal_pattern_summary()

        assert result["status"] == "insufficient_data"

    def test_disabled_checked_inside_cycle_function_too(self):
        """Defense in depth — mirrors run_adversarial_test_cycle()'s
        own precedent exactly: a direct call to the cycle function must
        also respect the disabled switch, not just the scheduler
        registration step in main.py."""
        from app.temporal_patterns import run_temporal_pattern_mining_cycle
        from unittest.mock import patch

        with patch("app.config.settings.temporal_pattern_detection_enabled", False):
            result = run_temporal_pattern_mining_cycle()
        assert result["status"] == "disabled"


class TestEndpointsViaTestClient:
    """End-to-end tests through the real FastAPI app — confirms the
    enable switch actually changes real HTTP-level behavior, and that
    the literal correlation-not-causation disclaimer is genuinely
    present in the real HTTP response, not just at the data layer.
    Mirrors test_adversarial_testing.py's TestEndpointsViaTestClient
    class exactly — each test boots its own short-lived TestClient
    since the enabled/disabled state must be patched before the
    lifespan context manager runs.
    """

    def test_trigger_endpoint_runs_a_real_cycle(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        import app.main as main

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.main.settings.temporal_pattern_detection_enabled", True), \
             patch("app.temporal_patterns.run_event_extraction_cycle", return_value=0):
            with TestClient(main.app) as client:
                response = client.post("/temporal-patterns/trigger")
                assert response.status_code == 200
                body = response.json()
                assert body["status"] == "ran"
                assert "events_considered" in body
                assert "comparisons_run" in body

    def test_trigger_endpoint_reports_disabled_without_running(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        import app.main as main

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        extraction_called = {"n": 0}

        def tracking_extraction():
            extraction_called["n"] += 1
            return 0

        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.main.settings.temporal_pattern_detection_enabled", False), \
             patch("app.temporal_patterns.run_event_extraction_cycle", side_effect=tracking_extraction):
            with TestClient(main.app) as client:
                response = client.post("/temporal-patterns/trigger")
                assert response.status_code == 200
                assert response.json()["status"] == "disabled"
                assert extraction_called["n"] == 0

    def test_temporal_patterns_endpoint_reports_disabled_state(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        import app.main as main

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.main.settings.temporal_pattern_detection_enabled", False):
            with TestClient(main.app) as client:
                response = client.get("/temporal-patterns")
                assert response.status_code == 200
                assert response.json() == {"status": "disabled", "count": 0, "patterns": []}

    def test_health_reports_disabled_temporal_pattern_detection(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        import app.main as main

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.main.settings.temporal_pattern_detection_enabled", False):
            with TestClient(main.app) as client:
                response = client.get("/health")
                assert response.status_code == 200
                assert response.json()["temporal_pattern_detection"] == {"status": "disabled"}

    def test_temporal_patterns_endpoint_includes_disclaimer_on_every_row(self, tmp_path):
        """The actual, required HTTP-level check: every pattern row in
        the real JSON response carries the literal correlation-not-
        causation disclaimer, not just at the data layer tested
        elsewhere."""
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta
        import app.main as main
        import app.temporal_patterns as tp_module

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.main.settings.temporal_pattern_detection_enabled", True):
            tp_module.init_temporal_patterns_db()
            con = tp_module._connect(temp_db)
            now = datetime.now(timezone.utc)
            con.execute(
                """INSERT INTO temporal_patterns (
                    event_type_a, event_type_b, lag_window_minutes, status,
                    raw_count, expected_count_null, p_value, corrected_threshold,
                    num_comparisons_in_pass, discovery_window_start, discovery_window_end,
                    first_found_timestamp, last_checked_timestamp
                ) VALUES ('door.front:opened', 'motion.hallway:detected', 30, 'candidate',
                          10, 1.0, 0.0001, 0.01, 5, ?, ?, ?, ?)""",
                tuple(
                    (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    for h in (24, 0, 0, 0)
                ),
            )
            con.commit()
            con.close()

            with TestClient(main.app) as client:
                response = client.get("/temporal-patterns")
                assert response.status_code == 200
                body = response.json()
                assert body["count"] == 1
                assert body["patterns"][0]["note"] == (
                    "This reflects observed timing correlation only and does not establish a causal relationship."
                )

    def test_temporal_patterns_endpoint_filters_by_status(self, tmp_path):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta
        import app.main as main
        import app.temporal_patterns as tp_module

        temp_db = str(tmp_path / "test_temporal_patterns.db")
        with patch("app.temporal_patterns.TEMPORAL_PATTERNS_DB", temp_db), \
             patch("app.main.settings.temporal_pattern_detection_enabled", True):
            tp_module.init_temporal_patterns_db()
            con = tp_module._connect(temp_db)
            now = datetime.now(timezone.utc)
            for status, a in [("candidate", "A1"), ("confirmed", "A2"), ("unconfirmed", "A3")]:
                con.execute(
                    """INSERT INTO temporal_patterns (
                        event_type_a, event_type_b, lag_window_minutes, status,
                        raw_count, expected_count_null, p_value, corrected_threshold,
                        num_comparisons_in_pass, discovery_window_start, discovery_window_end,
                        first_found_timestamp, last_checked_timestamp
                    ) VALUES (?, 'B', 30, ?, 10, 1.0, 0.0001, 0.01, 5, ?, ?, ?, ?)""",
                    (a, status,
                     (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     now.strftime("%Y-%m-%dT%H:%M:%SZ")),
                )
            con.commit()
            con.close()

            with TestClient(main.app) as client:
                response = client.get("/temporal-patterns?status=confirmed")
                assert response.status_code == 200
                body = response.json()
                assert body["count"] == 1
                assert body["patterns"][0]["event_type_a"] == "A2"

    def test_backup_info_includes_temporal_patterns_db(self, tmp_path):
        """Confirms the design doc's explicit 'do not forget' item
        (§6.3) is actually wired in, not just remembered in a comment —
        the real /backup/info endpoint must report the new DB file
        alongside the other five."""
        from fastapi.testclient import TestClient
        import app.main as main

        with TestClient(main.app) as client:
            response = client.get("/backup/info")
            assert response.status_code == 200
            files = response.json()["files"]
            assert "temporal_patterns.db" in files

