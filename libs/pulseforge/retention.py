"""Expire delivered event bodies while retaining lineage and an audit tombstone."""

import os


async def maintain(tx):
    days = int(os.getenv("EVENT_RETENTION_DAYS", "30"))
    if days < 1:
        raise ValueError("EVENT_RETENTION_DAYS must be positive")
    cur = await tx.conn.execute(
        "UPDATE outbox SET payload=jsonb_build_object('batch_id',payload->'batch_id',"
        "'source',payload->'source','bronze_keys',payload->'bronze_keys','retention_expired',true) "
        "WHERE published_at IS NOT NULL AND created_at < now() - %s * interval '1 day' "
        "AND NOT payload ? 'retention_expired' RETURNING id",
        (days,),
    )
    rows = await cur.fetchall()
    if rows:
        await tx.audit("outbox-retention", {"reason": "retention_expired", "count": len(rows), "days": days})
    if tx.service == "source-connectors":
        cur = await tx.conn.execute("SELECT to_regclass('webhook_inbox')")
        if (await cur.fetchone())[0]:
            cur = await tx.conn.execute(
                "UPDATE webhook_inbox w SET payload=jsonb_build_object('retention_expired',true) "
                "FROM checkpoints c WHERE c.source=w.source AND "
                "w.seq <= CASE WHEN c.cursor ~ '^[0-9]+$' THEN c.cursor::bigint ELSE -1 END "
                "AND w.received_at < now() - %s * interval '1 day' AND NOT w.payload ? 'retention_expired' "
                "RETURNING w.seq",
                (days,),
            )
            rows = await cur.fetchall()
            if rows:
                await tx.audit("inbox-retention", {"reason": "retention_expired", "count": len(rows), "days": days})
