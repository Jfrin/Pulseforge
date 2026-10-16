import json
from datetime import UTC, datetime

from app.bronze import MinioBronze, NullBronze
from app.connectors import Page

NOW = datetime(2026, 10, 16, 1, 2, 3, 456789, tzinfo=UTC)


class FakeMinio:
    def __init__(self, exists=False):
        self.exists = exists
        self.made = []
        self.puts = []

    def bucket_exists(self, bucket):
        return self.exists

    def make_bucket(self, bucket):
        self.made.append(bucket)

    def put_object(self, bucket, name, data, length, content_type=None):
        self.puts.append({"bucket": bucket, "key": name, "body": data.read(), "length": length, "type": content_type})


def bronze(fake):
    return MinioBronze("unused:9000", "k", "s", "pulseforge", client=fake)


async def test_ensure_bucket_creates_it_only_when_missing():
    missing, present = FakeMinio(exists=False), FakeMinio(exists=True)
    await bronze(missing).ensure_bucket()
    await bronze(present).ensure_bucket()
    assert missing.made == ["pulseforge"] and present.made == []


async def test_land_page_stores_the_raw_page_with_request_context():
    fake = FakeMinio()
    page = Page(records=[{"id": 1, "x": "ü"}, {"id": 2}], next_cursor="3", has_more=True)
    key = await bronze(fake).land_page("src", fetched_cursor="0", page=page, fetched_at=NOW)

    assert key is not None and key.startswith("bronze/src/dt=2026-10-16/20261016T010203456789Z-")
    assert key.endswith(".json")
    (put,) = fake.puts
    assert put["bucket"] == "pulseforge" and put["key"] == key
    assert put["length"] == len(put["body"]) and put["type"] == "application/json"
    body = json.loads(put["body"])
    assert body["source"] == "src" and body["request"] == {"cursor": "0"}
    assert body["fetched_at"] == "2026-10-16T01:02:03.456789+00:00"
    assert body["response"] == {"records": page.records, "next_cursor": "3", "has_more": True}


async def test_empty_pages_are_not_landed():
    fake = FakeMinio()
    page = Page(records=[], next_cursor="9")
    key = await bronze(fake).land_page("src", fetched_cursor="9", page=page, fetched_at=NOW)
    assert key is None and fake.puts == []


async def test_every_landing_gets_a_unique_key_so_nothing_is_overwritten():
    fake = FakeMinio()
    page = Page(records=[{"id": 1}])
    a = await bronze(fake).land_page("src", fetched_cursor=None, page=page, fetched_at=NOW)
    b = await bronze(fake).land_page("src", fetched_cursor=None, page=page, fetched_at=NOW)
    assert a != b


async def test_null_bronze_lands_nothing():
    assert await NullBronze().land_page("src", fetched_cursor=None, page=Page(records=[{"id": 1}])) is None
