import pytest

from app.connectors.inbox import InboxConnector
from tests.contract import check_connector_contract


class FakeReader:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def read(self, source, after_seq, limit):
        self.calls.append((source, after_seq, limit))
        return [r for r in self.rows if r[0] > after_seq][:limit]


ROWS = [(5, {"id": "a"}), (9, {"id": "b"}), (12, {"id": "c"}), (20, {"id": "d"}), (31, {"id": "e"})]


async def test_satisfies_the_connector_contract():
    records = await check_connector_contract(InboxConnector("webhook-demo", FakeReader(ROWS), page_size=2))
    assert [r["id"] for r in records] == list("abcde")


async def test_pages_follow_sequence_numbers_not_row_counts():
    c = InboxConnector("webhook-demo", FakeReader(ROWS), page_size=2)
    first = await c.fetch(None)
    assert [r["id"] for r in first.records] == ["a", "b"] and first.next_cursor == "9" and first.has_more
    second = await c.fetch(first.next_cursor)
    assert [r["id"] for r in second.records] == ["c", "d"] and second.next_cursor == "20" and second.has_more
    last = await c.fetch(second.next_cursor)
    assert [r["id"] for r in last.records] == ["e"] and last.next_cursor == "31" and not last.has_more


async def test_caught_up_keeps_a_resume_token_so_new_rows_are_picked_up_later():
    reader = FakeReader(ROWS)
    c = InboxConnector("webhook-demo", reader, page_size=10)
    page = await c.fetch(None)
    assert page.next_cursor == "31" and not page.has_more
    empty = await c.fetch("31")
    assert empty.records == [] and empty.next_cursor == "31"
    reader.rows.append((40, {"id": "f"}))
    assert [r["id"] for r in (await c.fetch("31")).records] == ["f"]


async def test_an_empty_inbox_starts_at_zero():
    page = await InboxConnector("webhook-demo", FakeReader([])).fetch(None)
    assert page.records == [] and page.next_cursor == "0" and not page.has_more


async def test_reads_one_extra_row_to_know_whether_more_exist():
    reader = FakeReader(ROWS)
    await InboxConnector("webhook-demo", reader, page_size=2).fetch(None)
    assert reader.calls == [("webhook-demo", 0, 3)]


@pytest.mark.parametrize("cursor", ["abc", "-1", "1.5"])
async def test_invalid_cursors_raise(cursor):
    with pytest.raises(ValueError):
        await InboxConnector("webhook-demo", FakeReader(ROWS)).fetch(cursor)


def test_page_size_must_be_positive():
    with pytest.raises(ValueError):
        InboxConnector("webhook-demo", FakeReader([]), page_size=0)
