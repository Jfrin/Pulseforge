"""Pairwise precision/recall for clusterings, plus concrete mistakes to look at."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Hashable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Metrics:
    precision: float  # of the pairs we put together, how many truly belong together
    recall: float  # of the pairs that truly belong together, how many we put together
    f1: float
    items: int
    predicted_clusters: int
    true_clusters: int
    predicted_pairs: int
    true_pairs: int
    correct_pairs: int


def _pairs(n: int) -> int:
    return n * (n - 1) // 2


def pairwise_metrics[K: Hashable](predicted: dict[K, str], truth: dict[K, str]) -> Metrics:
    """Only items present in both mappings are compared."""
    keys = predicted.keys() & truth.keys()
    pred_sizes = Counter(predicted[k] for k in keys)
    true_sizes = Counter(truth[k] for k in keys)
    joint = Counter((predicted[k], truth[k]) for k in keys)
    predicted_pairs = sum(_pairs(n) for n in pred_sizes.values())
    true_pairs = sum(_pairs(n) for n in true_sizes.values())
    correct = sum(_pairs(n) for n in joint.values())
    precision = correct / predicted_pairs if predicted_pairs else 1.0
    recall = correct / true_pairs if true_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return Metrics(
        precision, recall, f1, len(keys), len(pred_sizes), len(true_sizes), predicted_pairs, true_pairs, correct
    )


def find_errors[K: Hashable](
    predicted: dict[K, str], truth: dict[K, str], limit: int = 10
) -> tuple[list[tuple[K, K]], list[tuple[K, K]]]:
    """Return (false merges, false splits): one example pair per wrongly merged / split cluster."""
    keys = sorted(predicted.keys() & truth.keys(), key=str)
    by_pred: dict[str, list[K]] = defaultdict(list)
    by_true: dict[str, list[K]] = defaultdict(list)
    for k in keys:
        by_pred[predicted[k]].append(k)
        by_true[truth[k]].append(k)

    merges: list[tuple[K, K]] = []
    for members in by_pred.values():
        first = members[0]
        other = next((m for m in members[1:] if truth[m] != truth[first]), None)
        if other is not None:
            merges.append((first, other))

    splits: list[tuple[K, K]] = []
    for members in by_true.values():
        first = members[0]
        other = next((m for m in members[1:] if predicted[m] != predicted[first]), None)
        if other is not None:
            splits.append((first, other))
    return merges[:limit], splits[:limit]
