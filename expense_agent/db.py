"""Database connection shared by the app and the agent's tools.

A review opens one connection and shares it with every lookup it makes, so a
retrieval, three tools and the guard cost one TLS handshake instead of six.
Anything that takes an optional connection goes through run().
"""
from contextlib import contextmanager
from contextvars import ContextVar

import psycopg

from expense_agent.config import load_settings

_shared: ContextVar[psycopg.Connection | None] = ContextVar("shared_connection", default=None)


def connect() -> psycopg.Connection:
    return psycopg.connect(load_settings().database_url)


@contextmanager
def sharing(conn: psycopg.Connection):
    """Make conn the connection every run() call in this context uses."""
    token = _shared.set(conn)
    try:
        yield conn
    finally:
        _shared.reset(token)


def run(conn: psycopg.Connection | None, fn):
    """Call fn with the explicit connection, else the shared one, else a new one."""
    conn = conn or _shared.get()
    if conn is not None:
        return fn(conn)
    with connect() as own:
        return fn(own)
