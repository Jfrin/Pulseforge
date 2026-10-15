from __future__ import annotations

import asyncio
import os

from fastapi import Depends, FastAPI, HTTPException, Query

from .models import Chaos, ReviewBomb
from .state import MockState, paginate


def create_app(seed: int | None = None, base_games: int | None = None) -> FastAPI:
    state = MockState(
        seed if seed is not None else int(os.getenv("MOCK_SEED", "42")),
        base_games if base_games is not None else int(os.getenv("MOCK_BASE_GAMES", "200")),
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

    def page(items: list, cursor: str | None, limit: int) -> dict:
        try:
            chunk, nxt = paginate(items, cursor, limit)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        chunk = [dict(i) for i in chunk]
        if chunk and state.chaos_rng.random() < state.chaos.duplicate_rate:
            chunk.append(dict(state.chaos_rng.choice(chunk)))  # same record delivered twice
        return {"items": chunk, "next_cursor": nxt, "has_more": nxt is not None}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/games", dependencies=[Depends(chaos_gate)])
    def games(cursor: str | None = None, limit: int = Query(50, ge=1, le=500)) -> dict:
        return page(state.games, cursor, limit)

    @app.get("/reviews", dependencies=[Depends(chaos_gate)])
    def reviews(cursor: str | None = None, limit: int = Query(100, ge=1, le=1000),
                game_id: str | None = None) -> dict:
        items = state.reviews
        if game_id is not None:
            if game_id not in state.game_index:
                raise HTTPException(404, f"unknown game_id {game_id!r}")
            items = [r for r in items if r["game_id"] == game_id]
        return page(items, cursor, limit)

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
    def reset() -> dict:
        state.reset()
        return {"status": "reset"}

    return app


app = create_app()
