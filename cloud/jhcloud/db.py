"""Database engine and session (PostgreSQL in Docker, SQLite in tests)."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # 40 = FastAPI's thread pool per process, so each busy thread can get a connection;
        # wait at most 5 s for one, then answer "busy" instead of hanging (found in the load test)
        kwargs.update(pool_size=20, max_overflow=20, pool_timeout=5)
    return create_engine(url, **kwargs)


def make_sessionmaker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine) -> None:
    """Create missing tables, and add columns that newer versions introduced (a tiny migration step)."""
    from sqlalchemy import inspect, text
    from . import models  # noqa: F401  (registers the tables)
    Base.metadata.create_all(engine)
    have = {t: {c["name"] for c in inspect(engine).get_columns(t)} for t in Base.metadata.tables}
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            for column in table.columns:
                if column.name not in have.get(table.name, set()) and not column.primary_key:
                    kind = column.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {kind} DEFAULT \'\''))
