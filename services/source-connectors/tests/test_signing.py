import pytest

from app.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, sign, verify

SECRET = "s" * 20
BODY = b'{"id": 1}'
NOW = 1_800_000_000


def test_a_valid_signature_passes():
    verify([SECRET], str(NOW), sign(SECRET, NOW, BODY), BODY, now=NOW)


def test_a_tampered_body_fails():
    with pytest.raises(SignatureError):
        verify([SECRET], str(NOW), sign(SECRET, NOW, BODY), BODY + b" ", now=NOW)


def test_the_wrong_secret_fails():
    with pytest.raises(SignatureError):
        verify(["x" * 20], str(NOW), sign(SECRET, NOW, BODY), BODY, now=NOW)


def test_any_listed_secret_is_accepted_so_secrets_can_rotate():
    verify(["n" * 20, SECRET], str(NOW), sign(SECRET, NOW, BODY), BODY, now=NOW)


@pytest.mark.parametrize("offset", [-301, 301, 86_400])
def test_timestamps_outside_the_tolerance_are_rejected_in_both_directions(offset):
    ts = NOW + offset
    with pytest.raises(SignatureError):
        verify([SECRET], str(ts), sign(SECRET, ts, BODY), BODY, now=NOW)


@pytest.mark.parametrize("offset", [-300, 0, 300])
def test_timestamps_inside_the_tolerance_pass(offset):
    ts = NOW + offset
    verify([SECRET], str(ts), sign(SECRET, ts, BODY), BODY, now=NOW)


def test_the_signature_is_bound_to_the_timestamp():
    with pytest.raises(SignatureError):
        verify([SECRET], str(NOW + 1), sign(SECRET, NOW, BODY), BODY, now=NOW)


@pytest.mark.parametrize(
    "ts, sig", [(None, "sha256=ab"), (str(NOW), None), ("", "sha256=ab"), (str(NOW), ""), ("abc", "sha256=ab")]
)
def test_missing_or_malformed_headers_are_rejected(ts, sig):
    with pytest.raises(SignatureError):
        verify([SECRET], ts, sig, BODY, now=NOW)


def test_signature_format_and_header_names_are_stable():
    assert sign(SECRET, NOW, BODY).startswith("sha256=")
    assert (TIMESTAMP_HEADER, SIGNATURE_HEADER) == ("X-PulseForge-Timestamp", "X-PulseForge-Signature")
