"""
Postgres access for farmer profiles and message history.

Entirely optional, in keeping with the rest of the service: with no DATABASE_URL
the pool is never created, is_available() reports False, and every caller falls
back to the stateless behaviour the agent had before. Nothing here should ever
be the reason a farmer does not get an answer, so all failures are caught and
logged rather than raised.
"""
import threading

from config import DATABASE_URL, DB_POOL_MAX

_pool = None
_init_error: str | None = None
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS farmers (
    user_id           TEXT PRIMARY KEY,
    channel           TEXT NOT NULL DEFAULT 'sms',
    location_text     TEXT,
    latitude          DOUBLE PRECISION,
    longitude         DOUBLE PRECISION,
    state             TEXT,
    district          TEXT,
    language          TEXT,
    crops             TEXT[] NOT NULL DEFAULT '{}',
    location_asked_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id                 BIGSERIAL PRIMARY KEY,
    user_id            TEXT NOT NULL,
    direction          TEXT NOT NULL,
    body               TEXT NOT NULL,
    gateway_message_id TEXT UNIQUE,
    meta               JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_user_time
    ON messages (user_id, created_at DESC);
"""


def init() -> bool:
    """Create the pool and ensure the schema exists. Safe to call repeatedly."""
    global _pool, _init_error

    if _pool is not None:
        return True
    if _init_error is not None:
        return False

    with _lock:
        if _pool is not None:
            return True
        if not DATABASE_URL:
            _init_error = "DATABASE_URL not set; farmer profiles and history disabled"
            print(f"Database: {_init_error}")
            return False

        try:
            from psycopg_pool import ConnectionPool

            # Neon scales to zero, so the first connection after an idle period
            # has to wait for the compute to spin up.
            pool = ConnectionPool(
                DATABASE_URL,
                min_size=0,
                max_size=DB_POOL_MAX,
                timeout=30,
                max_idle=120,
                open=True,
            )
            pool.wait(timeout=30)

            with pool.connection() as conn:
                conn.execute(SCHEMA)

            _pool = pool
            print("Database ready (farmer profiles and history enabled)")
            return True

        except Exception as e:
            _init_error = str(e)
            print(f"Database unavailable, continuing stateless: {e}")
            return False


def is_available() -> bool:
    return _pool is not None


def status() -> str:
    if _pool is not None:
        return "connected"
    return _init_error or "not initialized"


def connection():
    """Context manager yielding a pooled connection. Caller must check is_available()."""
    return _pool.connection()


def close():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
