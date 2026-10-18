"""Entity resolution: collapse editions, remasters, DLC and bundles into one canonical product.

Incremental and explainable. Each incoming Game is compared with known products:
  1. blocking: only products sharing a title token or a studio key are candidates
  2. score = rapidfuzz token_sort_ratio of the base-title keys
        +5  if developer or publisher agrees           -20 if both are comparable and neither agrees
        veto (score 0) if the sequel numbers differ ("Neon Harbor" != "Neon Harbor 2")
  3. score >= threshold joins the best product; otherwise a new product is created
Scores in [review_threshold, threshold) are reported as `possible` but never merged.

Honest limits: greedy and arrival-order dependent, products are never merged after the fact, a record that
changes later keeps its first assignment, release dates are not used, and state is in memory (persist it
before relying on it across restarts).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .models import Game
from .titles import VARIANT_RANK, analyze_title

MAX_POSTING = 2000  # ignore tokens so common that they stop discriminating


@dataclass(frozen=True, slots=True)
class Member:
    source: str
    external_id: str
    variant: str


@dataclass
class Product:
    product_id: str
    title: str
    anchor_key: str
    numerals: frozenset[str]
    developer_keys: set[str] = field(default_factory=set)
    publisher_keys: set[str] = field(default_factory=set)
    members: list[Member] = field(default_factory=list)
    title_rank: int = 0


@dataclass(frozen=True, slots=True)
class Resolution:
    product_id: str
    is_new: bool
    score: float
    reasons: tuple[str, ...]
    variant: str
    possible: tuple[tuple[str, float], ...] = ()


def _product_id(source: str, external_id: str) -> str:
    return "p_" + hashlib.sha256(f"{source}\x1f{external_id}".encode()).hexdigest()[:12]


class Resolver:
    def __init__(self, *, threshold: float = 90.0, review_threshold: float = 80.0) -> None:
        if not 0 < review_threshold <= threshold <= 100:
            raise ValueError("need 0 < review_threshold <= threshold <= 100")
        self.threshold = threshold
        self.review_threshold = review_threshold
        self._products: dict[str, Product] = {}
        self._by_token: dict[str, set[str]] = {}
        self._by_company: dict[str, set[str]] = {}
        self._assigned: dict[tuple[str, str], tuple[str, str]] = {}  # member key -> (product id, variant)

    # ------------------------------------------------------------------ public
    def products(self) -> list[Product]:
        return list(self._products.values())

    def assignments(self) -> dict[tuple[str, str], str]:
        return {key: pid for key, (pid, _) in self._assigned.items()}

    def resolve(self, game: Game) -> Resolution:
        member_key = (game.source, game.external_id)
        if member_key in self._assigned:
            pid, variant = self._assigned[member_key]
            return Resolution(pid, False, 100.0, ("already_resolved",), variant)

        key = analyze_title(game.title)
        scored: list[tuple[float, str, tuple[str, ...]]] = []
        for pid in self._candidates(key.match_key, game):
            score, reasons = self._score(key.match_key, key.numerals, game, self._products[pid])
            scored.append((score, pid, reasons))
        scored.sort(key=lambda item: (-item[0], item[1]))

        best = scored[0] if scored else None
        possible = tuple(
            (pid, round(score, 1)) for score, pid, _ in scored if self.review_threshold <= score < self.threshold
        )[:3]

        if best and best[0] >= self.threshold:
            score, pid, reasons = best
            self._attach(self._products[pid], game, key.variant)
            return Resolution(pid, False, round(score, 1), (*reasons, f"variant={key.variant}"), key.variant, possible)

        product = self._create(game, key.match_key, key.numerals, key.variant)
        return Resolution(
            product.product_id, True, 100.0, ("new_product", f"variant={key.variant}"), key.variant, possible
        )

    # ------------------------------------------------------------------ internals
    def _candidates(self, match_key: str, game: Game) -> set[str]:
        found: set[str] = set()
        for token in match_key.split():
            posting = self._by_token.get(token, ())
            if len(posting) <= MAX_POSTING:
                found.update(posting)
        for company in (game.developer_key, game.publisher_key):
            if company:
                found.update(self._by_company.get(company, ()))
        return found

    def _score(
        self, match_key: str, numerals: frozenset[str], game: Game, product: Product
    ) -> tuple[float, tuple[str, ...]]:
        if numerals != product.numerals:
            return 0.0, ("sequel_numbers_differ",)
        title_score = fuzz.token_sort_ratio(match_key, product.anchor_key)
        reasons = [f"title_similarity={title_score:.0f}"]

        dev_comparable = bool(game.developer_key and product.developer_keys)
        pub_comparable = bool(game.publisher_key and product.publisher_keys)
        dev_equal = dev_comparable and game.developer_key in product.developer_keys
        pub_equal = pub_comparable and game.publisher_key in product.publisher_keys
        agrees = dev_equal or pub_equal
        conflict = (dev_comparable or pub_comparable) and not agrees

        score = float(title_score)
        if agrees:
            score = min(100.0, score + 5)
            reasons.append("developer_or_publisher_agrees")
        elif conflict:
            score -= 20
            reasons.append("developer_and_publisher_conflict")
        return score, tuple(reasons)

    def _create(self, game: Game, match_key: str, numerals: frozenset[str], variant: str) -> Product:
        product = Product(
            product_id=_product_id(game.source, game.external_id),
            title=game.title,
            anchor_key=match_key,
            numerals=numerals,
            title_rank=VARIANT_RANK[variant],
        )
        self._products[product.product_id] = product
        for token in match_key.split():
            self._by_token.setdefault(token, set()).add(product.product_id)
        self._attach(product, game, variant)
        return product

    def _attach(self, product: Product, game: Game, variant: str) -> None:
        product.members.append(Member(game.source, game.external_id, variant))
        self._assigned[(game.source, game.external_id)] = (product.product_id, variant)
        for key, bucket in ((game.developer_key, product.developer_keys), (game.publisher_key, product.publisher_keys)):
            if key:
                bucket.add(key)
                self._by_company.setdefault(key, set()).add(product.product_id)
        rank = VARIANT_RANK[variant]  # show the plainest member's title (the base game beats "Deluxe Edition")
        if (rank, len(game.title)) < (product.title_rank, len(product.title)):
            product.title, product.title_rank = game.title, rank


def resolve_games(resolver: Resolver, games: list[Game]) -> list[tuple[Game, Resolution]]:
    return [(game, resolver.resolve(game)) for game in games]
