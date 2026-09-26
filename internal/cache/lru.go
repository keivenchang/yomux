// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

// Package cache contains small, owner-synchronized cache primitives.
package cache

import "container/list"

type entry[K comparable, V any] struct {
	key   K
	value V
}

// LRU is a bounded least-recently-used cache. LRU does not synchronize access;
// callers that share one instance must keep their existing owner mutex around
// Get, Set, and Delete. A successful Get refreshes recency.
type LRU[K comparable, V any] struct {
	capacity int
	items    map[K]*list.Element
	order    *list.List
}

func NewLRU[K comparable, V any](capacity int) *LRU[K, V] {
	if capacity < 0 {
		capacity = 0
	}
	return &LRU[K, V]{capacity: capacity, items: make(map[K]*list.Element), order: list.New()}
}

func (c *LRU[K, V]) Get(key K) (V, bool) {
	if c == nil || c.order == nil {
		var zero V
		return zero, false
	}
	element, ok := c.items[key]
	if !ok {
		var zero V
		return zero, false
	}
	c.order.MoveToFront(element)
	return element.Value.(entry[K, V]).value, true
}

// Peek returns a value without changing its recency.
func (c *LRU[K, V]) Peek(key K) (V, bool) {
	if c == nil || c.order == nil {
		var zero V
		return zero, false
	}
	element, ok := c.items[key]
	if !ok {
		var zero V
		return zero, false
	}
	return element.Value.(entry[K, V]).value, true
}

func (c *LRU[K, V]) Set(key K, value V) {
	if c == nil || c.capacity == 0 {
		return
	}
	if element, ok := c.items[key]; ok {
		element.Value = entry[K, V]{key: key, value: value}
		c.order.MoveToFront(element)
		return
	}
	element := c.order.PushFront(entry[K, V]{key: key, value: value})
	c.items[key] = element
	for c.order.Len() > c.capacity {
		oldest := c.order.Back()
		if oldest == nil {
			break
		}
		oldestEntry := oldest.Value.(entry[K, V])
		delete(c.items, oldestEntry.key)
		c.order.Remove(oldest)
	}
}

func (c *LRU[K, V]) Delete(key K) bool {
	if c == nil || c.order == nil {
		return false
	}
	element, ok := c.items[key]
	if !ok {
		return false
	}
	delete(c.items, key)
	c.order.Remove(element)
	return true
}

func (c *LRU[K, V]) Len() int {
	if c == nil || c.order == nil {
		return 0
	}
	return c.order.Len()
}

func (c *LRU[K, V]) KeysLeastRecentFirst() []K {
	if c == nil || c.order == nil {
		return nil
	}
	keys := make([]K, 0, c.order.Len())
	for element := c.order.Back(); element != nil; element = element.Prev() {
		keys = append(keys, element.Value.(entry[K, V]).key)
	}
	return keys
}
