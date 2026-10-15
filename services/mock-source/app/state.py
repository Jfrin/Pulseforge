"""Deterministic fake game-store data (seeded), with deliberate naming noise and review bombs.

Reviews live in one append-only list ordered by created_at, so an offset cursor is stable
and incremental polling ("everything after my cursor") works.
"""
from __future__ import annotations

import base64
import random
from datetime import datetime, timedelta, timezone

from faker import Faker

from .models import Chaos

EDITIONS = ["Deluxe Edition", "Definitive Edition", "Remastered", "Game of the Year Edition",
            "Complete Edition", "Anniversary Edition"]
DLC_KINDS = ["Season Pass", "Expansion Pack", "Soundtrack", "Cosmetic Pack", "Character Pack"]
STUDIO_WORDS = ["Interactive", "Studios", "Games", "Entertainment", "Labs"]
FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso(dt: datetime) -> str:
    return dt.strftime(FMT)


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, value = raw.split(":", 1)
        offset = int(value)
        if prefix != "o" or offset < 0:
            raise ValueError
        return offset
    except Exception:
        raise ValueError(f"invalid cursor: {cursor!r}") from None


def paginate(items: list, cursor: str | None, limit: int) -> tuple[list, str | None]:
    offset = decode_cursor(cursor)
    chunk = items[offset : offset + limit]
    end = offset + len(chunk)
    return chunk, (encode_cursor(end) if end < len(items) else None)


class MockState:
    def __init__(self, seed: int, base_games: int) -> None:
        self.seed = seed
        self.base_games = base_games
        self.reset()

    def reset(self) -> None:
        self.rng = random.Random(self.seed)
        self.fake = Faker()
        self.fake.seed_instance(self.seed)
        self.chaos = Chaos()
        self.chaos_rng = random.Random()  # failure injection is intentionally not seeded
        self.games, self.ground_truth = self._build_catalog()
        self.game_index = {g["external_id"]: g for g in self.games}
        self._reviewers = [str(76561198000000000 + self.rng.randrange(10**9)) for _ in range(3000)]
        self._sentences = [self.fake.sentence(nb_words=12) for _ in range(200)]
        self._review_seq = 0
        self.reviews = self._build_reviews()

    # ---- catalog -------------------------------------------------------
    def _noisy_company(self, name: str) -> str:
        return self.rng.choice([name, name + " Inc.", name + " Ltd.", name.upper(), name.replace(" ", "")])

    def _edition_title(self, base: str) -> str:
        suffix = self.rng.choice(EDITIONS)
        fmt = self.rng.choice(["{t}: {s}", "{t} - {s}", "{t} ({s})", "{t} {s}", "{T} {s}"])
        return fmt.format(t=base, T=base.upper(), s=suffix)

    def _build_catalog(self):
        rng, fake = self.rng, self.fake
        used: set[str] = set()
        rows: list[dict] = []
        for n in range(self.base_games):
            while True:
                title = " ".join(w.title() for w in fake.words(nb=rng.choice([2, 2, 3]), unique=True))
                if title not in used:
                    used.add(title)
                    break
            dev = f"{fake.last_name()} {rng.choice(STUDIO_WORDS)}"
            pub = dev if rng.random() < 0.6 else f"{fake.last_name()} {rng.choice(STUDIO_WORDS)}"
            price = rng.choice([499, 999, 1499, 1999, 2999, 3999, 5999])
            released = fake.date_between("-8y", "-60d").isoformat()
            canonical = f"c-{n + 1:04d}"

            def row(t, kind, p, players_scale=1.0, dev_name=dev, pub_name=pub):
                return {
                    "canonical": canonical, "title": t, "type": kind,
                    "developer": dev_name, "publisher": pub_name,
                    "price_cents": p, "currency": "USD", "release_date": released,
                    "player_count": int(rng.lognormvariate(6, 1.5) * players_scale),
                }

            rows.append(row(title, "game", price))
            if rng.random() < 0.5:
                rows.append(row(self._edition_title(title), "game", price + 1000,
                                dev_name=self._noisy_company(dev), pub_name=self._noisy_company(pub)))
            if rng.random() < 0.3:
                rows.append(row(f"{title} - {rng.choice(DLC_KINDS)}", "dlc", rng.randint(299, 1499),
                                0.2, self._noisy_company(dev), self._noisy_company(pub)))
            if rng.random() < 0.1:
                rows.append(row(f"{title} + Expansion Bundle", "bundle", int(price * 1.4),
                                0.3, self._noisy_company(dev), self._noisy_company(pub)))

        rng.shuffle(rows)  # variants of one game land on different pages
        now = _iso(datetime.now(timezone.utc))
        games, truth = [], {}
        for i, r in enumerate(rows, start=1):
            ext = f"g-{i:05d}"
            truth[ext] = r.pop("canonical")
            games.append({"external_id": ext, **r, "updated_at": now})
        return games, truth

    # ---- reviews -------------------------------------------------------
    def _new_review(self, game_id: str, voted_up: bool, ts: datetime, reviewer: str) -> dict:
        self._review_seq += 1
        return {
            "review_id": f"r-{self._review_seq:07d}",
            "game_id": game_id,
            "reviewer_id": reviewer,
            "voted_up": voted_up,
            "text": self.rng.choice(self._sentences),
            "playtime_hours": round(self.rng.uniform(0.2, 400), 1),
            "created_at": _iso(ts),
        }

    def _build_reviews(self) -> list[dict]:
        rng = self.rng
        newest_allowed = datetime.now(timezone.utc) - timedelta(hours=1)
        drafts = []
        for g in self.games:
            n = rng.randint(0, 200) if g["type"] == "game" else rng.randint(0, 30)
            p_pos = rng.uniform(0.6, 0.95)
            for _ in range(n):
                ts = newest_allowed - timedelta(seconds=rng.uniform(0, 30 * 86400))
                drafts.append((ts, g["external_id"], rng.random() < p_pos, rng.choice(self._reviewers)))
        drafts.sort(key=lambda d: d[0])
        return [self._new_review(gid, up, ts, who) for ts, gid, up, who in drafts]

    def inject_bomb(self, game_id: str, count: int, negative_share: float, spread_minutes: int) -> int:
        now = datetime.now(timezone.utc)
        last = datetime.strptime(self.reviews[-1]["created_at"], FMT).replace(tzinfo=timezone.utc) \
            if self.reviews else now - timedelta(days=1)
        start = max(now - timedelta(minutes=spread_minutes), last)
        span = max((now - start).total_seconds(), 0)
        stamps = sorted(start + timedelta(seconds=self.rng.uniform(0, span)) for _ in range(count))
        for ts in stamps:
            reviewer = str(76561199000000000 + self.rng.randrange(10**9))
            self.reviews.append(self._new_review(game_id, self.rng.random() >= negative_share, ts, reviewer))
        return count
