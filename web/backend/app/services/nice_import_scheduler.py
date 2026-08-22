from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from sqlmodel import Session

from app.core.db import engine
from app.services import nice_import

logger = logging.getLogger(__name__)

NUDGE_DEBOUNCE_SECONDS = 2.0
SHUTDOWN_JOIN_SECONDS = 2.0

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_event = threading.Event()
_last_nudge_at = 0.0


def _session_factory() -> Session:
    return Session(engine)


def ensure_started(
    *,
    session_factory: Callable[[], Session] = _session_factory,
    debounce: bool = True,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """Start the process-local scheduler thread if one is not alive."""
    global _last_nudge_at, _thread

    now = monotonic()
    with _lock:
        if _thread is not None and _thread.is_alive():
            if debounce:
                _last_nudge_at = now
            return False
        if debounce and now - _last_nudge_at < NUDGE_DEBOUNCE_SECONDS:
            return False
        _last_nudge_at = now
        _stop_event.clear()
        _thread = threading.Thread(
            target=_run,
            kwargs={"session_factory": session_factory},
            name="nice-import-scheduler",
            daemon=True,
        )
        _thread.start()
        return True


def nudge_if_unfinished(session: Session) -> bool:
    if not nice_import.has_unfinished_jobs(session):
        return False
    return ensure_started()


def startup_recover() -> bool:
    with Session(engine) as session:
        return nudge_if_unfinished(session)


def shutdown() -> None:
    with _lock:
        thread = _thread
        _stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=SHUTDOWN_JOIN_SECONDS)


def is_running() -> bool:
    with _lock:
        return _thread is not None and _thread.is_alive()


def _run(*, session_factory: Callable[[], Session]) -> None:
    try:
        nice_import.run_import_worker(
            session_factory,
            stop_requested=_stop_event.is_set,
        )
    except Exception:
        logger.exception("NICE import scheduler stopped after an error")
