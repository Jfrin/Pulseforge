import io
import json

from app import cli, demo
from app.pipeline import process_batch
from app.pseudonymize import Pseudonymizer

PSEUDO = Pseudonymizer("a-test-salt-that-is-long-enough")


def test_the_demo_payloads_are_wellformed_and_show_both_outcomes():
    games = process_batch(demo.build_games_payload(), PSEUDO)
    assert games.summary() == {"games": 3, "reviews": 0, "rejects": 1}
    reviews = process_batch(demo.build_reviews_payload(), PSEUDO)
    assert reviews.summary() == {"games": 0, "reviews": 1, "rejects": 1}


def test_the_demo_runs(monkeypatch, capsys):
    monkeypatch.setenv("PSEUDONYM_SALT", "a-test-salt-that-is-long-enough")
    demo.main()
    out = capsys.readouterr().out
    assert "demo-games" in out and "REJECT" in out


def test_the_cli_summarizes_a_batch_from_stdin(monkeypatch, capsys):
    monkeypatch.setenv("PSEUDONYM_SALT", "a-test-salt-that-is-long-enough")
    stdin = io.StringIO(json.dumps(demo.build_games_payload()))
    assert cli.main([], stdin=stdin) == 0
    out = capsys.readouterr().out
    assert "'games': 3" in out and "title:missing" in out


def test_the_cli_can_print_the_full_silver_output(monkeypatch, capsys):
    monkeypatch.setenv("PSEUDONYM_SALT", "a-test-salt-that-is-long-enough")
    stdin = io.StringIO(json.dumps(demo.build_reviews_payload()))
    assert cli.main(["--json"], stdin=stdin) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["reviews"]) == 1 and "7656119800000001" not in json.dumps(out)
