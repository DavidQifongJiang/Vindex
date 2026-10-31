"""Process-local REST transport reuse, separate from ingestion clients."""
import os
from contextlib import contextmanager
from threading import Condition

from qdrant_client import QdrantClient

_condition = Condition()
_clients = {}
_active = 0
_closing = False


def _after_fork():
    global _condition, _clients, _active, _closing
    # Never share sockets or inherited locks with a parent process.
    _condition, _clients, _active, _closing = Condition(), {}, 0, False


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


@contextmanager
def semantic_client():
    global _active
    key = (os.getenv("QDRANT_URL", "http://localhost:6333"), os.getenv("QDRANT_API_KEY"))
    with _condition:
        if _closing:
            raise RuntimeError("Search clients are shutting down")
        if key not in _clients:
            _clients[key] = QdrantClient(url=key[0], api_key=key[1], timeout=2,
                                          check_compatibility=False)
        client = _clients[key]
        _active += 1
    try:
        yield client
    finally:
        with _condition:
            _active -= 1
            _condition.notify_all()


def close_search_clients():
    global _closing
    with _condition:
        _closing = True
        while _active:
            _condition.wait()
        clients = list(_clients.values())
        _clients.clear()
        try:
            for client in clients:
                client.close()
        finally:
            _closing = False
