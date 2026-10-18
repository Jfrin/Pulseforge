import asyncio
import time

import httpx2 as httpx
import pytest

from app.connectors.sse import SseConnector, parse_sse


async def lines(*items):
    for item in items:
        yield item


async def events(*items):
    return [e async for e in parse_sse(lines(*items))]


# ---------------- parser ----------------


async def test_a_blank_line_dispatches_an_event_with_default_type():
    (ev,) = await events("data: hello", "")
    assert (ev.id, ev.event, ev.data) == (None, "message", "hello")


async def test_multiline_data_is_joined_with_newlines():
    (ev,) = await events("data: one", "data: two", "")
    assert ev.data == "one\ntwo"


async def test_fields_comments_and_one_leading_space():
    (ev,) = await events(": keep-alive", "event: price", "id: 7", "data:  spaced", "")
    assert (ev.id, ev.event, ev.data) == ("7", "price", " spaced")


async def test_the_last_event_id_persists_across_events():
    a, b = await events("id: 1", "data: x", "", "data: y", "")
    assert a.id == "1" and b.id == "1"


async def test_events_without_data_are_not_dispatched_and_type_resets():
    out = await events("event: ping", "", "data: x", "")
    assert len(out) == 1 and out[0].event == "message"


async def test_an_unterminated_event_is_not_dispatched():
    assert await events("data: half") == []


# ---------------- connector ----------------


def client_for(body, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def sse(body, seen=None, **kw):
    return SseConnector("sse-demo", "https://example.test/events", client=client_for(body, seen), **kw)


async def test_events_become_envelope_records_and_the_last_id_is_the_resume_cursor():
    body = 'id: 1\nevent: review\ndata: {"id": 10}\n\nid: 2\ndata: plain text\n\n'
    page = await sse(body).fetch(None)
    assert page.records == [
        {"event_id": "1", "event": "review", "data": {"id": 10}},
        {"event_id": "2", "event": "message", "data": "plain text"},
    ]
    assert page.next_cursor == "2" and page.has_more is False


async def test_the_cursor_is_sent_as_last_event_id_and_the_accept_header_is_set():
    seen = []
    await sse("id: 3\ndata: x\n\n", seen).fetch("2")
    (request,) = seen
    assert request.headers["Last-Event-ID"] == "2" and request.headers["Accept"] == "text/event-stream"


async def test_no_last_event_id_on_the_first_connection():
    seen = []
    await sse("data: x\n\n", seen).fetch(None)
    assert "Last-Event-ID" not in seen[0].headers


async def test_hitting_max_events_asks_to_continue_immediately():
    body = "".join(f"id: {i}\ndata: {i}\n\n" for i in range(1, 6))
    page = await sse(body, max_events=3).fetch(None)
    assert len(page.records) == 3 and page.next_cursor == "3" and page.has_more is True


async def test_streams_without_ids_cannot_resume():
    page = await sse("data: a\n\ndata: b\n\n", max_events=1).fetch(None)
    assert len(page.records) == 1 and page.next_cursor is None and page.has_more is False


async def test_an_empty_stream_keeps_the_cursor_it_was_given():
    page = await sse("").fetch("9")
    assert page.records == [] and page.next_cursor == "9" and page.has_more is False


async def test_the_listening_window_ends_a_quiet_stream_and_keeps_what_arrived():
    async def slow():
        yield b"id: 1\ndata: first\n\n"
        await asyncio.sleep(10)
        yield b"id: 2\ndata: never\n\n"

    def handler(request):
        return httpx.Response(200, content=slow(), headers={"content-type": "text/event-stream"})

    c = SseConnector(
        "sse-demo",
        "https://example.test/events",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_seconds=0.3,
    )
    start = time.monotonic()
    page = await c.fetch(None)
    assert time.monotonic() - start < 3
    assert [r["event_id"] for r in page.records] == ["1"] and page.next_cursor == "1"


async def test_http_errors_are_raised_not_swallowed():
    c = SseConnector(
        "sse-demo",
        "https://example.test/events",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(503))),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await c.fetch(None)


def test_constructor_validation():
    with pytest.raises(ValueError):
        SseConnector("s", "https://x", max_events=0)
    with pytest.raises(ValueError):
        SseConnector("s", "https://x", max_seconds=0)
