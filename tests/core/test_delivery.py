import asyncio
import os
from contextlib import suppress
from unittest.mock import AsyncMock
from uuid import uuid4

import aio_pika
import pytest
from psycopg.types.json import Jsonb
from pulseforge import runtime
from pulseforge.db import Transaction
from pulseforge.events import Event
from pulseforge.replay import replay
from pulseforge.resilience import SourceUnavailable
from pulseforge.runtime import consume


def message(event):
    msg = AsyncMock()
    msg.body = event.model_dump_json().encode()
    msg.message_id = event.id
    return msg


@pytest.mark.asyncio
async def test_redelivery_is_idempotent_and_outbox_atomic(db):
    event = Event(id="one", type="BatchCreated", source="mock", payload={})

    async def handler(tx, event):
        await tx.put("results", "one", {"done": True})
        await tx.emit("ProductsScored", {"batch_id": event.id})

    first, second = message(event), message(event)
    await consume(db, "processing", handler, first)
    await consume(db, "processing", handler, second)
    first.ack.assert_awaited_once()
    second.ack.assert_awaited_once()
    async with db.pool.connection() as conn:
        assert len(await Transaction(conn, "test").all("results")) == 1
        cur = await conn.execute("SELECT count(*) FROM outbox")
        assert (await cur.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_failed_handler_rolls_back_and_dead_letters(db, monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "1")

    async def handler(tx, event):
        await tx.put("results", "should_rollback", {"bad": True})
        await tx.emit("ShouldNotExist", {})
        raise ValueError("invalid input")

    msg = message(Event(id="bad", type="BatchCreated", source="mock", payload={}))
    await consume(db, "processing", handler, msg)
    msg.ack.assert_awaited_once()
    async with db.pool.connection() as conn:
        assert not await Transaction(conn, "test").all("results")
        cur = await conn.execute("SELECT event_type FROM outbox")
        assert await cur.fetchall() == [("PipelineFailed",)]
        cur = await conn.execute("SELECT reason FROM dead_letters WHERE id='bad'")
        assert (await cur.fetchone())[0] == "ValueError"
        cur = await conn.execute("SELECT count(*) FROM consumed")
        assert (await cur.fetchone())[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["UPDATE audit SET record_id='changed'", "DELETE FROM audit", "TRUNCATE audit"])
async def test_audit_rejects_mutation(db, operation):
    async with db.pool.connection() as conn:
        await Transaction(conn, "test").audit("product", {"flags": ["suppressed"]})
    with pytest.raises(Exception, match="append-only"):
        async with db.pool.connection() as conn:
            await conn.execute(operation)


@pytest.mark.asyncio
async def test_dependency_outage_pauses_then_recovers_without_dlq(db, monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "1")
    monkeypatch.setattr(runtime.asyncio, "sleep", AsyncMock())
    event = Event(id="paused", type="ProductsScored", source="processing", payload={})
    failed = message(event)
    await consume(db, "orchestrator", AsyncMock(side_effect=SourceUnavailable()), failed)
    failed.nack.assert_awaited_once_with(requeue=True)
    async with db.pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM dead_letters")
        assert (await cur.fetchone())[0] == 0
    recovered = message(event)
    await consume(db, "orchestrator", AsyncMock(), recovered)
    recovered.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_retries_confirm_loss_with_same_event_id(db):
    broker = await aio_pika.connect_robust(os.environ["AMQP_URL"])
    task = None
    try:
        channel = await broker.channel(publisher_confirms=True)
        exchange = await channel.declare_exchange(f"test-{uuid4()}", auto_delete=True)
        queue = await channel.declare_queue(exclusive=True, auto_delete=True)
        await queue.bind(exchange, "TestEvent")
        async with db.pool.connection() as conn:
            await Transaction(conn, "test").emit("TestEvent", {"batch_id": "b1"})

        class LoseFirstConfirm:
            failed = False

            async def publish(self, *args, **kwargs):
                await exchange.publish(*args, **kwargs)
                if not self.failed:
                    self.failed = True
                    raise ConnectionError("publish succeeded but confirmation was lost")

        async with queue.iterator() as messages:
            task = asyncio.create_task(runtime.relay(db, LoseFirstConfirm(), "test"))
            first = await asyncio.wait_for(anext(messages), 5)
            second = await asyncio.wait_for(anext(messages), 5)
            assert first.message_id == second.message_id
            assert Event.model_validate_json(first.body).payload == {"batch_id": "b1"}
            await first.ack()
            await second.ack()
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await broker.close()


@pytest.mark.asyncio
async def test_dlq_replay_targets_original_consumer(db, monkeypatch):
    service = f"test-replay-{uuid4()}"
    monkeypatch.setenv("SERVICE", service)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"] + "?options=-csearch_path%3Dcore_tests")
    event = Event(id="retry-me", type="BatchCreated", source="mock", payload={"batch_id": "b1"})
    async with db.pool.connection() as conn:
        await conn.execute(
            "INSERT INTO dead_letters(id,event,reason) VALUES (%s,%s,'test')",
            (event.id, Jsonb(event.model_dump(mode="json"))),
        )
        await conn.execute("INSERT INTO failures VALUES (%s,5)", (event.id,))
    broker = await aio_pika.connect_robust(os.environ["AMQP_URL"])
    try:
        channel = await broker.channel()
        queue = await channel.declare_queue(service, durable=True, auto_delete=True)
        async with queue.iterator() as messages:
            await replay(event.id)
            msg = await asyncio.wait_for(anext(messages), 5)
            assert Event.model_validate_json(msg.body).id == event.id
            await msg.ack()
        async with db.pool.connection() as conn:
            cur = await conn.execute("SELECT replayed_at FROM dead_letters WHERE id=%s", (event.id,))
            assert (await cur.fetchone())[0] is not None
            cur = await conn.execute("SELECT count(*) FROM failures")
            assert (await cur.fetchone())[0] == 0
    finally:
        await broker.close()
