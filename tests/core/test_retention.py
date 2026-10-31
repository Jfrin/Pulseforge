import pytest
from pulseforge.retention import maintain


@pytest.mark.asyncio
async def test_retention_only_expires_old_delivered_bodies_and_audits(tx):
    await tx.emit("BatchCreated", {"batch_id": "old", "records": [{"reviewer_id": "private"}]})
    await tx.conn.execute("UPDATE outbox SET created_at=now()-interval '31 days', published_at=now()")
    await tx.emit("BatchCreated", {"batch_id": "pending", "records": [{"reviewer_id": "pending"}]})
    await tx.conn.execute("UPDATE outbox SET created_at=now()-interval '31 days' WHERE published_at IS NULL")
    await maintain(tx)
    cur = await tx.conn.execute("SELECT payload FROM outbox ORDER BY id")
    old, pending = [row[0] for row in await cur.fetchall()]
    assert old["batch_id"] == "old" and old["retention_expired"] and "records" not in old
    assert pending["records"][0]["reviewer_id"] == "pending"
    cur = await tx.conn.execute(
        "SELECT decision FROM audit WHERE record_id='outbox-retention' ORDER BY id DESC LIMIT 1"
    )
    assert (await cur.fetchone())[0]["count"] == 1
