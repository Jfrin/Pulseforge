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
