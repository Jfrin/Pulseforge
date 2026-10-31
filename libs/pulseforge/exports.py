from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os

import pyarrow as pa
import pyarrow.parquet as pq
from minio import Minio

from .events import utcnow

SCHEMA = pa.schema(
    [
        ("product_id", pa.string()),
        ("title", pa.string()),
        ("developer", pa.string()),
        ("publisher", pa.string()),
        ("price_cents", pa.int64()),
        ("currency", pa.string()),
        ("player_count", pa.int64()),
        ("review_count", pa.int64()),
        ("negative_count", pa.int64()),
        ("negative_share", pa.float64()),
        ("controversy_score", pa.float64()),
        ("source", pa.string()),
        ("batch_id", pa.string()),
        ("governance_flags", pa.string()),
        ("tombstone", pa.bool_()),
        ("audit_id", pa.int64()),
        ("lineage", pa.string()),
        ("confidence", pa.string()),
    ]
)


def gold_rows(payload):
    rows = []
    for product in payload["products"]:
        row = {k: product.get(k) for k in SCHEMA.names}
        row.update(
            batch_id=payload["batch_id"],
            governance_flags=json.dumps(product["governance"]["flags"]),
            tombstone=product["governance"]["tombstone"],
            audit_id=product["governance"]["audit_id"],
            lineage=json.dumps({"record": product.get("lineage"), "fields": product.get("field_lineage", {})}),
            confidence=json.dumps(product.get("confidence", {})),
        )
        rows.append(row)
    return rows


def encode(rows):
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=SCHEMA.names)
    writer.writeheader()
    writer.writerows(rows)
    parquet = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), parquet, compression="snappy")
    return text.getvalue().encode(), parquet.getvalue()


class ObjectStore:
    def __init__(self):
        self.client = Minio(
            os.environ["MINIO_ENDPOINT"],
            access_key=os.environ["MINIO_ACCESS_KEY"],
            secret_key=os.environ["MINIO_SECRET_KEY"],
            secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
        )
        self.bucket = os.getenv("MINIO_BUCKET", "pulseforge")

    async def put(self, key, body, content_type):
        def upload():
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
            self.client.put_object(self.bucket, key, io.BytesIO(body), len(body), content_type=content_type)

        await asyncio.to_thread(upload)


async def handle(tx, event):
    store = ObjectStore()
    suffix = hashlib.sha256(event.id.encode()).hexdigest()[:24]
    if event.type == "AnomalyDetected":
        key = f"gold/alerts/dt={event.occurred_at:%Y-%m-%d}/{suffix}.json"
        await store.put(key, json.dumps(event.payload).encode(), "application/json")
        await tx.put("results", suffix, {"alert_key": key, "alert_id": event.payload["alert_id"]})
        return
    rows = gold_rows(event.payload)
    csv_body, parquet_body = await asyncio.to_thread(encode, rows)
    prefix = f"gold/products/dt={event.occurred_at:%Y-%m-%d}/batch-{suffix}"
    await store.put(prefix + ".csv", csv_body, "text/csv")
    await store.put(prefix + ".parquet", parquet_body, "application/vnd.apache.parquet")
    result = {
        "batch_id": event.payload["batch_id"],
        "record_count": len(rows),
        "csv_key": prefix + ".csv",
        "parquet_key": prefix + ".parquet",
        "exported_at": utcnow().isoformat(),
    }
    await tx.put("results", event.payload["batch_id"], result)
    await tx.emit("ExportReady", result)
