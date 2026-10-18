from datetime import UTC, date, datetime

import pytest

from app.normalize import (
    clean_text,
    company_key,
    normalize_company,
    normalize_currency,
    normalize_kind,
    parse_bool,
    parse_date,
    parse_datetime,
    parse_int,
)


@pytest.mark.parametrize(
    "raw, expected",
    [("  Neon   Harbor \n", "Neon Harbor"), ("ＡＢＣ", "ABC"), ("", None), ("   ", None), (None, None), (42, "42")],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


@pytest.mark.parametrize(
    "name", ["Okafor Labs", "Okafor Labs Inc.", "OKAFOR LABS", "OkaforLabs", "Okafor Labs Ltd.", " okafor  labs, LLC "]
)
def test_studio_name_noise_collapses_to_one_key(name):
    assert company_key(name) == "okaforlabs"


def test_different_studios_keep_different_keys():
    assert company_key("Okafor Labs") != company_key("Vega Interactive")


def test_accents_and_ampersands_are_folded():
    assert company_key("Éditions Ubi & Cie") == company_key("Editions Ubi and Cie")


def test_a_name_made_only_of_legal_words_still_gets_a_key():
    assert company_key("Inc") == "inc"


def test_non_latin_names_are_not_erased():
    assert company_key("株式会社 ゲーム") is not None


def test_normalize_company_returns_display_and_key():
    assert normalize_company("  Vega   Interactive Inc. ") == ("Vega Interactive Inc.", "vegainteractive")
    assert normalize_company(None) == (None, None)


@pytest.mark.parametrize(
    "raw, expected", [("GAME", "game"), (" DLC ", "dlc"), ("bundle", "bundle"), ("tool", "unknown"), (None, "unknown")]
)
def test_normalize_kind(raw, expected):
    assert normalize_kind(raw) == expected


@pytest.mark.parametrize(
    "raw, expected", [("usd", "USD"), (" Eur ", "EUR"), ("US", None), ("dollars", None), (None, None), ("U$D", None)]
)
def test_normalize_currency(raw, expected):
    assert normalize_currency(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(5, 5), ("1999", 1999), (" 42 ", 42), ("-3", -3), ("1999.0", 1999), (7.0, 7), (7.5, None), ("7.5", None),
     ("free", None), ("", None), (None, None), (True, None), (float("nan"), None), (float("inf"), None)],
)  # fmt: skip
def test_parse_int(raw, expected):
    assert parse_int(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        (2, None),
        ("TRUE", True),
        ("no", False),
        ("maybe", None),
        (None, None),
    ],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2021-03-12", date(2021, 3, 12)),
        ("2021-03-12T10:00:00Z", date(2021, 3, 12)),
        ("12 March", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_datetime_handles_iso_naive_epoch_seconds_and_milliseconds():
    expected = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert parse_datetime("2026-10-01T12:00:00Z") == expected
    assert parse_datetime("2026-10-01T14:00:00+02:00") == expected
    assert parse_datetime("2026-10-01T12:00:00") == expected  # naive means UTC
    assert parse_datetime(int(expected.timestamp())) == expected
    assert parse_datetime(int(expected.timestamp()) * 1000) == expected


@pytest.mark.parametrize("raw", ["not a date", "", None, True, float("nan")])
def test_parse_datetime_rejects_garbage(raw):
    assert parse_datetime(raw) is None
