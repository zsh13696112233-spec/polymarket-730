import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine


def migrate(connection):
    context.configure(connection=connection, version_table="collection_alembic_version")
    with context.begin_transaction():
        context.run_migrations()


async def run():
    engine = create_async_engine(context.config.attributes["database_url"])
    async with engine.begin() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


asyncio.run(run())
