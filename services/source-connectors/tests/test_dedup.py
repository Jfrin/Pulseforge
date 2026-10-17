from app.dedup import key_records, payload_hash


def test_hash_ignores_key_order():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})


def test_hash_changes_when_content_changes():
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


def test_repeats_inside_a_page_are_dropped_first_wins():
    out = key_records([{"id": 1, "v": 1}, {"id": 1, "v": 1}, {"id": 2}], "id")
    assert [k.external_id for k in out] == ["1", "2"]


def test_changed_payload_with_same_id_is_kept_as_new_version():
    out = key_records([{"id": 1, "v": 1}, {"id": 1, "v": 2}], "id")
    assert len(out) == 2 and out[0].payload_hash != out[1].payload_hash


def test_missing_id_falls_back_to_content_hash():
    (k,) = key_records([{"title": "x"}], "id")
    assert k.external_id.startswith("hash:")


def test_numeric_ids_become_strings():
    (k,) = key_records([{"id": 42}], "id")
    assert k.external_id == "42"


def test_ignored_fields_do_not_change_the_hash():
    a = {"id": 1, "votes": 1, "author": {"playtime": 5, "name": "x"}}
    b = {"id": 1, "votes": 9, "author": {"playtime": 50, "name": "x"}}
    ignore = ("votes", "author.playtime")
    assert payload_hash(a, ignore) == payload_hash(b, ignore)
    assert payload_hash(a) != payload_hash(b)


def test_a_change_outside_the_ignored_fields_is_still_a_new_version():
    a = {"id": 1, "votes": 1, "author": {"playtime": 5, "name": "x"}}
    b = {"id": 1, "votes": 1, "author": {"playtime": 5, "name": "y"}}
    assert payload_hash(a, ("votes", "author.playtime")) != payload_hash(b, ("votes", "author.playtime"))


def test_ignoring_fields_never_mutates_the_record_and_tolerates_missing_paths():
    rec = {"id": 1, "votes": 1}
    payload_hash(rec, ("votes", "author.playtime", "nope.deeper.still"))
    assert rec == {"id": 1, "votes": 1}


def test_page_repeats_that_differ_only_in_ignored_fields_collapse():
    out = key_records([{"id": 1, "v": 1}, {"id": 1, "v": 2}], "id", ("v",))
    assert len(out) == 1 and out[0].record["v"] == 1
