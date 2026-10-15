import pytest

from app.connectors import InMemoryConnector, Page, SourceConnector, iter_pages
from tests.contract import check_connector_contract


def make(n: int, page_size: int = 3) -> InMemoryConnector:
    return InMemoryConnector("mem", [{"id": i} for i in range(n)], page_size=page_size)


async def test_in_memory_satisfies_contract():
    records = await check_connector_contract(make(10))
    assert [r["id"] for r in records] == list(range(10))


async def test_empty_source_returns_single_empty_page():
    pages = [p async for _, p in iter_pages(make(0))]
    assert len(pages) == 1 and pages[0].records == [] and not pages[0].has_more


async def test_exact_multiple_of_page_size_has_no_trailing_page():
    pages = [p async for _, p in iter_pages(make(6, page_size=3))]
    assert len(pages) == 2 and not pages[-1].has_more


async def test_resume_from_checkpoint_cursor():
    pages = [(c, p) async for c, p in iter_pages(make(9), start_cursor="3")]
    assert pages[0][0] == "3"
    assert [r["id"] for _, p in pages for r in p.records] == list(range(3, 9))


async def test_max_pages_stops_early():
    pages = [p async for _, p in iter_pages(make(100), max_pages=2)]
    assert len(pages) == 2 and pages[-1].has_more


async def test_bad_cursor_raises():
    with pytest.raises(ValueError):
        await make(3).fetch("abc")


async def test_records_are_copies():
    src = [{"id": 1}]
    page = await InMemoryConnector("m", src).fetch(None)
    page.records[0]["id"] = 99
    assert src[0]["id"] == 1


async def test_stuck_cursor_is_detected():
    class Stuck:
        name = "stuck"

        async def fetch(self, cursor):
            return Page(records=[], next_cursor="x", has_more=True)

    with pytest.raises(RuntimeError):
        async for _ in iter_pages(Stuck(), start_cursor="x"):
            pass


def test_non_connector_fails_protocol():
    class NotOne:
        pass

    assert not isinstance(NotOne(), SourceConnector)
