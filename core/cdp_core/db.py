from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def configure(database_url: str | None = None):
    """(Re)initialise the engine. Tests call this with a SQLite URL."""
    global _engine, _Session
    url = database_url or settings.database_url
    kwargs = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        _enable_sqlite_savepoints(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def _enable_sqlite_savepoints(eng):
    """pysqlite needs this for SAVEPOINT (begin_nested) to work (SQLAlchemy docs)."""

    @event.listens_for(eng, "connect")
    def _connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @event.listens_for(eng, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")


def engine():
    if _engine is None:
        configure()
    return _engine


def init_db():
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine())


@contextmanager
def session_scope():
    if _Session is None:
        configure()
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
