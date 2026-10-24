// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package main

import (
	"fmt"
	"slices"
	"testing"
)

func (s *eventSubscription) receive() clientEvent {
	for {
		if event, ok := s.dequeue(); ok {
			return event
		}
		if _, open := <-s.ready; !open {
			event, _ := s.dequeue()
			return event
		}
	}
}

func (s *eventSubscription) length() int {
	s.broker.mu.Lock()
	defer s.broker.mu.Unlock()
	return len(s.queue)
}

func TestEventSubscriptionCoalescingAndDequeueShareQueueOwner(t *testing.T) {
	broker := newEventBroker()
	stream, unsubscribe := broker.subscribe()
	defer unsubscribe()
	broker.mu.Lock()
	stream.enqueueLocked(clientEvent{ID: 1, Type: "settings_changed", Resource: "settings_changed"})
	stream.enqueueLocked(clientEvent{ID: 2, Type: "files_changed", Resource: "files_changed"})
	stream.enqueueLocked(clientEvent{ID: 3, Type: "files_changed", Resource: "files_changed"})
	started := make(chan struct{})
	received := make(chan clientEvent, 1)
	go func() { close(started); received <- stream.receive() }()
	<-started
	// The queue remains wholly owned while replacement runs; readiness carries no payload.
	if !broker.replaceQueuedResource(stream, "settings_changed", clientEvent{ID: 4, Type: "settings_changed", Resource: "settings_changed"}) {
		broker.mu.Unlock()
		t.Fatal("queued snapshot was not replaced")
	}
	ids := []int{}
	for _, event := range stream.queue {
		ids = append(ids, event.ID)
	}
	broker.mu.Unlock()
	if !slices.Equal(ids, []int{2, 3, 4}) {
		t.Fatalf("replacement order = %v", ids)
	}
	first := <-received
	second, third := stream.receive(), stream.receive()
	if !slices.Equal([]int{first.ID, second.ID, third.ID}, []int{2, 3, 4}) {
		t.Fatalf("dequeue escaped replacement owner: %+v %+v %+v", first, second, third)
	}
	select {
	case <-stream.ready:
		t.Fatal("empty queue retained readiness")
	default:
	}
}

func TestEventSubscriptionConcurrentReplacementPreservesOrderedEvents(t *testing.T) {
	for _, effects := range []int{1, 2, 32, 200} {
		t.Run(fmt.Sprint(effects), func(t *testing.T) {
			for round := 0; round < 8; round++ {
				broker := newEventBroker()
				stream, unsubscribe := broker.subscribe()
				// Guarantee coalescing is exercised independently of scheduler speed.
				for n := 0; n < 2; n++ {
					if _, err := broker.publish("settings_changed", map[string]any{"n": n}); err != nil {
						t.Fatal(err)
					}
				}
				finished := make(chan error, 1)
				go func() {
					for n := 0; n < effects; n++ {
						for snapshot := 0; snapshot < 8; snapshot++ {
							if _, err := broker.publish("settings_changed", map[string]any{"n": n, "snapshot": snapshot}); err != nil {
								finished <- err
								return
							}
						}
						if _, err := broker.publish("files_changed", map[string]any{"n": n}); err != nil {
							finished <- err
							return
						}
					}
					_, err := broker.publish("stats_sample", map[string]any{"done": true})
					finished <- err
				}()
				lastID, lastEffect, count := 0, -1, 0
				for {
					event := broker.decorateEvent(stream, stream.receive())
					if event.ID <= lastID {
						t.Fatalf("round%d subscriber order reversed: %d then%d", round, lastID, event.ID)
					}
					lastID = event.ID
					if event.Type == "files_changed" {
						n := event.Payload["n"].(int)
						if n != lastEffect+1 {
							t.Fatalf("round%d lost/reordered effect: %d then%d", round, lastEffect, n)
						}
						lastEffect = n
						count++
					}
					if event.Type == "stats_sample" {
						break
					}
				}
				if err := <-finished; err != nil {
					t.Fatal(err)
				}
				if count != effects || broker.dropped != 0 || broker.coalesced < 1 {
					t.Fatalf("round%d effects=%d want%d dropped=%d coalesced=%d", round, count, effects, broker.dropped, broker.coalesced)
				}
				unsubscribe()
			}
		})
	}
}

func TestEventSubscriptionRetirementKeepsAdmittedEventsAndClearsReadiness(t *testing.T) {
	broker := newEventBroker()
	stream, unsubscribe := broker.subscribe()
	event, err := broker.publish("files_changed", map[string]any{"path": "admitted"})
	if err != nil {
		t.Fatal(err)
	}
	unsubscribe()
	if got := stream.receive(); got.ID != event.ID {
		t.Fatalf("retirement discarded admitted event: %+v", got)
	}
	if got := stream.receive(); got.ID != 0 {
		t.Fatalf("closed queue returned event: %+v", got)
	}
}
