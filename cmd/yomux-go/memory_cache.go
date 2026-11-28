// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"os"
	"sync"
)

// memoryCacheDefaultLimitBytes is the performance.static_cache_mb default.
const memoryCacheDefaultLimitBytes = 64 << 20

// Kinds of memoryCache entries; each has its own counters.
const (
	memoryCacheGzip   = "gzip"
	memoryCacheTeX    = "tex"
	memoryCacheOffice = "office"
)

// memoryCache is the one server memory budget for bytes that are expensive to produce again:
// gzip-compressed app files and compiled TeX and Office previews. Every kind shares the
// performance.static_cache_mb budget and one least-recently-used order.
type memoryCache struct {
	mu         sync.Mutex
	entries    map[string]*memoryCacheEntry
	bytes      int64
	limit      int64
	configured bool
	clock      uint64
	stats      map[string]*memoryCacheStats
}

type memoryCacheEntry struct {
	kind string
	// info is the file version a gzip entry was compressed from.
	info os.FileInfo
	once sync.Once
	data []byte
	// meta is kept with a preview's bytes, such as the TeX source map and inputs.
	meta   any
	size   int64
	err    error
	used   uint64
	stored bool
}

type memoryCacheStats struct {
	Hits      uint64 `json:"hits"`
	Misses    uint64 `json:"misses"`
	Evictions uint64 `json:"evictions"`
	// Uncacheable counts finished results that were not stored, such as a compile whose
	// inputs the compiler could not report.
	Uncacheable uint64 `json:"uncacheable"`
	Bytes       int64  `json:"bytes"`
	Entries     int    `json:"entries"`
}

// statsLocked returns the counters of kind, creating them on first use.
func (c *memoryCache) statsLocked(kind string) *memoryCacheStats {
	if c.stats == nil {
		c.stats = map[string]*memoryCacheStats{}
	}
	if c.stats[kind] == nil {
		c.stats[kind] = &memoryCacheStats{}
	}
	return c.stats[kind]
}

// count records a lookup whose outcome the caller decided, such as a TeX entry whose inputs changed.
func (c *memoryCache) count(kind string, hit bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if hit {
		c.statsLocked(kind).Hits++
	} else {
		c.statsLocked(kind).Misses++
	}
}

// snapshot reports the budget and the counters by kind for diagnostics.
func (c *memoryCache) snapshot() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	kinds := map[string]memoryCacheStats{}
	for _, kind := range []string{memoryCacheGzip, memoryCacheTeX, memoryCacheOffice} {
		kinds[kind] = *c.statsLocked(kind)
	}
	return map[string]any{"limit_bytes": c.limitLocked(), "bytes": c.bytes, "kinds": kinds}
}

// loadGzip returns the compressed bytes of exactly the file version described by info. A warm-up
// load is kept only when it fits without dropping something that was already cached.
func (c *memoryCache) loadGzip(path string, info os.FileInfo, warm bool) ([]byte, error) {
	c.mu.Lock()
	entry := c.entries[path]
	hit := entry != nil && entry.kind == memoryCacheGzip && sameStaticVersion(entry.info, info)
	if !hit {
		if entry != nil {
			c.dropLocked(path, entry)
		}
		entry = &memoryCacheEntry{kind: memoryCacheGzip, info: info}
		if c.entries == nil {
			c.entries = map[string]*memoryCacheEntry{}
		}
		c.entries[path] = entry
	}
	if !warm {
		if hit {
			c.statsLocked(memoryCacheGzip).Hits++
		} else {
			c.statsLocked(memoryCacheGzip).Misses++
		}
	}
	c.clock++
	entry.used = c.clock
	c.mu.Unlock()
	entry.once.Do(func() {
		entry.data, entry.err = gzipStaticFile(path, info)
		entry.size = int64(len(entry.data))
		c.admit(path, entry, warm)
	})
	return entry.data, entry.err
}

// get returns a stored entry's bytes and metadata and marks it recently used. It does not count
// the lookup, because the caller may still find the entry out of date.
func (c *memoryCache) get(key string) ([]byte, any, *memoryCacheEntry, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry := c.entries[key]
	if entry == nil || !entry.stored {
		return nil, nil, nil, false
	}
	c.clock++
	entry.used = c.clock
	return entry.data, entry.meta, entry, true
}

// put stores finished bytes under key, charging size to the shared budget. An entry larger than
// the whole budget is not kept.
func (c *memoryCache) put(kind, key string, data []byte, meta any, size int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if previous := c.entries[key]; previous != nil {
		c.dropLocked(key, previous)
	}
	if c.entries == nil {
		c.entries = map[string]*memoryCacheEntry{}
	}
	c.clock++
	entry := &memoryCacheEntry{kind: kind, data: data, meta: meta, size: size, used: c.clock}
	c.entries[key] = entry
	c.admitLocked(key, entry, false)
}

// remove drops key, for example when a TeX entry's inputs changed.
func (c *memoryCache) removeIfSame(key string, expected *memoryCacheEntry) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if entry := c.entries[key]; entry != nil && entry == expected {
		c.dropLocked(key, entry)
	}
}

func (c *memoryCache) admit(key string, entry *memoryCacheEntry, warm bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.admitLocked(key, entry, warm)
}

func (c *memoryCache) admitLocked(key string, entry *memoryCacheEntry, warm bool) {
	if c.entries[key] != entry {
		return
	}
	limit := c.limitLocked()
	if entry.err != nil || entry.size > limit || (warm && c.bytes+entry.size > limit) {
		delete(c.entries, key)
		return
	}
	entry.stored = true
	c.bytes += entry.size
	stats := c.statsLocked(entry.kind)
	stats.Bytes += entry.size
	stats.Entries++
	c.evictLocked(entry)
}

func (c *memoryCache) setLimit(limit int64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.limit, c.configured = limit, true
	c.evictLocked(nil)
}

func (c *memoryCache) limitLocked() int64 {
	if !c.configured {
		return memoryCacheDefaultLimitBytes
	}
	return c.limit
}

// evictLocked drops the least recently used entries of every kind until the budget holds.
func (c *memoryCache) evictLocked(keep *memoryCacheEntry) {
	for c.bytes > c.limitLocked() {
		var oldestKey string
		var oldest *memoryCacheEntry
		for key, entry := range c.entries {
			if entry.stored && entry != keep && (oldest == nil || entry.used < oldest.used) {
				oldestKey, oldest = key, entry
			}
		}
		if oldest == nil {
			return
		}
		c.statsLocked(oldest.kind).Evictions++
		c.dropLocked(oldestKey, oldest)
	}
}

func (c *memoryCache) dropLocked(key string, entry *memoryCacheEntry) {
	if entry.stored {
		c.bytes -= entry.size
		stats := c.statsLocked(entry.kind)
		stats.Bytes -= entry.size
		stats.Entries--
		entry.stored = false
	}
	delete(c.entries, key)
}

// skip counts a result that could not be cached, such as a compile whose inputs are unknown.
func (c *memoryCache) skip(kind string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.statsLocked(kind).Uncacheable++
}
