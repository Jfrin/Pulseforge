import json

from websockets.asyncio.server import serve

from app.connectors.websocket import WebSocketConnector


async def test_websocket_resume_and_page_boundaries():
    cursors = []

    async def feed(socket):
        request = json.loads(await socket.recv())
        cursors.append(request["cursor"])
        start = int(request["cursor"] or "0")
        for number in range(start + 1, 4):
            await socket.send(json.dumps({"id": str(number), "data": {"external_id": number}}))

    async with serve(feed, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        connector = WebSocketConnector("websocket-test", f"ws://127.0.0.1:{port}", max_events=2)
        first = await connector.fetch(None)
        assert first.has_more and first.next_cursor == "2"
        second = await connector.fetch(first.next_cursor)
        assert not second.has_more and second.next_cursor == "3"
        assert cursors == [None, "2"]
        assert second.records == [{"event_id": "3", "data": {"external_id": 3}}]
