import pytest

from app.pseudonymize import Pseudonymizer

SALT = "a-test-salt-that-is-long-enough"


def test_deterministic_for_the_same_source_and_id():
    p = Pseudonymizer(SALT)
    assert p("steam-reviews", "7656") == p("steam-reviews", "7656")


def test_different_ids_get_different_pseudonyms():
    p = Pseudonymizer(SALT)
    assert p("steam-reviews", "1") != p("steam-reviews", "2")


def test_the_same_raw_id_in_two_sources_is_not_linkable():
    p = Pseudonymizer(SALT)
    assert p("steam-reviews", "1") != p("mock-reviews", "1")


def test_a_different_salt_gives_different_pseudonyms():
    assert Pseudonymizer(SALT)("s", "1") != Pseudonymizer(SALT + "-rotated")("s", "1")


def test_output_has_a_stable_shape_and_does_not_contain_the_raw_id():
    out = Pseudonymizer(SALT)("s", "76561198000000001")
    assert out.startswith("p_") and len(out) == 34
    assert "76561198000000001" not in out


def test_short_salts_are_refused():
    with pytest.raises(ValueError):
        Pseudonymizer("short")


def test_from_env_requires_the_variable(monkeypatch):
    monkeypatch.delenv("PSEUDONYM_SALT", raising=False)
    with pytest.raises(RuntimeError):
        Pseudonymizer.from_env()
    monkeypatch.setenv("PSEUDONYM_SALT", SALT)
    assert Pseudonymizer.from_env()("s", "1") == Pseudonymizer(SALT)("s", "1")


def test_repr_never_leaks_the_salt():
    assert SALT not in repr(Pseudonymizer(SALT))
