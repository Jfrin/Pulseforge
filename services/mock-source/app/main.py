from __future__ import annotations

import asyncio
import os
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query

from .models import Chaos, ReviewBomb
from .state import MockState, decode_cursor, encode_cursor, paginate


def create_app(seed: int | None = None, base_games: int | None = None, difficulty: str | None = None) -> FastAPI:
    state = MockState(
        seed if seed is not None else int(os.getenv("MOCK_SEED", "42")),
        base_games if base_games is not None else int(os.getenv("MOCK_BASE_GAMES", "200")),
        difficulty if difficulty is not None else os.getenv("MOCK_DIFFICULTY", "easy"),
    )
    app = FastAPI(title="PulseForge mock source")

    async def chaos_gate() -> None:
        c = state.chaos
        if c.latency_ms:
            await asyncio.sleep(c.latency_ms / 1000)
        roll = state.chaos_rng.random()
        if roll < c.rate_limit_rate:
            raise HTTPException(429, "rate limited", headers={"Retry-After": str(c.retry_after_s)})
        if roll < c.rate_limit_rate + c.error_rate:
            raise HTTPException(500, "injected failure")

    def page(items: list, cursor: str | None, limit: int, resume: bool = False) -> dict:
        """`resume=True` (append-only feeds): the last page still carries a next_cursor,
        a resume token pointing at the end, so a poller can pick up new items later.
        Snapshot catalogs return next_cursor=None at the end: start over next pass."""
        try:
            chunk, nxt = paginate(items, cursor, limit)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        token = nxt
        if nxt is None and resume:
            token = encode_cursor(decode_cursor(cursor) + len(chunk))
        chunk = [dict(i) for i in chunk]
        if chunk and state.chaos_rng.random() < state.chaos.duplicate_rate:
            chunk.append(dict(state.chaos_rng.choice(chunk)))  # same record delivered twice
        return {"items": chunk, "next_cursor": token, "has_more": nxt is not None}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/games", dependencies=[Depends(chaos_gate)])
    def games(cursor: str | None = None, limit: int = Query(50, ge=1, le=500)) -> dict:
        return page(state.games, cursor, limit)

    @app.get("/reviews", dependencies=[Depends(chaos_gate)])
    def reviews(cursor: str | None = None, limit: int = Query(100, ge=1, le=1000), game_id: str | None = None) -> dict:
        items = state.reviews
        if game_id is not None:
            if game_id not in state.game_index:
                raise HTTPException(404, f"unknown game_id {game_id!r}")
            items = [r for r in items if r["game_id"] == game_id]
        return page(items, cursor, limit, resume=True)

    # ---- admin: control the scenario -----------------------------------
    @app.get("/admin/chaos")
    def get_chaos() -> Chaos:
        return state.chaos

    @app.post("/admin/chaos")
    def set_chaos(chaos: Chaos) -> Chaos:
        state.chaos = chaos
        return chaos

    @app.post("/admin/review-bomb")
    def review_bomb(b: ReviewBomb) -> dict:
        if b.game_id not in state.game_index:
            raise HTTPException(404, f"unknown game_id {b.game_id!r}")
        n = state.inject_bomb(b.game_id, b.count, b.negative_share, b.spread_minutes)
        return {"injected": n, "game_id": b.game_id}

    @app.get("/admin/ground-truth")
    def ground_truth() -> dict:
        """external_id -> canonical product id, for entity-resolution precision/recall."""
        return state.ground_truth

    @app.post("/admin/reset")
    def reset(difficulty: Literal["easy", "hard"] | None = None) -> dict:
        """Reseed the catalog; `?difficulty=hard` switches to the harder entity-resolution catalog."""
        state.reset(difficulty)
        return {"status": "reset", "difficulty": state.difficulty}

    return app


app = create_app()
