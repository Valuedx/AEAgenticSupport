"""
PostgreSQL advisory locks — per-thread concurrency control.
Prevents race conditions when two Teams messages arrive for the same thread.
"""

import hashlib
from contextlib import contextmanager
from django.db import connection


def _lock_key(thread_id: str) -> int:
    h = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return int(h[:16], 16) & 0x7FFFFFFFFFFFFFFF  # must fit signed bigint


@contextmanager
def pg_advisory_lock(thread_id: str):
    key = _lock_key(thread_id)
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s);", [key])
    try:
        yield
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s);", [key])
