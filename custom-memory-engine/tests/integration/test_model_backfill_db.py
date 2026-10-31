"""B-05: the M1/M7 backfill SQL equals ``map_v1_version`` on every input, on PostgreSQL."""

import itertools
import os

import pytest
from psycopg import AsyncConnection, sql

from custom_memory_engine.config import TestDatabaseConfig as DatabaseConfig
from custom_memory_engine.model.axes import V1_BACKFILL_SQL, V1_LAYERS, V1_STATUSES, map_v1_version

pytestmark = pytest.mark.integration


async def test_backfill_sql_matches_the_reference_mapping_exhaustively() -> None:
    if not os.environ.get("CME_TEST_DATABASE_URL"):
        pytest.skip("CME_TEST_DATABASE_URL must name a disposable database")
    url = DatabaseConfig.from_environment().database_url.get_secret_value()
    inputs = list(itertools.product(V1_STATUSES, V1_LAYERS, (False, True)))
    async with await AsyncConnection.connect(url, connect_timeout=5) as conn:
        await conn.execute("CREATE TEMP TABLE v1 (status text, layer text, has_derivation boolean)")
        async with conn.cursor() as cursor:
            await cursor.executemany("INSERT INTO v1 VALUES (%s, %s, %s)", inputs)
            await cursor.execute(
                sql.SQL("SELECT status, layer, has_derivation, ({})::text FROM v1").format(sql.SQL(V1_BACKFILL_SQL))  # type: ignore[arg-type]
            )
            rows = await cursor.fetchall()
    assert len(rows) == len(inputs) == 40
    for status, layer, derived, rendered in rows:
        expected = map_v1_version(status, layer, derived)
        assert rendered == f"({expected.kind},{expected.lifecycle},{expected.retention},{expected.reason_code})"
