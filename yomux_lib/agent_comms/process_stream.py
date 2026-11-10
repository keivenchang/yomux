# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deadline-bound subprocess pipe exchange for structured agent clients."""

from __future__ import annotations

import codecs
import io
import os
import select
import selectors
import subprocess
import threading
import time
from collections.abc import Callable
from collections import deque

from .exceptions import TransportInterrupted


def retire_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def close_process_pipes(process: subprocess.Popen[str]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


class ProcessPipeOutput:
    """One resumable selector and byte-buffer owner for a structured subprocess."""

    def __init__(
        self, process: subprocess.Popen[str], deadline: Callable[[], float], *,
        cancel_event: threading.Event | None = None, input_text: str | None = None,
        input_queue: deque[bytes] | None = None,
    ) -> None:
        self.process = process
        self.deadline = deadline
        self.cancel_event = cancel_event
        self.input_queue = input_queue if input_queue is not None else deque()
        self.close_stdin = input_queue is None
        self.selector = selectors.DefaultSelector()
        self.events = deque()
        self.ready = deque()
        self.closed = False
        self.stdin_active = False
        try:
            for name in ("stdout", "stderr"):
                stream = getattr(process, name)
                if stream is not None:
                    decoder = io.IncrementalNewlineDecoder(
                        codecs.getincrementaldecoder(stream.encoding or "utf-8")(errors=stream.errors or "strict"),
                        translate=True,
                    )
                    self.selector.register(stream, selectors.EVENT_READ, [name, decoder, "", None])
            if input_text is not None:
                if process.stdin is None:
                    raise OSError("agent subprocess stdin is unavailable")
                if input_text:
                    self.input_queue.append(input_text.encode(process.stdin.encoding or "utf-8", errors=process.stdin.errors or "strict"))
                else:
                    process.stdin.close()
        except (KeyboardInterrupt, SystemExit):
            self.close()
            raise
        except Exception:
            self.close()
            raise

    def __enter__(self) -> ProcessPipeOutput:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self) -> ProcessPipeOutput:
        return self

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.selector.close()

    def _exchange(self, key: selectors.SelectorKey) -> None:
        name, decoder, buffered, chunk = key.data
        if name == "stdin":
            try:
                written = os.write(key.fd, buffered[:select.PIPE_BUF])
                key.data[2] = buffered[written:]
            except BrokenPipeError:
                if not self.close_stdin:
                    raise
                key.data[2] = b""
            if not key.data[2]:
                self.selector.unregister(key.fileobj)
                self.stdin_active = False
                self.input_queue.popleft()
                if self.close_stdin:
                    key.fileobj.close()
                else:
                    self.events.append(("stdin", ""))
            return
        if chunk is None:
            chunk = os.read(key.fd, 65536)
            key.data[3] = chunk
        checkpoint = decoder.getstate()
        try:
            decoded = decoder.decode(chunk, final=not chunk)
        except (KeyboardInterrupt, SystemExit):
            decoder.setstate(checkpoint)
            raise
        except Exception:
            decoder.setstate(checkpoint)
            raise
        buffered += decoded
        if name == "stderr":
            if buffered:
                self.events.append((name, buffered))
            buffered = ""
        else:
            *lines, buffered = buffered.split("\n")
            self.events.extend((name, line + "\n") for line in lines)
        key.data[2:4] = [buffered, None]
        if not chunk:
            self.selector.unregister(key.fileobj)
            if buffered:
                self.events.append((name, buffered))

    def __next__(self) -> tuple[str, str]:
        while not self.closed:
            if self.events:
                return self.events.popleft()
            if self.input_queue and not self.stdin_active:
                self.selector.register(self.process.stdin, selectors.EVENT_WRITE,
                                       ["stdin", None, memoryview(self.input_queue[0]), None])
                self.stdin_active = True
            if not self.selector.get_map() and self.process.poll() is not None:
                self.close()
                break
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise TransportInterrupted("interrupted")
            remaining = self.deadline() - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for agent subprocess")
            if not self.ready:
                self.ready.extend(key for key, _mask in self.selector.select(timeout=min(0.25, remaining)))
            if self.ready:
                self._exchange(self.ready[0])
                self.ready.popleft()
        raise StopIteration


def process_pipe_output(
    process: subprocess.Popen[str],
    deadline: Callable[[], float],
    *,
    cancel_event: threading.Event | None = None,
    input_text: str | None = None,
    input_queue: deque[bytes] | None = None,
) -> ProcessPipeOutput:
    return ProcessPipeOutput(process, deadline, cancel_event=cancel_event, input_text=input_text, input_queue=input_queue)
