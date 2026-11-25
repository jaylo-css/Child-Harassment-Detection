"""Database engine/session helpers (SQLite via SQLModel).

Encryption at rest: SQLite here is NOT encrypted. For production, use SQLCipher (e.g. the
`sqlcipher3` driver with `GUARDIAN_DB_URL=sqlite+pysqlcipher://:<key>@/path.db`) or full-disk
encryption. See docs/PRIVACY.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (register tables)

_engine: Engine | None = None
_engine_url: str | None = None


def make_engine(url: str) -> Engine:
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if url in ("sqlite://", "sqlite:///:memory:"):
        kwargs["poolclass"] = StaticPool
    elif url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, **kwargs)

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA secure_delete=ON")  # overwrite deleted content
        cur.close()

    SQLModel.metadata.create_all(eng)
    return eng


def init_engine(url: str) -> Engine:
    global _engine, _engine_url
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _engine.dispose()
        _engine = make_engine(url)
        _engine_url = url
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        from app.config import get_settings

        return init_engine(get_settings().db_url)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as s:
        yield s
