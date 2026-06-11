"""the one shared TtlCache. metadata.MetadataCache and the sessions transcript-lookup cache
route through it now. These pin the parent's contract so the consumers can rely on it."""

from yolomux_lib import cache
from yolomux_lib.cache import MISS
from yolomux_lib.cache import TtlCache


def test_get_or_miss_distinguishes_cached_none_from_absent():
    c = TtlCache(ttl_seconds=100)
    assert c.get_or_miss("absent") is MISS
    c.set("present", None)
    assert c.get_or_miss("present") is None  # a cached None must NOT read as a miss
    assert c.get("present", "default") is None


def test_injectable_clock_drives_expiry():
    now = [1000.0]
    c = TtlCache(ttl_seconds=5, clock=lambda: now[0])
    c.set("k", "v")
    assert c.get("k") == "v"
    now[0] += 4.0
    assert c.get("k") == "v"            # within the 5s TTL
    now[0] += 2.0
    assert c.get_or_miss("k") is MISS   # expired


def test_bounded_eviction_caps_size():
    c = TtlCache(ttl_seconds=300, max_entries=64)
    for i in range(200):
        c.set(f"k{i}", i)
    assert len(c.values) <= 64


def test_common_cache_miss_is_the_shared_sentinel():
    # metadata.py and sessions.py compare against common._CACHE_MISS; it must be the same object TtlCache
    # returns, or those comparisons silently never match after the B3 migration.
    from yolomux_lib import common
    assert common._CACHE_MISS is cache.MISS
