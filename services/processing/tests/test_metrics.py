from app.metrics import find_errors, pairwise_metrics


def test_a_perfect_clustering_scores_one():
    pred = {"a": "p1", "b": "p1", "c": "p2"}
    truth = {"a": "t1", "b": "t1", "c": "t2"}
    m = pairwise_metrics(pred, truth)
    assert (m.precision, m.recall, m.f1) == (1.0, 1.0, 1.0)
    assert (m.predicted_clusters, m.true_clusters, m.items) == (2, 2, 3)


def test_merging_everything_keeps_recall_but_loses_precision():
    pred = {"a": "p", "b": "p", "c": "p"}
    truth = {"a": "t1", "b": "t1", "c": "t2"}
    m = pairwise_metrics(pred, truth)
    assert (m.predicted_pairs, m.true_pairs, m.correct_pairs) == (3, 1, 1)
    assert m.recall == 1.0 and abs(m.precision - 1 / 3) < 1e-9


def test_splitting_everything_keeps_precision_but_loses_recall():
    pred = {"a": "p1", "b": "p2", "c": "p3"}
    truth = {"a": "t", "b": "t", "c": "t"}
    m = pairwise_metrics(pred, truth)
    assert m.precision == 1.0 and m.recall == 0.0 and m.f1 == 0.0


def test_a_partial_result_counts_pairs_exactly():
    pred = {"a": "p1", "b": "p1", "c": "p2"}
    truth = {"a": "t", "b": "t", "c": "t"}
    m = pairwise_metrics(pred, truth)
    assert m.precision == 1.0 and abs(m.recall - 1 / 3) < 1e-9


def test_only_items_known_to_both_sides_are_compared():
    m = pairwise_metrics({"a": "p", "b": "p", "zzz": "p"}, {"a": "t", "b": "t", "other": "t"})
    assert m.items == 2 and m.precision == 1.0 and m.recall == 1.0


def test_find_errors_returns_example_pairs():
    pred = {"a": "p1", "b": "p1", "c": "p2", "d": "p3"}
    truth = {"a": "t1", "b": "t2", "c": "t3", "d": "t3"}
    merges, splits = find_errors(pred, truth)
    assert merges == [("a", "b")] and splits == [("c", "d")]


def test_a_perfect_result_has_no_errors():
    pred = {"a": "p", "b": "p"}
    assert find_errors(pred, {"a": "t", "b": "t"}) == ([], [])
