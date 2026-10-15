"""Reusable contract check. Every future connector's tests should call this."""
from app.connectors import Page, SourceConnector, iter_pages


async def check_connector_contract(connector: SourceConnector) -> list[dict]:
    assert isinstance(connector, SourceConnector)
    assert isinstance(connector.name, str) and connector.name

    all_records: list[dict] = []
    seen_cursors: list[str | None] = []
    async for cursor, page in iter_pages(connector, max_pages=1000):
        assert isinstance(page, Page)
        assert all(isinstance(r, dict) for r in page.records)
        assert cursor not in seen_cursors, "cursor repeated"
        seen_cursors.append(cursor)
        if page.has_more:
            assert page.next_cursor is not None
        all_records.extend(page.records)

    # Re-polling a cursor must return the same data (idempotent upstream).
    for cursor in seen_cursors:
        a = await connector.fetch(cursor)
        b = await connector.fetch(cursor)
        assert a == b
    return all_records
