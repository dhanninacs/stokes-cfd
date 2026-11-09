"""A small synchronous event bus; clients subscribe to model and job events."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from stokes.log import get_logger

log = get_logger(__name__)

Listener = Callable[[str, dict[str, Any]], None]


@dataclass
class EventBus:
    _listeners: dict[str, list[Listener]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def subscribe(self, event: str, listener: Listener) -> Callable[[], None]:
        """Subscribe to ``event`` (or ``"*"`` for all); returns an unsubscribe function."""
        with self._lock:
            self._listeners.setdefault(event, []).append(listener)

        def unsubscribe() -> None:
            with self._lock:
                listeners = self._listeners.get(event, [])
                if listener in listeners:
                    listeners.remove(listener)

        return unsubscribe

    def emit(self, event: str, **data: Any) -> None:  # Any: JSON-able payload
        with self._lock:
            targets = list(self._listeners.get(event, ())) + list(self._listeners.get("*", ()))
        for listener in targets:
            try:
                listener(event, data)
            except Exception:
                log.exception("event listener failed", extra={"stokes_fields": {"event": event}})
