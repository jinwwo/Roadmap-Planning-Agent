"""
interactive/event_bus.py
─────────────────────────
Thread-safe 이벤트 버스 + stdout 캡처 유틸.

에이전트 코드에는 손을 대지 않고, `print()` 출력을 가로채서 UI 로 스트리밍합니다.
또한 에이전트/세션이 명시적으로 `bus.emit(event)` 를 호출할 수 있습니다.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from typing import Any, Iterator, Optional

# 세션 종료 신호
_SENTINEL = object()


@dataclass
class Event:
    type: str                    # "log" | "step_start" | "step_end" | "llm_token" | "hitl_request" | "result" | "error" | "done"
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"type": self.type, "payload": self.payload, "ts": self.ts}


class EventBus:
    """Thread-safe pub/sub 큐.  SSE 한 연결당 한 개의 EventBus 를 사용합니다."""

    def __init__(self) -> None:
        self._q: "queue.Queue[Any]" = queue.Queue()
        self._closed = False
        self._lock = threading.Lock()

    def emit(self, type_: str, **payload: Any) -> None:
        if self._closed:
            return
        self._q.put(Event(type=type_, payload=payload))

    def log(self, message: str, source: str = "agent") -> None:
        self.emit("log", message=message, source=source)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._q.put(_SENTINEL)

    def iter_events(self, timeout: float = 30.0) -> Iterator[Event]:
        """SSE 스트림용 제너레이터.  timeout 마다 keepalive heartbeat 발행."""
        while True:
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                # keepalive
                yield Event(type="ping", payload={})
                continue
            if item is _SENTINEL:
                return
            yield item


# ── stdout 캡처 ──────────────────────────────────────────────

class _TeeStream:
    """sys.stdout 를 대체하여 각 라인을 EventBus 로 복제합니다.  기존 터미널 출력도 유지."""

    def __init__(self, bus: EventBus, original):
        self._bus = bus
        self._orig = original
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, data: str) -> int:
        with self._lock:
            try:
                self._orig.write(data)
            except Exception:
                pass
            self._buf += data
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.rstrip()
                if line:
                    self._bus.log(line, source="agent")
        return len(data)

    def flush(self) -> None:
        try:
            self._orig.flush()
        except Exception:
            pass


@contextmanager
def capture_stdout_to(bus: EventBus):
    """with 블록 내부 stdout 출력을 bus 로 전달"""
    original = sys.stdout
    tee = _TeeStream(bus, original)
    sys.stdout = tee
    try:
        yield
    finally:
        sys.stdout = original
