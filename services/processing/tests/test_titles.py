import pytest

from app.titles import analyze_title


@pytest.mark.parametrize(
    "title",
    [
        "Neon Harbor",
        "Neon Harbor: Deluxe Edition",
        "Neon Harbor - Definitive Edition",
        "Neon Harbor (Game of the Year Edition)",
        "Neon Harbor Complete Edition",
        "NEON HARBOR Anniversary Edition",
        "Neon Harbor Remastered",
        "Neon Harbor Collector's Edition",
        "Neon Harbor - Season Pass",
        "Neon Harbor - Soundtrack",
        "Neon Harbor - Cosmetic Pack",
        "Neon Harbor + Expansion Bundle",
        "  Neon   Harbor  ",
    ],
)
def test_every_variant_format_reduces_to_the_same_match_key(title):
    assert analyze_title(title).match_key == "neon harbor"


@pytest.mark.parametrize(
    "title, variant",
    [
        ("Neon Harbor", "base"),
        ("Neon Harbor: Deluxe Edition", "edition"),
        ("Neon Harbor Remastered", "edition"),
        ("Neon Harbor - Season Pass", "dlc"),
        ("Neon Harbor - Soundtrack", "dlc"),
        ("Neon Harbor + Expansion Bundle", "bundle"),
    ],
)
def test_variant_kinds(title, variant):
    assert analyze_title(title).variant == variant


def test_stacked_markers_are_all_stripped_and_the_strongest_kind_wins():
    key = analyze_title("Neon Harbor - Season Pass Deluxe Edition")
    assert key.match_key == "neon harbor" and key.variant == "dlc" and len(key.markers) == 2


def test_a_title_made_only_of_markers_is_not_stripped_to_nothing():
    key = analyze_title("Deluxe Edition")
    assert key.match_key == "deluxe edition" and key.variant == "base"


def test_markers_in_the_middle_of_a_title_are_left_alone():
    assert analyze_title("Pack Rat Adventures").match_key == "pack rat adventures"
    assert analyze_title("Edition Zero Racing").match_key == "edition zero racing"


def test_stopwords_accents_ampersands_and_punctuation_do_not_matter():
    assert analyze_title("The Witcher 3").match_key == "witcher 3"
    assert analyze_title("Café Dörf").match_key == "cafe dorf"
    assert analyze_title("Cinder, Salt & Stone").match_key == analyze_title("Cinder Salt Stone").match_key


@pytest.mark.parametrize(
    "title, numerals",
    [
        ("Neon Harbor", set()),
        ("Neon Harbor 2", {"2"}),
        ("Neon Harbor 2: Deluxe Edition", {"2"}),
        ("Final Fantasy VII", {"7"}),
        ("Neon Harbor III", {"3"}),
        ("Final Fantasy X", {"x"}),
        ("Project X Zone 2", {"2"}),
    ],
)
def test_sequel_numbers_are_extracted(title, numerals):
    assert analyze_title(title).numerals == numerals


def test_roman_and_arabic_sequel_numbers_become_the_same_key():
    assert analyze_title("Neon Harbor II").match_key == analyze_title("Neon Harbor 2").match_key == "neon harbor 2"
    assert analyze_title("Neon Harbor II: Deluxe Edition").numerals == {"2"}


def test_a_lone_trailing_v_or_x_is_never_converted_so_mega_man_x_is_not_mega_man_10():
    assert analyze_title("Mega Man X").match_key == "mega man x"
    assert analyze_title("Mega Man X").match_key != analyze_title("Mega Man 10").match_key


@pytest.mark.parametrize(
    "title, key",
    [
        ("Borderlands 3 Super Deluxe Edition", "borderlands 3"),
        ("Prey: Digital Deluxe Edition", "prey"),
        ("Sid Meier's Civilization V: The Complete Edition", "sid meiers civilization v"),
    ],
)
def test_real_world_edition_spellings_reduce_to_the_base_title(title, key):
    result = analyze_title(title)
    assert result.match_key == key and result.variant == "edition"


def test_a_trailing_stopword_left_by_stripping_does_not_hide_the_sequel_number():
    assert analyze_title("Sid Meier's Civilization V: The Complete Edition").numerals == {"v"}
    assert analyze_title("Sid Meier's Civilization V").numerals == {"v"}


def test_edition_modifiers_are_only_stripped_next_to_a_real_marker():
    assert analyze_title("Digital Dreams").match_key == "digital dreams"
    assert analyze_title("Super Mario Adventure").match_key == "super mario adventure"
