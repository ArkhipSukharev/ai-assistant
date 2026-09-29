from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import delete, func, insert, inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base


async def migrate(sqlite_url: str, postgres_url: str, force: bool) -> None:
    if not sqlite_url.startswith("sqlite+aiosqlite://"):
        raise ValueError("--sqlite-url must use sqlite+aiosqlite")
    if not postgres_url.startswith("postgresql+asyncpg://"):
        raise ValueError("--postgres-url must use postgresql+asyncpg")

    source = create_async_engine(sqlite_url)
    target = create_async_engine(postgres_url)
    tables = list(Base.metadata.sorted_tables)

    try:
        async with source.connect() as source_connection, target.begin() as target_connection:
            await target_connection.run_sync(Base.metadata.create_all)
            source_tables = await source_connection.run_sync(
                lambda connection: set(inspect(connection).get_table_names())
            )

            occupied = []
            for table in tables:
                count = await target_connection.scalar(
                    select(func.count()).select_from(table)
                )
                if count:
                    occupied.append(f"{table.name} ({count})")
            if occupied and not force:
                raise RuntimeError(
                    "PostgreSQL is not empty: "
                    + ", ".join(occupied)
                    + ". Use --force to replace its application data."
                )

            if force:
                for table in reversed(tables):
                    await target_connection.execute(delete(table))

            total = 0
            for table in tables:
                if table.name not in source_tables:
                    print(f"{table.name}: skipped (not present in SQLite)")
                    continue
                rows = (
                    await source_connection.execute(select(table))
                ).mappings().all()
                if rows:
                    await target_connection.execute(insert(table), [dict(row) for row in rows])
                    total += len(rows)
                print(f"{table.name}: {len(rows)}")

            for table in tables:
                primary_key = list(table.primary_key.columns)
                if len(primary_key) != 1 or primary_key[0].name != "id":
                    continue
                table_name = table.name.replace('"', '""')
                await target_connection.execute(
                    text(
                        "SELECT setval("
                        f"pg_get_serial_sequence('\"{table_name}\"', 'id'), "
                        f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM \"{table_name}\""
                    )
                )

        print(f"Migration completed: {total} rows copied.")
    finally:
        await source.dispose()
        await target.dispose()


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    password = os.getenv("POSTGRES_PASSWORD", "")
    default_postgres_url = (
        f"postgresql+asyncpg://ai_assistant:{quote_plus(password)}"
        "@localhost:5432/ai_assistant"
        if password
        else None
    )
    parser = argparse.ArgumentParser(
        description="Copy all AI Assistant application data from SQLite to PostgreSQL."
    )
    parser.add_argument(
        "--sqlite-url",
        default="sqlite+aiosqlite:///./amocrm_assistant.db",
    )
    parser.add_argument(
        "--postgres-url",
        default=default_postgres_url,
        required=default_postgres_url is None,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing application rows in PostgreSQL before copying.",
    )
    args = parser.parse_args()
    asyncio.run(migrate(args.sqlite_url, args.postgres_url, args.force))


if __name__ == "__main__":
    main()
