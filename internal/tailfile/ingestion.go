// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

package tailfile

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
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
	tickOnce     sync.Once
	subscribers  map[chan time.Time]bool
	frameMu      sync.Mutex
	partial      map[string]partialFrame
	partialBytes int64
	mu           sync.Mutex
	pages        map[pageKey][]byte
	order        []pageKey
	BytesRead    int64
	CacheHits    int64
	frames       map[frameKey][]byte
	frameOrder   []frameKey
	frameBytes   int64
}
type pageKey struct {
	path                 string
	size, modified, page int64
	identity             string
}
type Snapshot struct {
	batchPages     map[int64][]byte
	physicalBytes  atomic.Int64
	owner          *Ingestion
	file           *os.File
	info           os.FileInfo
	path, identity string
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
	return &Snapshot{owner: o, file: file, info: info, path: file.Name(), identity: snapshotIdentity(info)}, nil
}

// ReadTail and Checkpoint consume one pinned snapshot so a caller cannot
// display one generation and begin following a later generation's EOF.
func (s *Snapshot) ReadTail(lines int, byteLimit int64) (string, error) {
	return readTail(s, s.info.Size(), lines, byteLimit)
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
		key := pageKey{s.path, s.info.Size(), s.info.ModTime().UnixNano(), page, s.identity}
		s.owner.mu.Lock()
		if s.owner.pages == nil {
			s.owner.pages = make(map[pageKey][]byte)
		}
		data, ok := s.batchPages[page]
		if !ok {
			data, ok = s.owner.pages[key]
		}
		if !ok {
			current, err := s.file.Stat()
			if err != nil || !os.SameFile(current, s.info) || current.Size() != s.info.Size() || current.ModTime() != s.info.ModTime() || snapshotIdentity(current) != s.identity {
				s.owner.mu.Unlock()
				return total, fmt.Errorf("transcript snapshot changed during read")
			}
			data = make([]byte, min(int64(ingestionPageBytes), s.info.Size()-page*ingestionPageBytes))
			n, err := s.file.ReadAt(data, page*ingestionPageBytes)
			s.owner.BytesRead += int64(n)
			s.physicalBytes.Add(int64(n))
			if err != nil && err != io.EOF {
				s.owner.mu.Unlock()
				return total, err
			}
			data = data[:n]
			if len(s.owner.order) >= ingestionCachePages {
				delete(s.owner.pages, s.owner.order[0])
				s.owner.order = s.owner.order[1:]
			}
			s.owner.pages[key] = data
			s.owner.order = append(s.owner.order, key)
		} else {
			s.owner.CacheHits++
		}
		if s.batchPages != nil {
			s.batchPages[page] = data
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
	path, snapshot string
	offset         int64
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
	key := frameKey{snapshot.path, snapshot.identity, offset}
	o.mu.Lock()
	cached := o.frames[key]
	o.mu.Unlock()
	if cached != nil {
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
	if complete {
		o.mu.Lock()
		if o.frames == nil {
			o.frames = make(map[frameKey][]byte)
		}
		if o.frames[key] == nil {
			for len(o.frameOrder) > 0 && (o.frameBytes+int64(len(line)) > 16<<20 || len(o.frameOrder) >= 4096) {
				old := o.frameOrder[0]
				o.frameOrder = o.frameOrder[1:]
				o.frameBytes -= int64(len(o.frames[old]))
				delete(o.frames, old)
			}
			if len(line) <= MaxRecordBytes {
				o.frames[key] = append([]byte(nil), line...)
				o.frameOrder = append(o.frameOrder, key)
				o.frameBytes += int64(len(line))
			}
		}
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
	pending := o.partial[snapshot.path]
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
	if o.partial == nil {
		o.partial = make(map[string]partialFrame)
	}
	o.partialBytes -= int64(len(o.partial[snapshot.path].data))
	delete(o.partial, snapshot.path)
	if !complete && len(combined) > 0 {
		if o.partialBytes+int64(len(combined)) > 16<<20 || len(o.partial) >= 256 {
			for key, entry := range o.partial {
				o.partialBytes -= int64(len(entry.data))
				delete(o.partial, key)
				if o.partialBytes+int64(len(combined)) <= 16<<20 && len(o.partial) < 256 {
					break
				}
			}
		}
		o.partial[snapshot.path] = partialFrame{snapshot.identity, offset, combined}
		o.partialBytes += int64(len(combined))
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
