from pathlib import Path

import pytest

from app.evaluate import evaluate
from app.labels import load_labeled_games

EXAMPLE = Path(__file__).resolve().parent.parent / "data" / "labels.example.csv"


def write(tmp_path, text):
    path = tmp_path / "labels.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_rows_become_games_with_labels_as_ground_truth(tmp_path):
    path = write(
        tmp_path,
        "title,developer,publisher,release_date,label\n"
        "Neon Harbor,Vega Interactive,Vega Interactive,2020-01-01,a\n"
        "Neon Harbor: Deluxe Edition,Vega Interactive Inc.,,,a\n",
    )
    games, truth = load_labeled_games(path)
    assert [g.title for g in games] == ["Neon Harbor", "Neon Harbor: Deluxe Edition"]
    assert games[0].release_date.isoformat() == "2020-01-01" and games[1].release_date is None
    assert games[0].developer_key == games[1].developer_key == "vegainteractive"
    assert truth == {"row-2": "a", "row-3": "a"}


def test_comment_lines_blank_rows_and_an_excel_bom_are_tolerated(tmp_path):
    path = tmp_path / "labels.csv"
    path.write_bytes("\ufeff# a comment\ntitle,label\nA Game,x\n\n   ,  \nB Game,y\n".encode())
    games, truth = load_labeled_games(path)
    assert [g.title for g in games] == ["A Game", "B Game"] and list(truth.values()) == ["x", "y"]


def test_explicit_external_ids_are_used(tmp_path):
    _, truth = load_labeled_games(write(tmp_path, "external_id,title,label\nsteam-570,Dota 2,dota\n"))
    assert truth == {"steam-570": "dota"}


@pytest.mark.parametrize(
    "text",
    [
        "title,developer\nA Game,X\n",  # no label column
        "label,developer\nx,X\n",  # no title column
        "title,label\n,x\n",  # empty title
        "title,label\nA Game,\n",  # empty label
        "external_id,title,label\na,One,x\na,Two,y\n",  # duplicate id
    ],
)
def test_bad_files_are_rejected_with_a_clear_error(tmp_path, text):
    with pytest.raises(ValueError):
        load_labeled_games(write(tmp_path, text))


def test_the_starter_sample_loads_and_can_be_scored():
    games, truth = load_labeled_games(EXAMPLE)
    assert len(games) >= 30 and len(set(truth.values())) >= 15
    report = evaluate(games, truth, origin="starter sample")
    assert 0.0 <= report.metrics.precision <= 1.0 and 0.0 <= report.metrics.recall <= 1.0
    assert report.metrics.items == len(games)
