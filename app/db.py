from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


def _database_engine_args() -> tuple[URL, dict[str, object]]:
    """Adapt provider-style PostgreSQL URLs for asyncpg."""
    url = make_url(settings.database_url)
    connect_args: dict[str, object] = {}
    if url.drivername == "postgresql+asyncpg":
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if sslmode:
            connect_args["ssl"] = sslmode not in {"disable", "allow"}
        url = url.set(query=query)
    return url, connect_args


_engine_url, _connect_args = _database_engine_args()
engine = create_async_engine(_engine_url, connect_args=_connect_args, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # Keep existing free-tier databases compatible with additive model changes.
        for table, (column, definition) in (
            ("dependencies", ("is_ignored", "BOOLEAN DEFAULT FALSE")),
            ("dependency_snapshots", ("lag_points", "FLOAT DEFAULT 0")),
            ("dependency_snapshots", ("age_points", "FLOAT DEFAULT 0")),
            ("dependency_snapshots", ("archived_points", "FLOAT DEFAULT 0")),
            ("dependency_snapshots", ("cve_points", "FLOAT DEFAULT 0")),
        ):
            clause = "IF NOT EXISTS " if connection.dialect.name != "sqlite" else ""
            statement = f"ALTER TABLE {table} ADD COLUMN {clause}{column} {definition}"
            try:
                await connection.execute(text(statement))
            except SQLAlchemyError:
                # Existing columns are harmless; startup must remain available during rollout.
                continue
