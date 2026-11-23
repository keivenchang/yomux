// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package finder

import (
	"crypto/rand"
	"io"
	"net/http"
	"os"
	"time"
	"unicode/utf8"
)

const reviewFileLimit = 1 << 20
const reviewSnapshotLimit = 32
const reviewMemoryLimit = 64 << 20

func (s *Service) reviewTimeLocked() time.Time {
	if s.reviewClock != nil {
		return s.reviewClock()
	}
	return time.Now()
}

type reviewVersion struct {
	at   time.Time
	text string
}
type fileReview struct {
	expiresAt            time.Time
	id, client, tracking string
	baseline             reviewVersion
	versions             []reviewVersion
	earliest             time.Time
	problem              string
}

// readReviewText uses the same authorized path owner as ordinary file reads.
// Metadata must survive the read; a partial/truncated write is not a verified version.
func (s *Service) readReviewText(entry *openFileRecord) (string, *Error) {
	file, _, _, openErr := s.openAny(entry.path)
	if openErr != nil {
		return "", openErr
	}
	defer file.Close()
	before, err := file.Stat()
	if err != nil {
		return "", filesystemError(entry.path, err)
	}
	if !before.Mode().IsRegular() || before.Size() > reviewFileLimit {
		return "", invalid(entry.path, "Recent changes supports text files up to 1 MiB")
	}
	data, err := io.ReadAll(io.LimitReader(file, reviewFileLimit+1))
	if err != nil {
		return "", filesystemError(entry.path, err)
	}
	after, err := file.Stat()
	if err != nil {
		return "", filesystemError(entry.path, err)
	}
	if !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) || int64(len(data)) != after.Size() {
		return "", invalid(entry.path, "File changed while reading; waiting for a stable version")
	}
	if len(data) > reviewFileLimit || !utf8.Valid(data) {
		return "", invalid(entry.path, "Recent changes requires UTF-8 text up to 1 MiB")
	}
	for _, b := range data {
		if b == 0 {
			return "", invalid(entry.path, "Recent changes requires text")
		}
	}
	current, _, _, pathErr := s.openAny(entry.path)
	if pathErr != nil {
		return "", pathErr
	}
	currentInfo, statErr := current.Stat()
	current.Close()
	if statErr != nil {
		return "", filesystemError(entry.path, statErr)
	}
	if !os.SameFile(after, currentInfo) || after.Size() != currentInfo.Size() || !after.ModTime().Equal(currentInfo.ModTime()) {
		return "", invalid(entry.path, "File was replaced while reading; waiting for a stable version")
	}
	if entry.fileID != fileID(after) {
		replacement, copyErr := copyDescriptor(file)
		if copyErr != nil {
			return "", filesystemError(entry.path, copyErr)
		}
		entry.file.Close()
		entry.file = replacement
		entry.fileID = fileID(after)
		entry.pathAmbiguous = false
		s.observeOpenFileIdentityLocked(after, nil)
	}
	return string(data), nil
}

func (s *Service) reviewBytesLocked() int {
	total := 0
	for _, r := range s.reviews {
		total += len(r.baseline.text)
		for _, v := range r.versions {
			total += len(v.text)
		}
	}
	return total
}

func trimReviewFirst(r *fileReview) bool {
	if len(r.versions) == 0 {
		return false
	}
	r.earliest = r.versions[0].at
	r.versions[0] = reviewVersion{}
	r.versions = r.versions[1:]
	if len(r.versions) > 0 {
		r.earliest = r.versions[0].at
	}
	return true
}

func (s *Service) trimReviewsLocked(extra int) bool {
	for s.reviewBytesLocked()+extra > reviewMemoryLimit {
		var oldest *fileReview
		for _, r := range s.reviews {
			if len(r.versions) > 0 && (oldest == nil || r.versions[0].at.Before(oldest.versions[0].at)) {
				oldest = r
			}
		}
		if oldest == nil {
			return false
		}
		trimReviewFirst(oldest)
	}
	return true
}

func (s *Service) observeReviewsLocked(now time.Time) {
	texts := map[string]string{}
	problems := map[string]*Error{}
	for key, r := range s.reviews {
		watch, live := s.watches[r.client]
		entry := s.openFiles[r.tracking]
		if live && watch.OpenFiles[r.tracking] != "" {
			r.expiresAt = watch.ExpiresAt
		}
		if !r.expiresAt.After(now) || entry == nil {
			delete(s.reviews, key)
			continue
		}
		text, read := texts[r.tracking]
		problem, failed := problems[r.tracking]
		if !read && !failed {
			text, problem = s.readReviewText(entry)
			if problem == nil {
				texts[r.tracking] = text
			} else {
				problems[r.tracking] = problem
			}
		}
		if problem != nil {
			r.problem = problem.Message
			continue
		}
		r.problem = ""
		cutoff := now.Add(-30 * time.Minute)
		for len(r.versions) > 1 && !r.versions[1].at.After(cutoff) {
			trimReviewFirst(r)
		}
		last := r.baseline.text
		if len(r.versions) > 0 {
			last = r.versions[len(r.versions)-1].text
		}
		if text == last {
			continue
		}
		for len(r.versions) >= reviewSnapshotLimit {
			trimReviewFirst(r)
		}
		if !s.trimReviewsLocked(len(text)) {
			r.problem = "Recent changes history is full; some observations are unavailable"
			r.earliest = now
			continue
		}
		if len(r.versions) == 0 && r.earliest.After(r.baseline.at) {
			r.earliest = now
		}
		r.versions = append(r.versions, reviewVersion{now, text})
	}
}

// Review requires a live browser watch and an opaque review capability. Content
// is never included in the globally published filesystem event stream.
func (s *Service) Review(payload map[string]any) (map[string]any, *Error) {
	s.openFileMutationMu.Lock()
	defer s.openFileMutationMu.Unlock()
	s.reconcileOpenFilesWithinMutation(nil)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.reviews == nil {
		s.reviews = map[string]*fileReview{}
	}
	now := s.reviewTimeLocked()
	client, _ := payload["client_id"].(string)
	tracking, _ := payload["tracking_id"].(string)
	action, _ := payload["action"].(string)
	id, _ := payload["review_id"].(string)
	minutes := float64(0)
	if raw, exists := payload["minutes"]; exists {
		var ok bool
		minutes, ok = raw.(float64)
		if !ok || minutes < 0 || minutes > 30 || minutes != float64(int(minutes)) {
			return nil, invalid("", "Review range must be 0 to 30 minutes")
		}
	}
	if action != "start" && action != "reset" && action != "read" && action != "end" {
		return nil, invalid("", "Unknown review action")
	}

	watch, live := s.watches[client]
	entry := s.openFiles[tracking]
	if !live || !watch.ExpiresAt.After(now) || watch.OpenFiles[tracking] == "" || entry == nil {
		return nil, &Error{Status: http.StatusGone, Code: "review_ended", Message: "Recent changes requires a live open-file watch; start a new review"}
	}
	r := s.reviews[id]
	if action == "start" || action == "reset" {
		if action == "reset" && (r == nil || r.client != client || r.tracking != tracking) {
			return nil, &Error{Status: http.StatusGone, Code: "review_ended", Message: "Review ended; start a new review"}
		}
		text, err := s.readReviewText(entry)
		if err != nil {
			return nil, err
		}
		// A failed admission leaves the old fixed baseline intact.
		extra := len(text)
		for _, previous := range s.reviews {
			if previous.client == client && previous.tracking == tracking {
				extra -= len(previous.baseline.text)
			}
		}
		if !s.trimReviewsLocked(extra) {
			return nil, &Error{Status: http.StatusTooManyRequests, Code: "review_capacity_exceeded", Message: "Recent changes is unavailable: history memory is full"}
		}
		for previousID, previous := range s.reviews {
			if previous.client == client && previous.tracking == tracking {
				delete(s.reviews, previousID)
			}
		}
		r = &fileReview{expiresAt: watch.ExpiresAt, id: rand.Text(), client: client, tracking: tracking, baseline: reviewVersion{now, text}, earliest: now}
		s.reviews[r.id] = r
	} else if r == nil || r.client != client || r.tracking != tracking {
		return nil, &Error{Status: http.StatusGone, Code: "review_ended", Message: "Review ended or server restarted; start a new review"}
	}
	if action == "end" {
		delete(s.reviews, r.id)
		return map[string]any{"ok": true}, nil
	}
	s.observeReviewsLocked(now)
	baseline := r.baseline
	complete := true
	if minutes > 0 {
		cutoff := now.Add(-time.Duration(minutes) * time.Minute)
		complete = (!cutoff.Before(r.earliest) || !r.earliest.After(r.baseline.at)) && !(len(r.versions) == 0 && r.earliest.After(r.baseline.at))
		for _, v := range r.versions {
			if !v.at.After(cutoff) {
				baseline = v
			} else {
				break
			}
		}
		if !complete && len(r.versions) > 0 {
			baseline = r.versions[0]
		}
	}
	var original any = baseline.text
	if minutes > 0 && !complete && len(r.versions) == 0 {
		original = nil
	}
	return map[string]any{"ok": true, "review_id": r.id, "tracking_id": tracking, "path": entry.path, "original": original, "started_at": r.baseline.at.UnixMilli(), "earliest_at": r.earliest.UnixMilli(), "complete": complete, "problem": r.problem}, nil
}
