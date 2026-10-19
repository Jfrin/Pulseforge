"""Entity resolution: collapse editions, remasters, DLC and bundles into one canonical product.

Incremental and explainable. Each incoming Game is compared with known products:
  1. blocking: only products sharing a title token or a studio key are candidates
  2. score = rapidfuzz token_sort_ratio of the base-title keys, then
        sequel numbers differ ("Neon Harbor" / "Neon Harbor 2", "II" == "2") ... veto (score 0)
        developer or publisher agrees ............................................ +5
        both comparable and neither agrees ....................................... -20
            (only -5 when the INCOMING title carries an edition/DLC/bundle marker: a remaster or port by
             another studio is still the same product. Softening on the product side instead lets a variant
             that arrived before its base game attract any same-title game from another studio)
        one title is the other plus 1-3 trailing words ("Halo" / "Halo Wars") ..... text alone cannot say whether
            it is a spin-off or an edition name we do not know, so it is capped below the threshold UNLESS the
            studio agrees and the release dates are within RELEASE_WINDOW_DAYS (then it counts as a match)
  3. score >= threshold joins the best product; otherwise a new product is created
Scores in [review_threshold, threshold) are reported as `possible` but never merged.

Honest limits: greedy and arrival-order dependent, products are never merged after the fact (a late record that
would bridge two products joins only one), a record that changes later keeps its first assignment, named DLC without
a marker ("Blood and Wine") needs structured parent links from the store, and state is in memory.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz

from .models import Game
from .titles import VARIANT_RANK, TitleKey, analyze_title

MAX_POSTING = 2000  # ignore tokens so common that they stop discriminating
RELEASE_WINDOW_DAYS = 30


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
    release_dates: list[date] = field(default_factory=list)
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


def _is_extension(a_key: str, b_key: str) -> bool:
    """True when one key is the other plus 1-3 trailing words."""
    a, b = a_key.split(), b_key.split()
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return 1 <= len(long_) - len(short) <= 3 and long_[: len(short)] == short


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
            score, reasons = self._score(key, game, self._products[pid])
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

        product = self._create(game, key)
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

    @staticmethod
    def _within_release_window(game: Game, product: Product) -> bool:
        if game.release_date is None:
            return False
        return any(abs((game.release_date - d).days) <= RELEASE_WINDOW_DAYS for d in product.release_dates)

    def _score(self, key: TitleKey, game: Game, product: Product) -> tuple[float, tuple[str, ...]]:
        if key.numerals != product.numerals:
            return 0.0, ("sequel_numbers_differ",)
        title_score = max(
            fuzz.token_sort_ratio(key.match_key, product.anchor_key),
            fuzz.ratio(key.match_key.replace(" ", ""), product.anchor_key.replace(" ", "")),  # a typo can eat a space
        )
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
            softened = key.variant != "base"
            score -= 5 if softened else 20
            reasons.append("studio_conflict_softened_by_variant" if softened else "developer_and_publisher_conflict")

        if _is_extension(key.match_key, product.anchor_key):
            if agrees and self._within_release_window(game, product):
                score = max(score, self.threshold)
                reasons.append("extension_same_release_window")
            else:
                score = min(score, self.threshold - 0.1)
                reasons.append("extension_needs_release_evidence")
        return score, tuple(reasons)

    def _create(self, game: Game, key: TitleKey) -> Product:
        product = Product(
            product_id=_product_id(game.source, game.external_id),
            title=game.title,
            anchor_key=key.match_key,
            numerals=key.numerals,
            title_rank=VARIANT_RANK[key.variant],
        )
        self._products[product.product_id] = product
        for token in key.match_key.split():
            self._by_token.setdefault(token, set()).add(product.product_id)
        self._attach(product, game, key.variant)
        return product

    def _attach(self, product: Product, game: Game, variant: str) -> None:
        product.members.append(Member(game.source, game.external_id, variant))
        self._assigned[(game.source, game.external_id)] = (product.product_id, variant)
        if game.release_date is not None:
            product.release_dates.append(game.release_date)
        for key, bucket in ((game.developer_key, product.developer_keys), (game.publisher_key, product.publisher_keys)):
            if key:
                bucket.add(key)
                self._by_company.setdefault(key, set()).add(product.product_id)
        rank = VARIANT_RANK[variant]  # show the plainest member's title (the base game beats "Deluxe Edition")
        if (rank, len(game.title)) < (product.title_rank, len(product.title)):
            product.title, product.title_rank = game.title, rank


def resolve_games(resolver: Resolver, games: list[Game]) -> list[tuple[Game, Resolution]]:
    return [(game, resolver.resolve(game)) for game in games]
