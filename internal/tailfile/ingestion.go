// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package tailfile

import (
	"bufio"
	"bytes"
	"container/list"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"reflect"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const ingestionPageBytes = 64 << 10
const ingestionCachePages = 256
const ingestionValidationReserveBytes = 256 << 10

// Record caches (decoded objects, complete frames, unfinished frames) share
// these bounds. Decoded objects are charged by approximate heap size.
const maxCachedTranscriptBytes = 16 << 20
const maxCachedTranscriptRecords = 4096
const maxCachedPartialFrames = 256

// File generations are tiny; the bound only stops unbounded growth when many
// archived transcripts pass through one long-lived server.
const maxTrackedFileGenerations = 4096

// generationSampleBytes matches the Position prefix/tail samples consumers
// already trust to resume an appended transcript.
const generationSampleBytes = 4096

// MaxRecordBytes bounds a complete frame, including its line terminator.
const MaxRecordBytes = 8 << 20

// CompleteRecordBatchBytes admits a maximum-size record or the first excess
// byte, plus validation/page overhead. Smaller API budgets remain supported,
// but a changing snapshot cannot safely reuse an unfinished prior read.
const CompleteRecordBatchBytes = MaxRecordBytes + ingestionValidationReserveBytes + 1

// Ingestion is instance-local. A snapshot page is physically read once for
// overlapping consumers, even if their durable transactions complete separately.
// Bounded eviction permits measured recovery rereads rather than retaining logs.
type Ingestion struct {
	projectionMu   sync.Mutex
	decoded        boundedCache[[sha256.Size]byte, *decodedRecord]
	tickOnce       sync.Once
	subscribers    map[chan time.Time]bool
	frameMu        sync.Mutex
	mu             sync.Mutex
	partial        boundedCache[string, partialFrame]
	pages          boundedCache[pageKey, []byte]
	frames         boundedCache[frameKey, []byte]
	files          boundedCache[string, *fileGeneration]
	nextGeneration uint64
	BytesRead      int64
	CacheHits      int64
}

// Projection is a stateless consumer of a decoded transcript record. The JSON
// object and projected values are shared. Treat both as read-only and keep
// source-specific state in the consumer checkpoint. RegisterProjection only
// allocates the handle; the handle's identity keys its memoized value on each
// cached record, so create one at package scope for each record product.
type Projection[T any] struct {
	project func([]byte, map[string]any) T
}

func RegisterProjection[T any](project func([]byte, map[string]any) T) *Projection[T] {
	return &Projection[T]{project: project}
}

type decodedRecord struct {
	once        sync.Once
	object      map[string]any
	mu          sync.Mutex
	projections map[any]any
	size        int64
}

// Decode returns the shared decoded JSON object for one record, or nil when the
// record is not a JSON object. The object is read-only. A nil Ingestion and
// records above MaxRecordBytes decode without caching.
func (o *Ingestion) Decode(line []byte) map[string]any {
	line = bytes.TrimSuffix(line, []byte{'\n'})
	if o == nil || len(line) > MaxRecordBytes {
		return decodeTranscriptObject(line)
	}
	return o.decodedRecord(line).object
}

// Read projects a record without decoding its JSON again for each consumer.
// The content key allows append-only snapshots and tail readers to share the
// same record. The cache is volatile and bounded; retries after eviction or
// restart can decode a record again without coupling consumer checkpoints.
func (p *Projection[T]) Read(ingestion *Ingestion, line []byte) T {
	line = bytes.TrimSuffix(line, []byte{'\n'})
	if ingestion == nil || len(line) > MaxRecordBytes {
		return p.project(line, decodeTranscriptObject(line))
	}
	record := ingestion.decodedRecord(line)
	record.mu.Lock()
	defer record.mu.Unlock()
	if value, ok := record.projections[p]; ok {
		return value.(projectionValue[T]).value
	}
	value := p.project(line, record.object)
	// Unknown retained costs (for example a closure's captures) are never memoized.
	// An evicted record remains usable by its reader but must not be resurrected.
	size, known := retainedProjectionBytes(reflect.ValueOf(value), 0)
	if !known || size > maxCachedTranscriptBytes-record.size-64 {
		return value
	}
	ingestion.projectionMu.Lock()
	defer ingestion.projectionMu.Unlock()
	key := sha256.Sum256(line)
	if current, ok := ingestion.decoded.get(key); !ok || current != record {
		return value
	}
	if record.projections == nil {
		record.projections = make(map[any]any)
	}
	record.projections[p] = projectionValue[T]{value}
	record.size += size + 64
	ingestion.decoded.put(key, record, record.size, maxCachedTranscriptRecords, maxCachedTranscriptBytes)
	return value
}

type projectionValue[T any] struct{ value T }

// decodedRecord returns the decoded cache entry for line. The entry is
// published before decoding so concurrent consumers wait on one decode; it is
// charged by line length until the decoded size is known.
func (o *Ingestion) decodedRecord(line []byte) *decodedRecord {
	key := sha256.Sum256(line)
	o.projectionMu.Lock()
	record, ok := o.decoded.get(key)
	if !ok {
		record = &decodedRecord{}
		o.decoded.put(key, record, int64(len(line)), maxCachedTranscriptRecords, maxCachedTranscriptBytes)
	}
	o.projectionMu.Unlock()
	record.once.Do(func() {
		record.object = decodeTranscriptObject(line)
		size := decodedRecordOverheadBytes + decodedBytes(record.object)
		record.size = size
		o.projectionMu.Lock()
		if current, ok := o.decoded.get(key); ok && current == record {
			o.decoded.put(key, record, size, maxCachedTranscriptRecords, maxCachedTranscriptBytes)
		}
		o.projectionMu.Unlock()
	})
	return record
}

// decodedRecordOverheadBytes covers one cache entry, its record, and its key.
const decodedRecordOverheadBytes = 256

// decodedBytes approximates the heap retained by a decoded JSON value, so the
// decoded-record bound measures objects rather than their source lines. The
// per-node constants (map slots, string headers, allocation size classes)
// were calibrated against runtime heap growth on a 60 MB Codex rollout, where
// the estimate is within 5% of the measured heap.
func decodedBytes(value any) int64 {
	switch value := value.(type) {
	case map[string]any:
		size := int64(64)
		for key, item := range value {
			size += 32 + decodedStringBytes(key) + decodedBytes(item)
		}
		return size
	case []any:
		size := int64(24 + 16*len(value))
		for _, item := range value {
			size += decodedBytes(item)
		}
		return size
	case string:
		return decodedStringBytes(value)
	default:
		return 8
	}
}

func decodedStringBytes(value string) int64 {
	return 24 + int64(len(value)+len(value)/16)
}

// retainedProjectionBytes conservatively counts container storage and reachable
// values. Aliases may be counted twice; unknown kinds and cyclic/deep values
// skip memoization rather than admitting an uncharged allocation.
func retainedProjectionBytes(value reflect.Value, depth int) (int64, bool) {
	if !value.IsValid() {
		return 0, true
	}
	if depth > 64 {
		return 0, false
	}
	size := int64(value.Type().Size())
	add := func(child reflect.Value) bool {
		amount, known := retainedProjectionBytes(child, depth+1)
		size += amount
		return known
	}
	switch value.Kind() {
	case reflect.String:
		size += int64(value.Len())
	case reflect.Interface, reflect.Pointer:
		if !value.IsNil() && !add(value.Elem()) {
			return 0, false
		}
	case reflect.Slice, reflect.Array:
		if value.Kind() == reflect.Slice {
			size += int64(value.Cap()) * int64(value.Type().Elem().Size())
		}
		for index := 0; index < value.Len(); index++ {
			child := value.Index(index)
			if !add(child) {
				return 0, false
			}
			size -= int64(child.Type().Size())
			if size > maxCachedTranscriptBytes {
				return size, true
			}
		}
	case reflect.Map:
		size += 64
		entries := value.MapRange()
		for entries.Next() {
			size += 32
			if !add(entries.Key()) || !add(entries.Value()) {
				return 0, false
			}
			if size > maxCachedTranscriptBytes {
				return size, true
			}
		}
	case reflect.Struct:
		for index := 0; index < value.NumField(); index++ {
			child := value.Field(index)
			if !add(child) {
				return 0, false
			}
			size -= int64(child.Type().Size())
		}
	case reflect.Bool, reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64,
		reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr,
		reflect.Float32, reflect.Float64, reflect.Complex64, reflect.Complex128:
	default:
		return 0, false
	}
	return size, true
}

func decodeTranscriptObject(line []byte) map[string]any {
	var record map[string]any
	if json.Unmarshal(line, &record) != nil {
		return nil
	}
	return record
}

// Cached pages and complete frames are keyed by stable file identity and
// generation rather than by snapshot, so appends do not invalidate complete
// records that were already read. Unfinished frames stay keyed by snapshot.
type pageKey struct {
	file       string
	generation uint64
	page       int64
}

// fileGeneration describes the latest snapshot of one file generation.
// Cached pages and frames of a generation hold that snapshot's bytes. boundary
// is the end of the last complete (LF-terminated) record seen in those bytes;
// bytes after it may belong to an unfinished record that the writer can still
// rewrite, so they are never carried into the next snapshot.
type fileGeneration struct {
	id       uint64
	snapshot string
	size     int64
	boundary int64
}

type Snapshot struct {
	batchPages     map[int64][]byte
	physicalBytes  atomic.Int64
	owner          *Ingestion
	file           *os.File
	info           os.FileInfo
	path, identity string
	fileID         string
	generation     uint64
}

func (o *Ingestion) Snapshot(file *os.File) (*Snapshot, error) {
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("transcript is not a regular file")
	}
	// Sys includes ctime on supported platforms; size/mtime alone do not detect
	// same-size rewrites whose mtime was restored by the writer.
	snapshot := &Snapshot{owner: o, file: file, info: info, path: file.Name(), identity: snapshotIdentity(info), fileID: stableFileIdentity(info)}
	o.mu.Lock()
	defer o.mu.Unlock()
	if snapshot.generation, err = snapshot.validateGeneration(); err != nil {
		return nil, err
	}
	return snapshot, nil
}

// validateGeneration lets a changed snapshot keep its generation's cached
// complete records when the file grew and the first and last sampled bytes
// before the previous complete-record boundary still equal the cached bytes.
// That is the same evidence consumer positions use to resume after an append;
// a rewrite confined to the middle of earlier complete records is detected by
// neither. Unfinished bytes after the boundary are dropped, so a rewritten
// partial record is reread. Same-size changes, truncation, replacement, and
// sample mismatches start a new generation. Caller holds owner.mu.
func (s *Snapshot) validateGeneration() (uint64, error) {
	o := s.owner
	known, ok := o.files.get(s.fileID)
	if ok && known.snapshot == s.identity {
		return known.id, nil
	}
	next := &fileGeneration{snapshot: s.identity, size: s.info.Size()}
	if ok && next.size > known.size && known.boundary > 0 {
		head, err := s.matchesCachedPages(known.id, 0, min(known.boundary, generationSampleBytes))
		if err != nil {
			return 0, err
		}
		tail := false
		if head {
			tail, err = s.matchesCachedPages(known.id, max(0, known.boundary-generationSampleBytes), known.boundary)
			if err != nil {
				return 0, err
			}
		}
		if tail {
			next.id, next.boundary = known.id, known.boundary
			for page := known.boundary / ingestionPageBytes; page*ingestionPageBytes < known.size; page++ {
				key := pageKey{s.fileID, known.id, page}
				keep := known.boundary - page*ingestionPageBytes
				if data, cached := o.pages.get(key); cached && int64(len(data)) > keep {
					if keep > 0 {
						o.pages.put(key, data[:keep], keep, ingestionCachePages, ingestionCachePages*ingestionPageBytes)
					} else {
						o.pages.remove(key)
					}
				}
			}
		}
	}
	if next.id == 0 {
		o.nextGeneration++
		next.id = o.nextGeneration
	}
	o.files.put(s.fileID, next, 1, maxTrackedFileGenerations, maxTrackedFileGenerations)
	return next.id, nil
}

// matchesCachedPages compares the file's current bytes in [start, end) with
// the generation's cached pages, reading the file directly and charging that
// read. A page that is no longer cached counts as a mismatch. Caller holds
// owner.mu.
func (s *Snapshot) matchesCachedPages(generation uint64, start, end int64) (bool, error) {
	current := make([]byte, end-start)
	n, err := s.file.ReadAt(current, start)
	s.owner.BytesRead += int64(n)
	s.physicalBytes.Add(int64(n))
	if err != nil && err != io.EOF {
		return false, err
	}
	if n < len(current) {
		return false, nil
	}
	for offset := start; offset < end; {
		page := offset / ingestionPageBytes
		within := offset - page*ingestionPageBytes
		data, ok := s.owner.pages.get(pageKey{s.fileID, generation, page})
		if !ok || int64(len(data)) <= within {
			return false, nil
		}
		length := min(int64(len(data))-within, end-offset)
		if !bytes.Equal(data[within:within+length], current[offset-start:offset-start+length]) {
			return false, nil
		}
		offset += length
	}
	return true, nil
}

// ReadTail and Checkpoint consume one pinned snapshot so a caller cannot
// display one generation and begin following a later generation's EOF.
func (s *Snapshot) ReadTail(lines int, byteLimit int64) (string, error) {
	return readTail(s, s.info.Size(), lines, byteLimit)
}

func (o *Ingestion) ReadTail(path string, lines int, byteLimit int64) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	return o.ReadFileTail(file, lines, byteLimit)
}

func (o *Ingestion) ReadFileTail(file *os.File, lines int, byteLimit int64) (string, error) {
	if o == nil {
		return ReadFile(file, lines, byteLimit)
	}
	snapshot, err := o.Snapshot(file)
	if err != nil {
		return "", err
	}
	return snapshot.ReadTail(lines, byteLimit)
}

func (s *Snapshot) Size() int64 { return s.info.Size() }

func (s *Snapshot) Checkpoint(ctx context.Context, offset int64) (Position, error) {
	if err := ctx.Err(); err != nil {
		return Position{}, err
	}
	if offset < 0 || offset > s.info.Size() {
		return Position{}, fmt.Errorf("transcript offset is outside snapshot")
	}
	position := Position{Snapshot: s.identity, Offset: offset, Size: s.info.Size(), Generation: 1, Identity: stableFileIdentity(s.info)}
	var err error
	position.Prefix, err = hashSample(s, 0, min(s.info.Size(), int64(4096)))
	if err != nil {
		return Position{}, err
	}
	position.Tail, err = hashSample(s, max(int64(0), offset-4096), min(offset, int64(4096)))
	if err != nil {
		return Position{}, err
	}
	current, err := s.file.Stat()
	if err != nil {
		return Position{}, err
	}
	if snapshotIdentity(current) != s.identity {
		return Position{}, fmt.Errorf("transcript snapshot changed during checkpoint")
	}
	return position, ctx.Err()
}

func (s *Snapshot) ReadAt(p []byte, offset int64) (int, error) {
	total := 0
	for len(p) > 0 {
		if offset >= s.info.Size() {
			return total, io.EOF
		}
		page := offset / ingestionPageBytes
		s.owner.mu.Lock()
		data, err := s.page(page)
		if err != nil {
			s.owner.mu.Unlock()
			return total, err
		}
		within := offset % ingestionPageBytes
		if within >= int64(len(data)) {
			s.owner.mu.Unlock()
			return total, io.ErrUnexpectedEOF
		}
		n := copy(p, data[within:])
		s.owner.mu.Unlock()
		total += n
		offset += int64(n)
		p = p[n:]
	}
	return total, nil
}

// page returns this snapshot's bytes of one page. Cached pages of a generation
// hold its latest snapshot's bytes, and validateGeneration drops unfinished
// bytes before a grown snapshot continues the generation. A cached page cut
// short by an earlier, smaller snapshot is therefore extended by reading only
// its missing suffix, and a longer page cached by a later snapshot is
// truncated to this snapshot. Caller holds owner.mu.
func (s *Snapshot) page(page int64) ([]byte, error) {
	if data, ok := s.batchPages[page]; ok {
		return data, nil
	}
	start := page * ingestionPageBytes
	need := min(int64(ingestionPageBytes), s.info.Size()-start)
	key := pageKey{s.fileID, s.generation, page}
	data, _ := s.owner.pages.get(key)
	if int64(len(data)) >= need {
		s.owner.CacheHits++
		data = data[:need]
	} else {
		if err := s.unchanged(); err != nil {
			return nil, err
		}
		grown := make([]byte, need)
		cached := copy(grown, data)
		n, err := s.file.ReadAt(grown[cached:], start+int64(cached))
		s.owner.BytesRead += int64(n)
		s.physicalBytes.Add(int64(n))
		if err != nil && err != io.EOF {
			return nil, err
		}
		// A change during the read could tear the page; never cache it.
		if err := s.unchanged(); err != nil {
			return nil, err
		}
		data = grown[:cached+n]
		s.owner.pages.put(key, data, int64(len(data)), ingestionCachePages, ingestionCachePages*ingestionPageBytes)
		// Track the latest snapshot's complete-record boundary. Each byte is
		// scanned once, when it is read; a missed boundary only means a later
		// append rereads more.
		if known, ok := s.owner.files.get(s.fileID); ok && known.id == s.generation && known.snapshot == s.identity {
			if newline := bytes.LastIndexByte(data[cached:], '\n'); newline >= 0 {
				known.boundary = max(known.boundary, start+int64(cached+newline)+1)
			}
		}
	}
	if s.batchPages != nil {
		s.batchPages[page] = data
	}
	return data, nil
}

func (s *Snapshot) unchanged() error {
	current, err := s.file.Stat()
	if err != nil {
		return err
	}
	if !os.SameFile(current, s.info) || snapshotIdentity(current) != s.identity {
		return fmt.Errorf("transcript snapshot changed during read")
	}
	return nil
}

// boundedCache is the one volatile LRU policy for pages, frames, unfinished
// frames, decoded records, and file generations. Callers hold the owning lock
// and pass the bounds with each insert.
type boundedCache[K comparable, V any] struct {
	entries map[K]*list.Element
	recency list.List
	bytes   int64
}

type boundedCacheEntry[K comparable, V any] struct {
	key   K
	value V
	size  int64
}

func (c *boundedCache[K, V]) get(key K) (V, bool) {
	element, ok := c.entries[key]
	if !ok {
		var zero V
		return zero, false
	}
	c.recency.MoveToFront(element)
	return element.Value.(*boundedCacheEntry[K, V]).value, true
}

// put replaces any entry for key, then evicts least recently used entries until
// both bounds hold. A value larger than maxBytes is not retained.
func (c *boundedCache[K, V]) put(key K, value V, size int64, maxEntries int, maxBytes int64) {
	c.remove(key)
	if size > maxBytes {
		return
	}
	for c.recency.Len() > 0 && (c.recency.Len() >= maxEntries || c.bytes+size > maxBytes) {
		c.remove(c.recency.Back().Value.(*boundedCacheEntry[K, V]).key)
	}
	if c.entries == nil {
		c.entries = make(map[K]*list.Element)
	}
	c.entries[key] = c.recency.PushFront(&boundedCacheEntry[K, V]{key, value, size})
	c.bytes += size
}

func (c *boundedCache[K, V]) remove(key K) {
	if element, ok := c.entries[key]; ok {
		c.bytes -= element.Value.(*boundedCacheEntry[K, V]).size
		c.recency.Remove(element)
		delete(c.entries, key)
	}
}

// ReadRecord preserves the existing stats framing contract: only LF (or the
// caller's explicit terminal-CR rule) completes a record. EOF is never a commit.
func ReadRecord(ctx context.Context, reader *bufio.Reader, maxBytes int64) ([]byte, int64, bool, bool, error) {
	var line []byte
	var total int64
	for {
		if err := ctx.Err(); err != nil {
			return nil, total, false, false, err
		}
		fragment, err := reader.ReadSlice('\n')
		available := maxBytes - total
		if int64(len(fragment)) > available {
			if available > 0 {
				line = append(line, fragment[:available]...)
				total += available
			}
			return line, total, false, true, nil
		}
		line = append(line, fragment...)
		total += int64(len(fragment))
		if err == nil {
			return line, total, true, false, nil
		}
		if err == bufio.ErrBufferFull {
			continue
		}
		if err == io.EOF {
			return line, total, false, false, nil
		}
		return nil, total, false, false, err
	}
}

// Position contains validation hashes, never transcript text. Generation changes
// explicitly invalidate the consumer's old normalized projection.
type Position struct {
	Snapshot   string
	Offset     int64
	Size       int64
	Generation uint64
	Identity   string
	Prefix     string
	Tail       string
}
type Record struct {
	Start, End int64
	Data       []byte
}
type Batch struct {
	Source        string
	Snapshot      string
	Before, After Position
	Records       []Record
	Reset         bool
	Gap           string
	Complete      bool
	ModifiedNS    int64
	BytesRead     int64
}

func hashSample(reader io.ReaderAt, offset, length int64) (string, error) {
	if length == 0 {
		return "", nil
	}
	data := make([]byte, length)
	if _, err := reader.ReadAt(data, offset); err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}
func (o *Ingestion) ReadBatch(ctx context.Context, path string, before Position, maxBytes int64, maxRecords int) (b Batch, readErr error) {
	b = Batch{Source: path, Before: before, After: before}
	f, err := os.Open(path)
	if err != nil {
		return b, err
	}
	defer f.Close()
	snapshot, err := o.Snapshot(f)
	if err != nil {
		return b, err
	}
	// Pin references only for this bounded batch, so shared-cache eviction
	// cannot turn framing or validation rereads into extra physical reads.
	snapshot.batchPages = make(map[int64][]byte)
	defer func() {
		b.BytesRead = snapshot.physicalBytes.Load()
		snapshot.owner.mu.Lock()
		snapshot.batchPages = nil
		snapshot.owner.mu.Unlock()
	}()
	b.Snapshot = snapshot.identity
	size := snapshot.info.Size()
	b.ModifiedNS = snapshot.info.ModTime().UnixNano()
	prefix, err := hashSample(snapshot, 0, min(before.Size, size, int64(4096)))
	if err != nil {
		return b, err
	}
	tail, err := hashSample(snapshot, max(int64(0), before.Offset-4096), min(before.Offset, int64(4096)))
	if err != nil && before.Offset <= size {
		return b, err
	}
	// Identity here uses stable file identity via SameFile-independent device/inode
	// fields supplied by the platform helper, rather than the snapshot ctime key.
	identity := stableFileIdentity(snapshot.info)
	if before.Generation == 0 || (size == before.Size && before.Snapshot != "" && before.Snapshot != snapshot.identity) || before.Identity != identity || before.Offset > size || before.Size > size || before.Prefix != prefix || before.Tail != tail {
		b.Reset = before.Generation > 0
		if b.Reset {
			b.Gap = "source_generation_changed"
		}
		b.After = Position{Generation: before.Generation + 1, Identity: identity}
	}
	start := b.After.Offset
	limit := min(maxBytes-min(maxBytes/4, int64(ingestionValidationReserveBytes)), size-start)

	consumed := int64(0)
	for len(b.Records) < maxRecords && consumed < limit {
		line, n, complete, cut, readErr := o.resumeFrame(ctx, snapshot, b.After.Offset, limit-consumed)
		if readErr != nil {
			return b, readErr
		}
		consumed += n
		if !complete && !cut && start+consumed == size && len(line) > 0 && line[len(line)-1] == '\r' {
			complete = true
		}
		if !complete {
			break
		}
		end := b.After.Offset + int64(len(line))
		b.Records = append(b.Records, Record{b.After.Offset, end, line})
		b.After.Offset = end
	}
	b.After.Size = size
	b.After.Snapshot = snapshot.identity
	b.After.Prefix, err = hashSample(snapshot, 0, min(size, int64(4096)))
	if err != nil {
		return b, err
	}
	b.After.Tail, err = hashSample(snapshot, max(int64(0), b.After.Offset-4096), min(b.After.Offset, int64(4096)))
	if err != nil {
		return b, err
	}
	b.Complete = b.After.Offset == size
	current, err := f.Stat()
	if err != nil {
		return b, err
	}
	if snapshotIdentity(current) != snapshot.identity {
		return b, fmt.Errorf("transcript changed during batch")
	}
	if err := ctx.Err(); err != nil {
		return b, err
	}
	return b, nil
}

func stableFileIdentity(info os.FileInfo) string {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return fmt.Sprintf("%v", info.Sys())
	}
	return fmt.Sprintf("%d:%d", stat.Dev, stat.Ino)
}

func snapshotIdentity(info os.FileInfo) string {
	value := reflect.Indirect(reflect.ValueOf(info.Sys()))
	change := value.FieldByName("Ctim")
	if !change.IsValid() {
		change = value.FieldByName("Ctimespec")
	}
	return fmt.Sprintf("%s:%d:%d:%v", stableFileIdentity(info), info.Size(), info.ModTime().UnixNano(), change)
}

// ReadFrame fans out immutable complete records before either consumer filters
// their contents. Consumer positions and commit admission remain independent.
// Incomplete fragments are never advertised as complete records.
type frameKey struct {
	file       string
	generation uint64
	offset     int64
}

func (o *Ingestion) ReadFrame(ctx context.Context, snapshot *Snapshot, offset, limit int64) ([]byte, int64, bool, bool, error) {
	if err := ctx.Err(); err != nil {
		return nil, 0, false, false, err
	}
	if limit <= 0 {
		return nil, 0, false, true, nil
	}
	o.frameMu.Lock()
	defer o.frameMu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, 0, false, false, err
	}
	key := frameKey{snapshot.fileID, snapshot.generation, offset}
	o.mu.Lock()
	cached, _ := o.frames.get(key)
	o.mu.Unlock()
	// A frame cached by a later snapshot of this generation may extend past
	// this snapshot's end; such a frame does not exist in this snapshot.
	if cached != nil && offset+int64(len(cached)) <= snapshot.info.Size() {
		if int64(len(cached)) > limit {
			return append([]byte(nil), cached[:limit]...), limit, false, true, nil
		}
		return append([]byte(nil), cached...), int64(len(cached)), true, false, nil
	}
	reader := bufio.NewReader(io.NewSectionReader(snapshot, offset, limit))
	line, n, complete, cut, err := ReadRecord(ctx, reader, limit)
	if err != nil {
		return line, n, complete, cut, err
	}
	if !complete && !cut && offset+n == snapshot.info.Size() && len(line) > 0 && line[len(line)-1] == '\r' {
		complete = true
	}
	// A CR-terminated frame is complete only at this snapshot's EOF; an
	// appended LF would extend it, so only LF-terminated frames are shared
	// across the snapshots of a generation.
	if complete && line[len(line)-1] == '\n' && len(line) <= MaxRecordBytes {
		o.mu.Lock()
		o.frames.put(key, append([]byte(nil), line...), int64(len(line)), maxCachedTranscriptRecords, maxCachedTranscriptBytes)
		o.mu.Unlock()
	}
	return line, n, complete, cut, nil
}

// Partial framing remains bounded and volatile. Durable consumers record only
// the complete boundary, so restart safely replays the original incomplete row.
type partialFrame struct {
	snapshot string
	offset   int64
	data     []byte
}

func (o *Ingestion) resumeFrame(ctx context.Context, snapshot *Snapshot, offset, limit int64) ([]byte, int64, bool, bool, error) {
	o.mu.Lock()
	pending, _ := o.partial.get(snapshot.path)
	o.mu.Unlock()
	var prefix []byte
	// Size and ctime change on both append and in-place rewrite. Boundary
	// samples cannot prove that a cached record's middle bytes are unchanged.
	// Reuse only this immutable snapshot; changed sources replay from the
	// durable complete-record boundary under the caller's existing byte budget.
	if pending.offset == offset && pending.snapshot == snapshot.identity && offset+int64(len(pending.data)) <= snapshot.info.Size() {
		prefix = pending.data
	}
	line, n, complete, cut, err := o.ReadFrame(ctx, snapshot, offset+int64(len(prefix)), limit)
	if err != nil {
		return nil, n, complete, cut, err
	}
	combined := append(append([]byte(nil), prefix...), line...)
	if len(combined) > MaxRecordBytes {
		return nil, n, false, false, fmt.Errorf("transcript record exceeds 8 MiB")
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	o.partial.remove(snapshot.path)
	if !complete && len(combined) > 0 {
		o.partial.put(snapshot.path, partialFrame{snapshot.identity, offset, combined}, int64(len(combined)), maxCachedPartialFrames, maxCachedTranscriptBytes)
	}
	return combined, n, complete, cut, nil
}
func (o *Ingestion) Metrics() (int64, int64) {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.BytesRead, o.CacheHits
}

// Start owns the one periodic ingestion clock for this server. Consumers have
// independent bounded signals; a failed/slow accounting transaction cannot
// block attribution or accumulate an unbounded replay queue.
func (o *Ingestion) Start(ctx context.Context) {
	o.tickOnce.Do(func() {
		go func() {
			ticker := time.NewTicker(5003 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case at := <-ticker.C:
					o.mu.Lock()
					for ch := range o.subscribers {
						select {
						case ch <- at:
						default:
						}
					}
					o.mu.Unlock()
				}
			}
		}()
	})
}
func (o *Ingestion) Subscribe() (chan time.Time, func()) {
	ch := make(chan time.Time, 1)
	o.mu.Lock()
	if o.subscribers == nil {
		o.subscribers = make(map[chan time.Time]bool)
	}
	o.subscribers[ch] = true
	o.mu.Unlock()
	return ch, func() { o.mu.Lock(); delete(o.subscribers, ch); o.mu.Unlock() }
}
