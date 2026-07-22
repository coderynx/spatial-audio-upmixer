"""Database setup and transactional session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative model base."""


def create_database_engine(database_url: str) -> Engine:
    """Create an engine compatible with SQLite and PostgreSQL."""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def upgrade_database(database_url: str) -> None:
    """Apply committed migrations to the configured database."""
    package_dir = Path(__file__).resolve().parent
    config = Config()
    config.attributes["database_url_configured"] = True
    config.set_main_option("script_location", str(package_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions that retain loaded state after commits."""
    return sessionmaker(engine, expire_on_commit=False)


def session_dependency(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield one request-scoped session."""
    with factory() as session:
        yield session
