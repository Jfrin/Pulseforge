from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

import uvicorn
from fastapi import FastAPI


class _Server(uvicorn.Server):
    """uvicorn without its own signal handling: our main() owns SIGINT/SIGTERM."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:  # uvicorn >= 0.29
        yield

    def install_signal_handlers(self) -> None:  # older uvicorn
        pass


async def serve(app: FastAPI, port: int, stop: asyncio.Event, host: str = "0.0.0.0") -> None:
    server = _Server(uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False))

    async def watch() -> None:
        await stop.wait()
        server.should_exit = True

    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
