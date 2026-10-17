import pytest

from app.connectors import CsvDirectoryConnector, Page
from tests.contract import check_connector_contract


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


def conn(tmp_path, page_size=2):
    return CsvDirectoryConnector("csv", tmp_path, page_size=page_size)


async def pages(c):
    out, cursor = [], None
    while True:
        page = await c.fetch(cursor)
        out.append(page)
        if not page.has_more:
            return out
        cursor = page.next_cursor


async def all_records(c):
    return [r for p in await pages(c) for r in p.records]


async def test_satisfies_the_connector_contract(tmp_path):
    write(tmp_path / "a.csv", "id,v\n1,a\n2,b\n3,c\n4,d\n5,e\n")
    write(tmp_path / "b.csv", "id,v\n6,f\n")
    records = await check_connector_contract(conn(tmp_path))
    assert [r["id"] for r in records] == ["1", "2", "3", "4", "5", "6"]


async def test_cells_are_raw_strings(tmp_path):
    write(tmp_path / "a.csv", "id,price\n1,0010\n2,3.50\n")
    assert await all_records(conn(tmp_path)) == [{"id": "1", "price": "0010"}, {"id": "2", "price": "3.50"}]


async def test_excel_bom_is_stripped_from_the_first_header(tmp_path):
    write(tmp_path / "a.csv", "\ufeffexternal_id,title\nA,Alpha\n")
    assert await all_records(conn(tmp_path)) == [{"external_id": "A", "title": "Alpha"}]


async def test_quoted_commas_and_newlines_survive_page_boundaries(tmp_path):
    write(tmp_path / "a.csv", 'id,note\n1,"line one\nline two"\n2,plain\n3,"x, y"\n')
    got = await all_records(conn(tmp_path, page_size=1))
    assert got == [
        {"id": "1", "note": "line one\nline two"},
        {"id": "2", "note": "plain"},
        {"id": "3", "note": "x, y"},
    ]


async def test_short_rows_get_none_and_long_rows_keep_the_extras(tmp_path):
    write(tmp_path / "a.csv", "a,b\n1\n1,2,3,4\n")
    assert await all_records(conn(tmp_path)) == [
        {"a": "1", "b": None},
        {"a": "1", "b": "2", "_extra": ["3", "4"]},
    ]


async def test_blank_lines_are_skipped(tmp_path):
    write(tmp_path / "a.csv", "a\n1\n\n2\n")
    assert [r["a"] for r in await all_records(conn(tmp_path))] == ["1", "2"]


async def test_files_are_read_in_name_order_and_a_page_never_mixes_files(tmp_path):
    write(tmp_path / "b.csv", "id\nb1\n")
    write(tmp_path / "a.csv", "id\na1\na2\na3\n")
    ps = await pages(conn(tmp_path, page_size=2))
    assert [[r["id"] for r in p.records] for p in ps] == [["a1", "a2"], ["a3"], ["b1"]]
    assert ps[1].next_cursor == "b.csv@0" and ps[-1].next_cursor is None and not ps[-1].has_more


async def test_only_matching_files_are_read(tmp_path):
    write(tmp_path / "a.csv", "id\n1\n")
    write(tmp_path / "notes.txt", "id\n2\n")
    assert [r["id"] for r in await all_records(conn(tmp_path))] == ["1"]


async def test_a_file_dropped_in_later_is_picked_up_on_the_next_pass(tmp_path):
    write(tmp_path / "a.csv", "id\n1\n")
    c = conn(tmp_path)
    assert len(await all_records(c)) == 1
    write(tmp_path / "z.csv", "id\n2\n")
    assert [r["id"] for r in await all_records(c)] == ["1", "2"]


async def test_header_only_and_empty_files_yield_nothing_but_do_not_block_the_next_file(tmp_path):
    write(tmp_path / "a.csv", "id\n")
    write(tmp_path / "b.csv", "")
    write(tmp_path / "c.csv", "id\n7\n")
    assert [r["id"] for r in await all_records(conn(tmp_path))] == ["7"]


async def test_empty_or_missing_directory_is_an_empty_finished_page(tmp_path):
    assert await conn(tmp_path).fetch(None) == Page(records=[], next_cursor=None, has_more=False)
    assert (await conn(tmp_path / "nope").fetch(None)).has_more is False


async def test_resume_cursor_for_a_vanished_file_continues_with_the_next_one(tmp_path):
    write(tmp_path / "a.csv", "id\n1\n")
    write(tmp_path / "c.csv", "id\n3\n")
    page = await conn(tmp_path).fetch("b.csv@5")
    assert [r["id"] for r in page.records] == ["3"]
    assert (await conn(tmp_path).fetch("d.csv@0")).records == []


@pytest.mark.parametrize("cursor", ["garbage", "a.csv@-1", "a.csv@x", "@5"])
async def test_invalid_cursors_raise(tmp_path, cursor):
    write(tmp_path / "a.csv", "id\n1\n")
    with pytest.raises(ValueError):
        await conn(tmp_path).fetch(cursor)


def test_page_size_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        CsvDirectoryConnector("csv", tmp_path, page_size=0)
