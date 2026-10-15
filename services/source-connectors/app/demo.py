"""`docker compose run source-connectors` walks a demo connector through the interface."""

import asyncio

from app.connectors import InMemoryConnector, SourceConnector, iter_pages


async def main() -> None:
    records = [{"external_id": f"app-{i}", "title": f"Game {i}"} for i in range(1, 8)]
    connector = InMemoryConnector("demo", records, page_size=3)
    assert isinstance(connector, SourceConnector)

    async for cursor, page in iter_pages(connector):
        ids = [r["external_id"] for r in page.records]
        print(f"cursor={cursor!r:5} -> {ids} next={page.next_cursor!r} has_more={page.has_more}")


if __name__ == "__main__":
    asyncio.run(main())
