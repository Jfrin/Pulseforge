from __future__ import annotations

import asyncio
import json
import os
import smtplib
from email.message import EmailMessage

import httpx2 as httpx

from .events import utcnow


def send_email(payload):
    message = EmailMessage()
    message["Subject"] = f"PulseForge: {payload['kind']}"
    message["From"] = os.getenv("SMTP_FROM", "pulseforge@localhost")
    message["To"] = os.environ["SMTP_TO"]
    message["Message-ID"] = f"<{payload['alert_id']}@pulseforge>"
    message.set_content(json.dumps(payload, indent=2))
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "25")), timeout=15) as client:
        if os.getenv("SMTP_STARTTLS", "false").lower() == "true":
            client.starttls()
        if os.getenv("SMTP_USER"):
            client.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        client.send_message(message)


async def handle(tx, event):
    payload = event.payload
    channels = []
    if os.getenv("SLACK_WEBHOOK_URL"):
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(os.environ["SLACK_WEBHOOK_URL"], json={"text": json.dumps(payload)})
            response.raise_for_status()
        channels.append("slack")
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_TO"):
        await asyncio.to_thread(send_email, payload)
        channels.append("email")
    await tx.put(
        "results",
        payload["alert_id"],
        {
            "alert": payload,
            "channels": channels,
            "status": "sent" if channels else "stored",
            "delivered_at": utcnow().isoformat(),
        },
    )
