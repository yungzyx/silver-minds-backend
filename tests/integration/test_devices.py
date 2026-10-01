import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from app.core.clock import utcnow
from app.models import AuditEvent, DeviceEvent, FamilyAccess
from app.modules.devices import stream
from app.worker.main import run_pending
from tests.helpers import API, link_token, new_user

JPEG = b"\xff\xd8\xff\xe0" + b"cuadro-uno"
JPEG_2 = b"\xff\xd8\xff\xe0" + b"cuadro-dos"


def device_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Device {token}"}


def viewer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Viewer {token}"}


def new_device(client: TestClient, headers: dict, name: str = "Silvia") -> tuple[dict, str]:
    response = client.post(f"{API}/devices", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    body = response.json()
    return body, body["token"]


def accepted_contact(client: TestClient, headers: dict, providers, name: str = "Camila") -> str:
    contact_id = client.post(
        f"{API}/contacts",
        headers=headers,
        json={"name": name, "email": f"{name.lower()}@example.com"},
    ).json()["id"]
    run_pending()
    token = link_token(providers.email.sent[-1].text)
    client.post(f"{API}/contact-consents/{token}", json={"decision": "accept"})
    providers.email.sent.clear()
    return contact_id


def family_token(
    client: TestClient, headers: dict, providers, *, camera: bool = True
) -> tuple[str, str]:
    """Da acceso a un contacto aceptado y devuelve (access_id, token del enlace)."""
    contact_id = accepted_contact(client, headers, providers)
    access = client.post(
        f"{API}/family-access",
        headers=headers,
        json={"contact_id": contact_id, "can_view_camera": camera},
    )
    assert access.status_code == 201, access.text
    run_pending()
    link = providers.email.sent[-1].text
    token = link.split("/family/#token=")[1].split()[0]
    return access.json()["id"], token


def auth(socket, token: str) -> dict:
    socket.send_json({"type": "auth", "token": token})
    return socket.receive_json()


def next_frame(socket) -> tuple[bytes, list[dict]]:
    """Lee hasta el próximo cuadro binario; devuelve también los mensajes de estado previos."""
    statuses = []
    while True:
        message = socket.receive()
        if message.get("bytes") is not None:
            return message["bytes"], statuses
        statuses.append(json.loads(message["text"]))


def until(socket, predicate) -> dict:
    while True:
        message = socket.receive_json()
        if predicate(message):
            return message


@pytest.fixture(autouse=True)
def _no_frame_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stream, "MIN_FRAME_INTERVAL", 0.0)


# --- Dispositivo --------------------------------------------------------------------


def test_device_token_is_shown_once_and_stored_hashed(client: TestClient, db: Session) -> None:
    _, headers = new_user(client)

    created, token = new_device(client, headers)
    listed = client.get(f"{API}/devices", headers=headers).json()["items"][0]

    assert token and "token" not in listed
    assert created["name"] == "Silvia" and created["camera_sharing"] is False
    assert token not in str(db.execute(select(DeviceEvent)).all())


def test_device_acts_for_its_owner(knowledge: None, client: TestClient) -> None:
    _, headers = new_user(client, preferred_name="Rosa")
    _, token = new_device(client, headers)
    device = device_headers(token)

    session = client.get(f"{API}/device/session", headers=device).json()
    conversation_id = client.post(f"{API}/conversations", headers=device).json()["id"]
    reply = client.post(
        f"{API}/conversations/{conversation_id}/messages",
        headers=device,
        json={"content": "Me gustaría una actividad con plantas y huerto"},
    ).json()

    assert session["device_name"] == "Silvia" and session["preferred_name"] == "Rosa"
    assert session["mode"] == "normal" and session["viewers"] == []
    assert reply["proposals"][0]["title"] == "Taller de huerto comunitario"
    # La conversación hecha desde el dispositivo pertenece a la cuenta de Rosa.
    assert client.get(f"{API}/conversations", headers=headers).json()["total"] == 1


def test_device_token_cannot_manage_devices_or_family_access(client: TestClient) -> None:
    _, headers = new_user(client)
    _, token = new_device(client, headers)
    device = device_headers(token)

    assert client.post(f"{API}/devices", headers=device, json={"name": "Otro"}).status_code == 401
    assert client.get(f"{API}/devices", headers=device).status_code == 401
    assert client.get(f"{API}/family-access", headers=device).status_code == 401
    assert (
        client.post(
            f"{API}/family-access", headers=device, json={"contact_id": str(uuid.uuid4())}
        ).status_code
        == 401
    )


def test_revoked_or_unknown_device_token_is_rejected(client: TestClient) -> None:
    _, headers = new_user(client)
    created, token = new_device(client, headers)

    client.post(f"{API}/devices/{created['id']}/revoke", headers=headers)

    assert client.get(f"{API}/device/session", headers=device_headers(token)).status_code == 401
    assert (
        client.get(f"{API}/device/session", headers=device_headers("inventado")).status_code == 401
    )
    assert (
        client.get(f"{API}/device/session", headers=headers).status_code == 401
    )  # JWT no es Device


def test_devices_are_isolated_between_users(client: TestClient) -> None:
    _, rosa = new_user(client)
    _, pedro = new_user(client)
    created, _ = new_device(client, rosa)

    assert client.get(f"{API}/devices", headers=pedro).json()["total"] == 0
    assert client.post(f"{API}/devices/{created['id']}/revoke", headers=pedro).status_code == 404


def test_calls_are_recorded_by_origin(client: TestClient, db: Session) -> None:
    _, headers = new_user(client)
    _, token = new_device(client, headers)
    device = device_headers(token)

    for kind in ("wake_button", "wake_name", "wake_name"):
        response = client.post(f"{API}/device/events", headers=device, json={"kind": kind})
        assert response.status_code == 202 and response.json() == {"recorded": True}

    kinds = sorted(db.execute(select(DeviceEvent.kind)).scalars())
    assert kinds == ["wake_button", "wake_name", "wake_name"]


def test_device_cannot_report_arbitrary_event_kinds(client: TestClient) -> None:
    _, headers = new_user(client)
    _, token = new_device(client, headers)

    response = client.post(
        f"{API}/device/events", headers=device_headers(token), json={"kind": "camera_on"}
    )

    assert response.status_code == 422


def test_presence_is_ignored_while_the_camera_is_off(client: TestClient, db: Session) -> None:
    _, headers = new_user(client)
    _, token = new_device(client, headers)

    response = client.post(
        f"{API}/device/events",
        headers=device_headers(token),
        json={"kind": "presence", "value": 0.4},
    )

    assert response.json() == {"recorded": False}
    assert db.execute(select(DeviceEvent)).first() is None


def test_presence_is_recorded_with_camera_on_and_throttled(client: TestClient, db: Session) -> None:
    _, headers = new_user(client)
    _, token = new_device(client, headers)
    device = device_headers(token)
    with client.websocket_connect(f"{API}/device/stream") as socket:
        auth(socket, token)
        socket.send_json({"type": "camera", "enabled": True})
        until(socket, lambda m: m["type"] == "camera")

        first = client.post(
            f"{API}/device/events", headers=device, json={"kind": "presence", "value": 0.4}
        )
        second = client.post(
            f"{API}/device/events", headers=device, json={"kind": "presence", "value": 0.5}
        )

    assert first.json() == {"recorded": True} and second.json() == {"recorded": False}
    kinds = list(db.execute(select(DeviceEvent.kind).order_by(DeviceEvent.created_at)).scalars())
    assert kinds == ["camera_on", "presence"]


# --- Acceso familiar ----------------------------------------------------------------


def test_family_access_requires_an_accepted_contact(client: TestClient) -> None:
    _, headers = new_user(client)
    pending = client.post(
        f"{API}/contacts", headers=headers, json={"name": "Hijo", "email": "hijo@example.com"}
    ).json()["id"]

    response = client.post(f"{API}/family-access", headers=headers, json={"contact_id": pending})

    assert response.status_code == 422


def test_family_link_keeps_the_token_out_of_the_request_path(client: TestClient, providers) -> None:
    _, headers = new_user(client, preferred_name="Rosa")

    family_token(client, headers, providers)

    email = providers.email.sent[-1]
    assert "Rosa te dio acceso" in email.subject
    assert "/family/#token=" in email.text  # fragmento: no llega al servidor ni a sus logs
    assert "No incluye sus conversaciones" in email.text


def test_viewer_sees_activity_signals(knowledge: None, client: TestClient, providers) -> None:
    _, headers = new_user(client, preferred_name="Rosa")
    _, device_token = new_device(client, headers)
    device = device_headers(device_token)
    _, token = family_token(client, headers, providers)
    client.post(f"{API}/device/events", headers=device, json={"kind": "wake_button"})
    client.post(f"{API}/device/events", headers=device, json={"kind": "wake_name"})
    conversation_id = client.post(f"{API}/conversations", headers=device).json()["id"]
    proposal = client.post(
        f"{API}/conversations/{conversation_id}/messages",
        headers=device,
        json={"content": "Me gustaría una actividad con plantas y huerto"},
    ).json()["proposals"][0]
    client.post(f"{API}/proposals/{proposal['id']}/approve", headers=device, json={"version": 1})

    overview = client.get(f"{API}/family/overview", headers=viewer_headers(token)).json()

    assert overview["person_name"] == "Rosa" and overview["viewer_name"] == "Camila"
    assert overview["device"] == {
        "name": "Silvia",
        "online": True,
        "last_seen_at": overview["device"]["last_seen_at"],
    }
    assert overview["camera"] == {"allowed": True, "sharing": False}
    assert overview["today"]["interactions"] == 1
    assert overview["today"]["calls_by_button"] == 1 and overview["today"]["calls_by_name"] == 1
    assert overview["today"]["last_interaction_at"] is not None
    assert overview["week"]["activities_approved"] == 1
    assert len(overview["series"]) == 7 and overview["series"][-1]["interactions"] == 1
    labels = [entry["label"] for entry in overview["timeline"]]
    assert "Aprobó una actividad" in labels and "Llamó al asistente con el botón" in labels


def test_overview_never_exposes_conversations_or_safety_state(
    knowledge: None, client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    _, device_token = new_device(client, headers)
    device = device_headers(device_token)
    _, token = family_token(client, headers, providers)
    conversation_id = client.post(f"{API}/conversations", headers=device).json()["id"]
    for content in ("Me gusta el jazz y tejer", "Ya no quiero seguir viviendo"):
        client.post(
            f"{API}/conversations/{conversation_id}/messages",
            headers=device,
            json={"content": content},
        )

    raw = client.get(f"{API}/family/overview", headers=viewer_headers(token)).text.lower()

    for private in ("jazz", "tejer", "viviendo", "support", "urgent", "clarify", "mode", "apoyo"):
        assert private not in raw, private


def test_viewer_token_only_opens_the_family_panel(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    _, token = family_token(client, headers, providers)
    viewer = viewer_headers(token)

    for path in ("/profile", "/memories", "/conversations", "/contacts", "/proposals", "/devices"):
        assert client.get(f"{API}{path}", headers=viewer).status_code == 401, path
    assert client.get(f"{API}/device/session", headers=viewer).status_code == 401


def test_revoked_access_stops_working_immediately(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    access_id, token = family_token(client, headers, providers)

    revoked = client.post(f"{API}/family-access/{access_id}/revoke", headers=headers)

    assert revoked.json()["status"] == "revoked"
    assert client.get(f"{API}/family/overview", headers=viewer_headers(token)).status_code == 401


def test_expired_access_is_rejected(client: TestClient, providers, db: Session) -> None:
    _, headers = new_user(client)
    _, token = family_token(client, headers, providers)
    db.execute(select(FamilyAccess)).scalar_one().expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    assert client.get(f"{API}/family/overview", headers=viewer_headers(token)).status_code == 401


def test_revoking_the_contact_revokes_their_access(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    _, token = family_token(client, headers, providers)
    contact_id = client.get(f"{API}/contacts", headers=headers).json()["items"][0]["id"]

    client.post(f"{API}/contacts/{contact_id}/revoke", headers=headers)

    assert client.get(f"{API}/family/overview", headers=viewer_headers(token)).status_code == 401
    listed = client.get(f"{API}/family-access", headers=headers).json()["items"][0]
    assert listed["status"] == "revoked"


def test_family_access_is_isolated_between_users(client: TestClient, providers) -> None:
    _, rosa = new_user(client, preferred_name="Rosa")
    _, pedro = new_user(client, preferred_name="Pedro")
    access_id, token = family_token(client, rosa, providers)

    overview = client.get(f"{API}/family/overview", headers=viewer_headers(token)).json()

    assert overview["person_name"] == "Rosa"
    assert client.get(f"{API}/family-access", headers=pedro).json()["total"] == 0
    assert client.post(f"{API}/family-access/{access_id}/revoke", headers=pedro).status_code == 404


# --- Transmisión en vivo ------------------------------------------------------------


def test_stream_rejects_missing_or_invalid_tokens(client: TestClient) -> None:
    for path in ("/device/stream", "/family/stream"):
        with client.websocket_connect(f"{API}{path}") as socket:
            socket.send_json({"type": "auth", "token": "inventado"})
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
        assert closed.value.code == 4401


def test_frames_reach_the_family_only_while_the_camera_is_on(
    client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    _, device_token = new_device(client, headers)
    _, token = family_token(client, headers, providers)

    with (
        client.websocket_connect(f"{API}/device/stream") as device,
        client.websocket_connect(f"{API}/family/stream") as viewer,
    ):
        ready = auth(device, device_token)
        viewer.send_json({"type": "auth", "token": token})
        watching = until(device, lambda m: m["type"] == "viewers")

        device.send_bytes(JPEG)  # cámara apagada: no debe llegar
        device.send_json({"type": "camera", "enabled": True})
        names = until(device, lambda m: m["type"] == "viewers" and m["names"])
        device.send_bytes(JPEG_2)
        frame, statuses = next_frame(viewer)

        device.send_json({"type": "camera", "enabled": False})
        off = until(viewer, lambda m: m["type"] == "status" and not m["sharing"])

    assert ready == {"type": "ready", "camera_sharing": False}
    assert watching["names"] == []  # con la cámara apagada nadie está mirando
    assert names["names"] == ["Camila"]  # la persona mayor ve quién mira
    assert frame == JPEG_2  # el primer cuadro nunca se transmitió
    assert any(status["sharing"] for status in statuses)
    assert off["sharing"] is False
    kinds = set(db.execute(select(DeviceEvent.kind)).scalars())
    assert {"camera_on", "camera_off", "view_start"} <= kinds
    assert "camera.view_started" in set(db.execute(select(AuditEvent.action)).scalars())


def test_only_jpeg_frames_within_size_are_relayed(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    _, device_token = new_device(client, headers)
    _, token = family_token(client, headers, providers)

    with (
        client.websocket_connect(f"{API}/device/stream") as device,
        client.websocket_connect(f"{API}/family/stream") as viewer,
    ):
        auth(device, device_token)
        viewer.send_json({"type": "auth", "token": token})
        device.send_json({"type": "camera", "enabled": True})
        until(device, lambda m: m["type"] == "camera")
        device.send_bytes(b"esto no es una imagen")
        device.send_bytes(JPEG + b"x" * 400_000)
        device.send_bytes(JPEG)
        frame, _ = next_frame(viewer)

    assert frame == JPEG


def test_viewer_without_camera_permission_is_denied(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    new_device(client, headers)
    _, token = family_token(client, headers, providers, camera=False)

    with client.websocket_connect(f"{API}/family/stream") as viewer:
        denied = auth(viewer, token)
        with pytest.raises(WebSocketDisconnect) as closed:
            viewer.receive_json()

    overview = client.get(f"{API}/family/overview", headers=viewer_headers(token)).json()
    assert denied == {"type": "denied", "reason": "camera_not_allowed"}
    assert closed.value.code == 4403
    assert overview["camera"]["allowed"] is False  # las métricas sí están disponibles


def test_revoking_access_cuts_a_live_stream(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    _, device_token = new_device(client, headers)
    access_id, token = family_token(client, headers, providers)

    with (
        client.websocket_connect(f"{API}/device/stream") as device,
        client.websocket_connect(f"{API}/family/stream") as viewer,
    ):
        auth(device, device_token)
        viewer.send_json({"type": "auth", "token": token})
        device.send_json({"type": "camera", "enabled": True})
        until(device, lambda m: m["type"] == "viewers" and m["names"])

        client.post(f"{API}/family-access/{access_id}/revoke", headers=headers)
        gone = until(device, lambda m: m["type"] == "viewers" and not m["names"])
        closed = viewer.receive()
        while closed["type"] != "websocket.close":  # descarta mensajes de estado previos
            closed = viewer.receive()

    assert gone["names"] == []
    assert closed["code"] == 4403


def test_camera_state_survives_reconnection_and_shows_in_overview(
    client: TestClient, providers
) -> None:
    _, headers = new_user(client)
    _, device_token = new_device(client, headers)
    _, token = family_token(client, headers, providers)
    with client.websocket_connect(f"{API}/device/stream") as device:
        auth(device, device_token)
        device.send_json({"type": "camera", "enabled": True})
        until(device, lambda m: m["type"] == "camera")

    with client.websocket_connect(f"{API}/device/stream") as device:
        ready = auth(device, device_token)

    overview = client.get(f"{API}/family/overview", headers=viewer_headers(token)).json()
    assert ready["camera_sharing"] is True
    assert overview["camera"]["sharing"] is True
    assert "Encendió la cámara" in [entry["label"] for entry in overview["timeline"]]
