"""At-least-once broker delivery, transactional consumers/outbox, bounded retries and replayable DLQ."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os

import aio_pika
import uvicorn
from fastapi import FastAPI, HTTPException
from psycopg.types.json import Jsonb

from .db import Database, Transaction
from .events import Event
from .resilience import SourceUnavailable
from .retention import maintain

log = logging.getLogger("runtime")
BINDINGS = {
    "processing": ["BatchCreated"],
    "orchestrator": ["ProductsScored"],
    "governance": ["EnrichmentCompleted"],
    "export": ["GovernanceChecked", "AnomalyDetected"],
    "analytics": ["GovernanceChecked", "SourceHealth", "EnrichmentCompleted", "PipelineFailed", "ExportReady"],
    "notification": ["AnomalyDetected"],
}


async def topology(channel):
    exchange = await channel.declare_exchange("pulseforge", aio_pika.ExchangeType.TOPIC, durable=True)
    for service, bindings in BINDINGS.items():
        queue = await channel.declare_queue(service, durable=True)
        for binding in bindings:
            await queue.bind(exchange, binding)
    return exchange


async def relay(db, exchange, service):
    while True:
        try:
            async with db.pool.connection() as conn, conn.transaction():
                cur = await conn.execute(
                    "SELECT id,event_type,source,payload,created_at FROM outbox "
                    "WHERE published_at IS NULL ORDER BY id LIMIT 50 FOR UPDATE SKIP LOCKED"
                )
                for row in await cur.fetchall():
                    event = Event(
                        id=f"{service}:{row[0]}", type=row[1], source=row[2], payload=row[3], occurred_at=row[4]
                    )
                    await exchange.publish(
                        aio_pika.Message(
                            event.model_dump_json().encode(),
                            message_id=event.id,
                            content_type="application/json",
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        ),
                        routing_key=event.type,
                    )
                    await conn.execute("UPDATE outbox SET published_at=now() WHERE id=%s", (row[0],))
        except Exception:
            log.exception("outbox relay failed; unpublished rows will retry")
        await asyncio.sleep(0.5)


async def consume(db, service, handler, message):
    event = None
    try:
        event = Event.model_validate_json(message.body)
        async with db.pool.connection() as conn, conn.transaction():
            # Serialize state transitions per service, including scheduled governance sweeps.
            await conn.execute("SELECT pg_advisory_xact_lock(71234)")
            cur = await conn.execute(
                "INSERT INTO consumed(id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id", (event.id,)
            )
            if await cur.fetchone():
                await handler(Transaction(conn, service), event)
                await conn.execute("DELETE FROM failures WHERE id=%s", (event.id,))
        await message.ack()
    except SourceUnavailable:
        # An unavailable dependency is a paused batch, not a poison record.
        async with db.pool.connection() as conn:
            await Transaction(conn, service).emit("SourceHealth", {"source": service, "success": False})
        await asyncio.sleep(5)
        await message.nack(requeue=True)
    except Exception as exc:
        log.exception("consumer failed service=%s", service)
        event_id = event.id if event else (message.message_id or message.body.hex()[:128])
        try:
            async with db.pool.connection() as conn, conn.transaction():
                cur = await conn.execute(
                    "INSERT INTO failures VALUES (%s,1) ON CONFLICT(id) DO UPDATE "
                    "SET attempts=failures.attempts+1 RETURNING attempts",
                    (event_id,),
                )
                attempts = (await cur.fetchone())[0]
                if attempts >= int(os.getenv("MAX_ATTEMPTS", "5")):
                    body = event.model_dump(mode="json") if event else {"invalid": message.body.hex()}
                    await conn.execute(
                        "INSERT INTO dead_letters(id,event,reason) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (event_id, Jsonb(body), type(exc).__name__),
                    )
                    await Transaction(conn, service).emit("PipelineFailed", {"service": service, "event_id": event_id})
            if attempts >= int(os.getenv("MAX_ATTEMPTS", "5")):
                await message.ack()
            else:
                await asyncio.sleep(min(2**attempts, 10))
                await message.nack(requeue=True)
        except Exception:
            # A DB outage must never turn a broker delivery into a lost event.
            log.exception("unable to persist retry; requeueing")
            await asyncio.sleep(2)
            await message.nack(requeue=True)


async def main():
    logging.basicConfig(level=logging.INFO)
    service = os.environ["SERVICE"]
    db = Database(os.environ["DATABASE_URL"])
    await db.open()
    broker = await aio_pika.connect_robust(os.environ["AMQP_URL"])
    channel = await broker.channel(publisher_confirms=True)
    await channel.set_qos(prefetch_count=1)
    exchange = await topology(channel)
    module = importlib.import_module("app.handler") if service != "source-connectors" else None
    app = FastAPI(title=f"PulseForge {service}")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": service}

    @app.get("/ready")
    async def ready():
        async with db.pool.connection() as conn:
            await conn.execute("SELECT 1")
        if broker.is_closed or any(task.done() for task in tasks):
            raise HTTPException(503, "worker or broker unavailable")
        return {"ready": True}

    @app.get("/metrics")
    async def metrics():
        async with db.pool.connection() as conn:
            tx = Transaction(conn, service)
            return await tx.all("metrics")

    @app.get("/results")
    async def results():
        async with db.pool.connection() as conn:
            return await Transaction(conn, service).all("results")

    @app.get("/dead-letters")
    async def dead_letters():
        async with db.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id,reason,created_at,replayed_at FROM dead_letters ORDER BY created_at DESC"
            )
            return [
                {"id": row[0], "reason": row[1], "created_at": row[2], "replayed_at": row[3]}
                for row in await cur.fetchall()
            ]

    async def worker():
        queue = await channel.get_queue(service)
        async with queue.iterator() as messages:
            async for message in messages:
                await consume(db, service, module.handle, message)

    async def schedule():
        while True:
            try:
                async with db.pool.connection() as conn, conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(71234)")
                    tx = Transaction(conn, service)
                    await maintain(tx)
                    if module and hasattr(module, "sweep"):
                        await module.sweep(tx)
            except Exception:
                log.exception("scheduled sweep failed")
            await asyncio.sleep(60)

    tasks = [asyncio.create_task(relay(db, exchange, service)), asyncio.create_task(schedule())]
    if module:
        tasks.append(asyncio.create_task(worker()))
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    try:
        done, _ = await asyncio.wait([server_task, *tasks], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        server.should_exit = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server_task
        await broker.close()
        await db.pool.close()


if __name__ == "__main__":
    asyncio.run(main())
