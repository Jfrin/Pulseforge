import time

import pytest
from fastapi.testclient import TestClient

from app.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from app.webhook import create_app, load_secrets

SECRET = "x" * 24


class FakeInbox:
    def __init__(self):
        self.rows = []

    async def append(self, source, records):
        self.rows += [(source, r) for r in records]
        return len(records)


@pytest.fixture
def inbox():
    return FakeInbox()


@pytest.fixture
def client(inbox):
    return TestClient(create_app(inbox, {"demo": [SECRET]}))


def post(client, body, *, secret=SECRET, ts=None, name="demo", sign_body=None):
    ts = int(time.time()) if ts is None else ts
    headers = {
        TIMESTAMP_HEADER: str(ts),
        SIGNATURE_HEADER: sign(secret, ts, body if sign_body is None else sign_body),
        "content-type": "application/json",
    }
    return client.post(f"/webhooks/{name}", content=body, headers=headers)


def test_a_signed_object_is_accepted_and_stored_under_the_webhook_source(client, inbox):
    r = post(client, b'{"id": 1, "title": "x"}')
    assert r.status_code == 202 and r.json() == {"accepted": 1}
    assert inbox.rows == [("webhook-demo", {"id": 1, "title": "x"})]


def test_a_signed_array_stores_every_record(client, inbox):
    r = post(client, b'[{"id": 1}, {"id": 2}]')
    assert r.status_code == 202 and r.json() == {"accepted": 2}
    assert [row[1]["id"] for row in inbox.rows] == [1, 2]


def test_a_rotated_secret_is_still_accepted(inbox):
    c = TestClient(create_app(inbox, {"demo": ["n" * 24, SECRET]}))
    assert post(c, b'{"id": 1}').status_code == 202


@pytest.mark.parametrize("case", ["wrong_secret", "tampered_body", "stale", "no_headers"])
def test_unauthentic_requests_are_rejected_and_nothing_is_stored(client, inbox, case):
    body = b'{"id": 1}'
    if case == "wrong_secret":
        r = post(client, body, secret="y" * 24)
    elif case == "tampered_body":
        r = post(client, body, sign_body=b'{"id": 2}')
    elif case == "stale":
        r = post(client, body, ts=int(time.time()) - 3600)
    else:
        r = client.post("/webhooks/demo", content=body)
    assert r.status_code == 401 and r.json() == {"detail": "invalid signature"}
    assert inbox.rows == []


def test_an_unknown_webhook_is_404(client, inbox):
    assert post(client, b'{"id": 1}', name="nope").status_code == 404
    assert inbox.rows == []


@pytest.mark.parametrize("body", [b"not json", b"42", b'"text"', b"[]", b"[1, 2]", b'{"x": NaN}', b'[{"a": 1}, 5]'])
def test_bodies_that_are_not_objects_or_arrays_of_objects_are_422(client, inbox, body):
    assert post(client, body).status_code == 422
    assert inbox.rows == []


def test_oversized_bodies_are_413(inbox):
    c = TestClient(create_app(inbox, {"demo": [SECRET]}, max_body_bytes=50))
    assert post(c, b'{"id": "' + b"a" * 100 + b'"}').status_code == 413
    assert inbox.rows == []


def test_too_many_records_are_413(inbox):
    c = TestClient(create_app(inbox, {"demo": [SECRET]}, max_records=2))
    assert post(c, b'[{"a":1},{"a":2},{"a":3}]').status_code == 413


def test_health_needs_no_signature(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_load_secrets_parses_names_and_rotation():
    out = load_secrets("demo=" + SECRET + ", partner=" + "a" * 20 + "|" + "b" * 20)
    assert out == {"demo": [SECRET], "partner": ["a" * 20, "b" * 20]}
    assert load_secrets("") == {}


@pytest.mark.parametrize("raw", ["demo", "demo=", "=" + "s" * 20, "Bad Name=" + "s" * 20, "demo=short"])
def test_load_secrets_rejects_bad_entries(raw):
    with pytest.raises(ValueError):
        load_secrets(raw)
