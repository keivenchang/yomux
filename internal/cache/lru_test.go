// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package cache

import (
	"slices"
	"testing"
)

func TestLRUKeysLeastRecentFirstTracksReads(t *testing.T) {
	cache := NewLRU[string, int](3)
	cache.Set("first", 1)
	cache.Set("second", 2)
	cache.Set("third", 3)
	if _, ok := cache.Get("first"); !ok {
		t.Fatal("cache miss for existing key")
	}
	if got, want := cache.KeysLeastRecentFirst(), []string{"second", "third", "first"}; !slices.Equal(got, want) {
		t.Fatalf("least-recent-first keys = %#v, want %#v", got, want)
	}
}

func TestLRUPeekDoesNotRefreshRecency(t *testing.T) {
	cache := NewLRU[string, int](2)
	cache.Set("first", 1)
	cache.Set("second", 2)
	if value, ok := cache.Peek("first"); !ok || value != 1 {
		t.Fatalf("peek first = %d, %t", value, ok)
	}
	if got, want := cache.KeysLeastRecentFirst(), []string{"first", "second"}; !slices.Equal(got, want) {
		t.Fatalf("peek changed least-recent-first order = %#v, want %#v", got, want)
	}
}
