from unittest.mock import MagicMock

import httpx2 as httpx
import pytest
from pulseforge import notifications
from pulseforge.events import Event


@pytest.mark.asyncio
async def test_slack_and_email_delivery(memory, monkeypatch):
    requests = []
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200)))
    monkeypatch.setattr(notifications.httpx, "AsyncClient", lambda **kwargs: http)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://slack.test/hook")
    monkeypatch.setenv("SMTP_HOST", "mail.test")
    monkeypatch.setenv("SMTP_TO", "ops@test")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASSWORD", "password")
    smtp = MagicMock()
    monkeypatch.setattr(notifications.smtplib, "SMTP", smtp)
    await notifications.handle(
        memory, Event(type="AnomalyDetected", source="analytics", payload={"alert_id": "one", "kind": "review_bomb"})
    )
    result = await memory.get("results", "one")
    assert result["channels"] == ["slack", "email"] and len(requests) == 1
    mail = smtp.return_value.__enter__.return_value
    mail.starttls.assert_called_once()
    mail.login.assert_called_once_with("user", "password")
    assert mail.send_message.call_args.args[0]["Message-ID"] == "<one@pulseforge>"


@pytest.mark.asyncio
async def test_notification_failures_are_not_marked_sent(memory, monkeypatch):
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    monkeypatch.setattr(notifications.httpx, "AsyncClient", lambda **kwargs: http)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://slack.test/hook")
    with pytest.raises(httpx.HTTPStatusError):
        await notifications.handle(
            memory,
            Event(type="AnomalyDetected", source="analytics", payload={"alert_id": "one", "kind": "review_bomb"}),
        )
    assert not await memory.all("results")
