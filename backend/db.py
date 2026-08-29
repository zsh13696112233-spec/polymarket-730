from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import Settings
from backend.models import Base


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_sqlite_directory()
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
        )
        if settings.database_url.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", self._set_sqlite_pragmas)
        self.sessions = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @staticmethod
    def _set_sqlite_pragmas(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    async def initialize(self) -> None:
        if self.settings.database_url.endswith(":memory:"):
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return

        async with self.engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
        await self.engine.dispose()

        config_path = Path(__file__).resolve().parent / "alembic.ini"  # noqa: ASYNC240
        alembic_config = AlembicConfig(str(config_path))
        alembic_config.attributes["database_url"] = self.settings.database_url
        # Compatibility for databases briefly created by the pre-migration development
        # build: those tables exactly match 0001 but have no alembic_version row.
        # Stamp their actual schema revision, then replay every later migration.
        if "watched_wallets" in table_names and "alembic_version" not in table_names:
            await asyncio.to_thread(command.stamp, alembic_config, "0001_initial")
        await asyncio.to_thread(command.upgrade, alembic_config, "head")

    async def close(self) -> None:
        await self.engine.dispose()
