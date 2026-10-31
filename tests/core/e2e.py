"""Black-box acceptance: live mock -> bronze -> every stage -> gold and review-bomb notification."""

import asyncio
import io
import json
import time

import httpx2 as httpx
import pyarrow.parquet as pq
from pulseforge.exports import ObjectStore


async def eventually(client, url, predicate, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        if predicate(data):
            return data
        await asyncio.sleep(1)
    raise AssertionError(f"Timed out waiting for {url}")


async def main():
    async with httpx.AsyncClient(timeout=20) as client:
        exports = await eventually(
            client, "http://export:8000/results", lambda d: any("parquet_key" in r for r in d.values())
        )
        first = next(r for r in exports.values() if "parquet_key" in r)
        store = ObjectStore()
        lifecycle = store.client.get_bucket_lifecycle(store.bucket)
        assert any(rule.rule_id == "pulseforge-bronze-retention" for rule in lifecycle.rules)
        response = store.client.get_object(store.bucket, first["parquet_key"])
        try:
            rows = pq.read_table(io.BytesIO(response.read())).to_pylist()
        finally:
            response.close()
            response.release_conn()
        assert rows and rows[0]["audit_id"] > 0
        lineage = json.loads(rows[0]["lineage"])["record"]
        assert lineage["bronze_key"].startswith("bronze/")
        assert "reviewer" not in rows[0] and "review" not in rows[0]
        game = (await client.get("http://mock-source:8000/games", params={"limit": 1})).json()["items"][0]
        injected = await client.post(
            "http://mock-source:8000/admin/review-bomb",
            json={
                "game_id": game["external_id"],
                "count": 600,
                "negative_share": 1.0,
                "spread_minutes": 10,
            },
        )
        injected.raise_for_status()
        alerts = await eventually(
            client, "http://analytics:8000/results", lambda d: any(a["kind"] == "review_bomb" for a in d.values())
        )
        alert = next(a for a in alerts.values() if a["kind"] == "review_bomb")
        assert alert["review_count"] >= 100 and alert["negative_share"] >= 0.7
        await eventually(client, "http://notification:8000/results", lambda d: alert["alert_id"] in d)
        await eventually(
            client,
            "http://export:8000/results",
            lambda d: any(r.get("alert_id") == alert["alert_id"] for r in d.values()),
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "gold_rows": len(rows),
                    "bronze_key": lineage["bronze_key"],
                    "alert_id": alert["alert_id"],
                    "review_count": alert["review_count"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
