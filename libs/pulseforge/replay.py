"""Run inside the failed service: python -m pulseforge.replay <dead-letter-id>."""

import asyncio
import os
import sys

import aio_pika

from .db import Database
from .events import Event


async def replay(event_id):
    db = Database(os.environ["DATABASE_URL"])
    await db.open()
    broker = await aio_pika.connect_robust(os.environ["AMQP_URL"])
    try:
        channel = await broker.channel(publisher_confirms=True)
        async with db.pool.connection() as conn, conn.transaction():
            cur = await conn.execute("SELECT event FROM dead_letters WHERE id=%s FOR UPDATE", (event_id,))
            row = await cur.fetchone()
            if not row:
                raise ValueError("dead letter not found")
            event = Event.model_validate(row[0])
            await conn.execute("DELETE FROM failures WHERE id=%s", (event.id,))
            await conn.execute("UPDATE dead_letters SET replayed_at=now() WHERE id=%s", (event_id,))
            # Target this consumer, so other services don't reprocess a replay.
            await channel.default_exchange.publish(
                aio_pika.Message(event.model_dump_json().encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                routing_key=os.environ["SERVICE"],
            )
    finally:
        await broker.close()
        await db.pool.close()


if __name__ == "__main__":
    asyncio.run(replay(sys.argv[1]))
