import json

import httpx
import pytest

from app.integrations.email.base import (
    EmailAmbiguousError,
    EmailMessage,
    EmailRejectedError,
    EmailTransientError,
)
from app.integrations.email.resend import ResendEmailSender

MESSAGE = EmailMessage(
    to="camila@example.com", subject="Hola", text="Texto", idempotency_key="invitation:123"
)


def sender(handler) -> ResendEmailSender:
    return ResendEmailSender(
        "re_test_key", "Silver Minds <no-reply@example.com>", transport=httpx.MockTransport(handler)
    )


def test_request_matches_resend_contract() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "email_1"})

    provider_id = sender(handler).send(MESSAGE)

    assert provider_id == "email_1"
    assert seen["url"] == "https://api.resend.com/emails"
    assert seen["headers"]["Authorization"] == "Bearer re_test_key"
    assert seen["headers"]["Idempotency-Key"] == "invitation:123"
    assert seen["headers"]["User-Agent"].startswith("silver-minds-backend")
    assert seen["body"] == {
        "from": "Silver Minds <no-reply@example.com>",
        "to": ["camila@example.com"],
        "subject": "Hola",
        "text": "Texto",
    }


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (422, {"name": "validation_error"}, EmailRejectedError),
        (401, {"name": "invalid_api_key"}, EmailRejectedError),
        (409, {"name": "invalid_idempotent_request"}, EmailRejectedError),
        (409, {"name": "concurrent_idempotent_requests"}, EmailTransientError),
        (429, {"name": "rate_limit_exceeded"}, EmailTransientError),
        (500, {"name": "application_error"}, EmailAmbiguousError),
        (503, {"name": "service_unavailable"}, EmailAmbiguousError),
    ],
)
def test_error_responses_are_classified(status: int, body: dict, expected: type) -> None:
    with pytest.raises(expected):
        sender(lambda _: httpx.Response(status, json=body)).send(MESSAGE)


def test_connection_refused_means_not_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(EmailTransientError):
        sender(handler).send(MESSAGE)


def test_timeout_after_sending_is_ambiguous() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(EmailAmbiguousError):
        sender(handler).send(MESSAGE)


def test_success_without_id_is_ambiguous() -> None:
    with pytest.raises(EmailAmbiguousError):
        sender(lambda _: httpx.Response(200, json={})).send(MESSAGE)
