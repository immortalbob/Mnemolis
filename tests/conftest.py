"""
Shared pytest configuration for the Mnemolis test suite.

The autouse fixture below exists because of a real, confirmed bug class
found via deliberately running the full suite under reversed and
randomized test collection order (rather than trusting the default,
fixed order pytest happens to use): app.router._cache and
app.router._routing_cache are plain module-level dicts, shared across
the entire test process. Several existing test classes — predating this
fixture, spread across test_router.py, test_routing_cache.py,
test_fusion.py, and test_cache_persistence.py — call clear_cache()/
clear_routing_cache() in their own setup_method (a clean START) but
never had a teardown_method to restore prior state afterward (no clean
END). Each one passes in isolation; the problem is only visible when a
DIFFERENT test, running later in the same process, depends on either
cache being empty and gets real, leftover entries instead.

This was masked for the entire life of this project so far because a
real cache.json/routing_cache.json file sitting on disk (left over from
any prior manual run, in any dev environment or CI runner that has ever
actually started the app for real) causes load_cache()/
load_routing_cache() to reset the in-memory dict to whatever the file
contains as a side effect of successfully parsing it. A genuinely clean
checkout — exactly what GitHub Actions' fresh runner is — has no such
file, so the early-return path in both load functions leaves whatever a
prior test left behind completely untouched, which is the actual,
confirmed mechanism behind a real "tests pass locally, fail in CI"
report.

Rather than hand-writing a save/clear/restore teardown_method into each
of the ~9 affected test classes individually (real, but a maintenance
burden that just reintroduces the same risk for the next test someone
adds without remembering to do the same), this autouse fixture applies
the fix once, for every test in the suite, including any written in the
future. A test that already manages this state correctly itself (most
do, with their own proper setup_method/teardown_method pairs) is
unaffected — this fixture's own save/restore is idempotent and simply a
second, redundant safety net in that case.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_router_module_caches():
    """Snapshot app.router._cache and _routing_cache before each test,
    restore them after — regardless of what the test itself does to
    either dict. Runs for every test in the suite automatically; no
    test needs to opt in.
    """
    import app.router as router_module

    original_cache = dict(router_module._cache)
    original_routing_cache = dict(router_module._routing_cache)

    yield

    router_module._cache.clear()
    router_module._cache.update(original_cache)
    router_module._routing_cache.clear()
    router_module._routing_cache.update(original_routing_cache)
