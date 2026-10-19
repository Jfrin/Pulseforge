"""Deterministic fake game-store data (seeded), with deliberate naming noise and review bombs.

Reviews live in one append-only list ordered by created_at, so an offset cursor is stable
and incremental polling ("everything after my cursor") works.
"""

from __future__ import annotations

import base64
import random
from datetime import UTC, date, datetime, timedelta

from faker import Faker

from .models import Chaos

EDITIONS = [
    "Deluxe Edition",
    "Definitive Edition",
    "Remastered",
    "Game of the Year Edition",
    "Complete Edition",
    "Anniversary Edition",
]
DLC_KINDS = ["Season Pass", "Expansion Pack", "Soundtrack", "Cosmetic Pack", "Character Pack"]
STUDIO_WORDS = ["Interactive", "Studios", "Games", "Entertainment", "Labs"]
# Hard mode: edition names a title-cleaning vocabulary will not know, and spin-off subtitles
UNKNOWN_EDITIONS = [
    "Platinum Edition", "Legacy Edition", "Royal Edition", "Champion Edition", "Directors Edition",
    "Prestige Edition",
]  # fmt: skip
SPINOFF_WORDS = ["Wars", "Legends", "Tactics", "Online", "Origins", "Arena", "Unleashed"]
DIFFICULTIES = ("easy", "hard")
ROMAN = {2: "II", 3: "III"}
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
    """`difficulty="hard"` adds what real catalogs have and the easy mode lacks: sequels, spin-offs, unrelated
    games sharing a title, typos, unknown edition names, remasters by other studios. Easy mode draws exactly the
    random numbers it always did, so its catalog is unchanged."""

    def __init__(self, seed: int, base_games: int, difficulty: str = "easy") -> None:
        self.seed = seed
        self.base_games = base_games
        self.difficulty = self._check(difficulty)
        self.reset()

    @staticmethod
    def _check(difficulty: str) -> str:
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"difficulty must be one of {DIFFICULTIES}")
        return difficulty

    @property
    def hard(self) -> bool:
        return self.difficulty == "hard"

    def reset(self, difficulty: str | None = None) -> None:
        if difficulty is not None:
            self.difficulty = self._check(difficulty)
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

    def _edition_title(self, base: str, editions: list[str] = EDITIONS) -> str:
        suffix = self.rng.choice(editions)
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

            def vbase(title=title):
                return self._variant_base(title) if self.hard else title

            def row(t, kind, p, players_scale=1.0, dev_name=dev, pub_name=pub, canonical=canonical, released=released):
                return {
                    "canonical": canonical,
                    "title": t,
                    "type": kind,
                    "developer": dev_name,
                    "publisher": pub_name,
                    "price_cents": p,
                    "currency": "USD",
                    "release_date": released,
                    "player_count": int(rng.lognormvariate(6, 1.5) * players_scale),
                }

            rows.append(row(title, "game", price))
            if rng.random() < 0.5:
                rows.append(
                    row(
                        self._edition_title(vbase()),
                        "game",
                        price + 1000,
                        dev_name=self._noisy_company(dev),
                        pub_name=self._noisy_company(pub),
                    )
                )
            if rng.random() < 0.3:
                rows.append(
                    row(
                        f"{vbase()} - {rng.choice(DLC_KINDS)}",
                        "dlc",
                        rng.randint(299, 1499),
                        0.2,
                        self._noisy_company(dev),
                        self._noisy_company(pub),
                    )
                )
            if rng.random() < 0.1:
                rows.append(
                    row(
                        f"{vbase()} + Expansion Bundle",
                        "bundle",
                        int(price * 1.4),
                        0.3,
                        self._noisy_company(dev),
                        self._noisy_company(pub),
                    )
                )

            if self.hard:
                rows.extend(self._hard_rows(canonical, title, dev, price, released, row))

        rng.shuffle(rows)  # variants of one game land on different pages
        now = _iso(datetime.now(UTC))
        games, truth = [], {}
        for i, r in enumerate(rows, start=1):
            ext = f"g-{i:05d}"
            truth[ext] = r.pop("canonical")
            games.append({"external_id": ext, **r, "updated_at": now})
        return games, truth

    # ---- hard mode -----------------------------------------------------
    def _typo(self, text: str) -> str:
        i = self.rng.randrange(1, len(text) - 1)
        kind = self.rng.choice(["drop", "swap", "double"])
        if kind == "drop":
            return text[:i] + text[i + 1 :]
        if kind == "swap":
            return text[:i] + text[i + 1] + text[i] + text[i + 2 :]
        return text[:i] + text[i] + text[i:]

    def _variant_base(self, title: str) -> str:
        """The base title as a variant listing might spell it: a typo, or a leading "The"."""
        roll = self.rng.random()
        if roll < 0.15:
            return self._typo(title)
        if roll < 0.25:
            return f"The {title}"
        return title

    def _numeral(self, number: int, style: str) -> str:
        return str(number) if style == "arabic" else ROMAN[number]

    def _hard_rows(self, canonical: str, title: str, dev: str, price: int, released: str, row) -> list[dict]:
        rng, fake = self.rng, self.fake
        base_date = date.fromisoformat(released)
        out: list[dict] = []

        def later(years_lo: int, years_hi: int) -> str:
            return (base_date + timedelta(days=rng.randint(365 * years_lo, 365 * years_hi))).isoformat()

        def other_studio() -> str:
            return f"{fake.last_name()} {rng.choice(STUDIO_WORDS)}"

        # a sequel (a DIFFERENT product), sometimes with its own edition spelled in the other numeral style
        if rng.random() < 0.25:
            number, style = rng.choice([2, 3]), rng.choice(["arabic", "roman"])
            seq_id, seq_date = f"{canonical}-seq", later(1, 4)
            out.append(
                row(f"{title} {self._numeral(number, style)}", "game", price, canonical=seq_id, released=seq_date)
            )
            if rng.random() < 0.5:
                edition_style = style if rng.random() < 0.5 else ("roman" if style == "arabic" else "arabic")
                out.append(
                    row(
                        self._edition_title(f"{title} {self._numeral(number, edition_style)}"),
                        "game",
                        price + 1000,
                        dev_name=self._noisy_company(dev),
                        canonical=seq_id,
                        released=seq_date,
                    )
                )
        # a spin-off: same studio, subtitle, years later (a DIFFERENT product)
        if rng.random() < 0.15:
            out.append(
                row(
                    f"{title} {rng.choice(SPINOFF_WORDS)}",
                    "game",
                    price,
                    canonical=f"{canonical}-spin",
                    released=later(1, 3),
                )
            )
        # an unrelated game from another studio that happens to share the title (a DIFFERENT product)
        if rng.random() < 0.10:
            studio = other_studio()
            out.append(
                row(
                    title, "game", price, dev_name=studio, pub_name=studio, canonical=f"{canonical}-twin",
                    released=fake.date_between("-8y", "-60d").isoformat(),
                )
            )  # fmt: skip
        # an edition whose name no cleaning vocabulary knows (SAME product)
        if rng.random() < 0.25:
            out.append(
                row(
                    self._edition_title(title, UNKNOWN_EDITIONS), "game", price + 1500,
                    dev_name=self._noisy_company(dev),
                )
            )  # fmt: skip
        # a remaster by a different studio, years later (SAME product)
        if rng.random() < 0.10:
            studio = other_studio()
            out.append(
                row(f"{title} Remastered", "game", price, dev_name=studio, pub_name=studio, released=later(2, 6))
            )
        return out

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
        newest_allowed = datetime.now(UTC) - timedelta(hours=1)
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
        now = datetime.now(UTC)
        last = (
            datetime.strptime(self.reviews[-1]["created_at"], FMT).replace(tzinfo=UTC)
            if self.reviews
            else now - timedelta(days=1)
        )
        start = max(now - timedelta(minutes=spread_minutes), last)
        span = max((now - start).total_seconds(), 0)
        stamps = sorted(start + timedelta(seconds=self.rng.uniform(0, span)) for _ in range(count))
        for ts in stamps:
            reviewer = str(76561199000000000 + self.rng.randrange(10**9))
            self.reviews.append(self._new_review(game_id, self.rng.random() >= negative_share, ts, reviewer))
        return count
