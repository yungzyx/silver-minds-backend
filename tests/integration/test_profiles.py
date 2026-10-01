import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent
from tests.helpers import auth_headers

API = "/api/v1"


def test_profile_update_persists_and_is_audited(client: TestClient, db: Session) -> None:
    user_id = uuid.uuid4()
    headers = auth_headers(user_id)

    response = client.patch(
        f"{API}/profile",
        headers=headers,
        json={"preferred_name": "Rosa", "timezone": "America/Santiago", "voice_replies": True},
    )

    assert response.status_code == 200
    assert client.get(f"{API}/profile", headers=headers).json()["preferred_name"] == "Rosa"
    actions = db.execute(select(AuditEvent.action).where(AuditEvent.owner_id == user_id)).scalars()
    assert "profile.updated" in list(actions)


def test_profile_rejects_unknown_timezone_and_extra_fields(client: TestClient) -> None:
    headers = auth_headers()

    bad_timezone = client.patch(f"{API}/profile", headers=headers, json={"timezone": "Marte/Base"})
    extra_field = client.patch(f"{API}/profile", headers=headers, json={"id": str(uuid.uuid4())})

    assert bad_timezone.status_code == 422
    assert extra_field.status_code == 422
    assert bad_timezone.json()["error"]["code"] == "validation_error"


def test_updating_profile_does_not_touch_another_user(client: TestClient) -> None:
    rosa, pedro = auth_headers(), auth_headers()
    client.patch(f"{API}/profile", headers=pedro, json={"preferred_name": "Pedro"})

    client.patch(f"{API}/profile", headers=rosa, json={"preferred_name": "Rosa"})

    assert client.get(f"{API}/profile", headers=pedro).json()["preferred_name"] == "Pedro"


def test_preference_lifecycle(client: TestClient) -> None:
    headers = auth_headers()

    created = client.post(
        f"{API}/preferences",
        headers=headers,
        json={"category": "schedule", "value": "Prefiero las mañanas"},
    )
    preference_id = created.json()["id"]
    edited = client.patch(
        f"{API}/preferences/{preference_id}", headers=headers, json={"value": "Después de las 10"}
    )
    listed = client.get(f"{API}/preferences", headers=headers).json()
    deleted = client.delete(f"{API}/preferences/{preference_id}", headers=headers)

    assert created.status_code == 201
    assert edited.json()["value"] == "Después de las 10"
    assert listed["total"] == 1
    assert deleted.status_code == 204
    assert client.get(f"{API}/preferences", headers=headers).json()["total"] == 0


def test_preferences_are_isolated_between_users(client: TestClient) -> None:
    rosa, pedro = auth_headers(), auth_headers()
    preference_id = client.post(
        f"{API}/preferences", headers=rosa, json={"category": "interest", "value": "Jardinería"}
    ).json()["id"]

    listed_by_pedro = client.get(f"{API}/preferences", headers=pedro).json()
    edit_by_pedro = client.patch(
        f"{API}/preferences/{preference_id}", headers=pedro, json={"value": "Otra cosa"}
    )
    delete_by_pedro = client.delete(f"{API}/preferences/{preference_id}", headers=pedro)

    assert listed_by_pedro["total"] == 0
    assert edit_by_pedro.status_code == 404
    assert delete_by_pedro.status_code == 404
    assert (
        client.get(f"{API}/preferences", headers=rosa).json()["items"][0]["value"] == "Jardinería"
    )


def test_preference_rejects_unknown_category(client: TestClient) -> None:
    response = client.post(
        f"{API}/preferences", headers=auth_headers(), json={"category": "diagnosis", "value": "x"}
    )

    assert response.status_code == 422
