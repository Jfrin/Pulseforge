"""Needs real Postgres (TEST_DATABASE_URL), via the `store` fixture."""

import asyncio

import pytest_asyncio

from app.connectors.inbox import InboxConnector
from app.inbox import Inbox
from app.poller import Poller


@pytest_asyncio.fixture
async def inbox(store):
    box = Inbox(store.pool)
    await box.migrate()
    async with store.pool.connection() as conn:
        await conn.execute("TRUNCATE webhook_inbox RESTART IDENTITY")
    return box


async def test_append_then_read_in_order(inbox):
    assert await inbox.append("s", [{"i": 1}, {"i": 2}, {"i": 3}]) == 3
    rows = await inbox.read("s", 0, 10)
    assert [p["i"] for _, p in rows] == [1, 2, 3]
    assert [seq for seq, _ in rows] == sorted(seq for seq, _ in rows)


async def test_read_honours_after_seq_and_limit(inbox):
    await inbox.append("s", [{"i": i} for i in range(5)])
    first_two = await inbox.read("s", 0, 2)
    rest = await inbox.read("s", first_two[-1][0], 10)
    assert [p["i"] for _, p in first_two] == [0, 1] and [p["i"] for _, p in rest] == [2, 3, 4]


async def test_sources_are_isolated_and_empty_appends_store_nothing(inbox):
    await inbox.append("a", [{"i": 1}])
    await inbox.append("b", [{"i": 2}])
    assert await inbox.append("a", []) == 0
    assert [p["i"] for _, p in await inbox.read("a", 0, 10)] == [1]


async def test_concurrent_appends_are_all_stored_with_strictly_increasing_sequence_numbers(inbox):
    await asyncio.gather(*(inbox.append("s", [{"i": i}]) for i in range(20)))
    rows = await inbox.read("s", 0, 100)
    seqs = [seq for seq, _ in rows]
    assert sorted(p["i"] for _, p in rows) == list(range(20))
    assert seqs == sorted(seqs) and len(set(seqs)) == 20


async def test_webhook_records_flow_through_the_normal_pipeline_and_retries_are_deduplicated(store, inbox):
    poller = Poller(InboxConnector("webhook-demo", inbox), store, id_field="id")
    await inbox.append("webhook-demo", [{"id": 1}, {"id": 2}, {"id": 3}])
    assert (await poller.poll_once()).new == 3

    await inbox.append("webhook-demo", [{"id": 3}, {"id": 4}])  # the sender retried id 3
    second = await poller.poll_once()
    assert (second.new, second.duplicates) == (1, 1)

    assert (await poller.poll_once()).new == 0  # caught up: only a resume token remains
    async with store.pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM pending_records WHERE source = 'webhook-demo'")
        row = await cur.fetchone()
    assert row is not None and row[0] == 4
