import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Device
from app.modules.devices.demo import setup_demo
from tests.helpers import API


def token_of(url: str) -> str:
    return re.search(r"#token=([\w-]+)", url).group(1)


@pytest.mark.parametrize("page", ["/device/", "/family/"])
def test_pages_are_served_with_a_strict_content_security_policy(
    client: TestClient, page: str
) -> None:
    response = client.get(page)

    policy = response.headers["content-security-policy"]
    assert response.status_code == 200 and "<!doctype html>" in response.text
    assert "script-src 'self'" in policy and "unsafe-inline" not in policy
    assert "frame-ancestors 'none'" in policy and "object-src 'none'" in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"


def test_pages_do_not_use_inline_scripts_or_html_injection(client: TestClient) -> None:
    for path in ("/device/index.html", "/family/index.html"):
        assert re.search(r"<script(?![^>]*\bsrc=)", client.get(path).text) is None
    for path in ("/device/device.js", "/device/camera.js", "/family/family.js", "/shared/api.js"):
        source = client.get(path).text
        assert "innerHTML" not in source and "console.log" not in source, path


def test_api_responses_do_not_get_page_headers(client: TestClient) -> None:
    assert "content-security-policy" not in client.get(f"{API}/health").headers


def test_demo_setup_creates_working_links(client: TestClient) -> None:
    with session_scope() as db:
        links = setup_demo(db)

    device = {"Authorization": f"Device {token_of(links.device_url)}"}
    viewer = {"Authorization": f"Viewer {token_of(links.family_url)}"}
    session = client.get(f"{API}/device/session", headers=device).json()
    overview = client.get(f"{API}/family/overview", headers=viewer).json()

    assert "/device/#token=" in links.device_url and "/family/#token=" in links.family_url
    assert session["device_name"] == "Silvia" and session["preferred_name"] == "Rosa"
    assert overview["person_name"] == "Rosa" and overview["viewer_name"] == "Camila"
    assert overview["camera"] == {"allowed": True, "sharing": False}


def test_demo_setup_is_repeatable_and_rotates_tokens(client: TestClient, db: Session) -> None:
    with session_scope() as session:
        first = setup_demo(session)
    with session_scope() as session:
        second = setup_demo(session)

    old_device = {"Authorization": f"Device {token_of(first.device_url)}"}
    old_viewer = {"Authorization": f"Viewer {token_of(first.family_url)}"}
    new_device = {"Authorization": f"Device {token_of(second.device_url)}"}
    active = db.execute(
        select(func.count()).select_from(Device).where(Device.status == "active")
    ).scalar_one()
    assert client.get(f"{API}/device/session", headers=old_device).status_code == 401
    assert client.get(f"{API}/family/overview", headers=old_viewer).status_code == 401
    assert client.get(f"{API}/device/session", headers=new_device).status_code == 200
    assert active == 1


def test_demo_setup_refuses_to_run_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "environment", "production")

    with session_scope() as db, pytest.raises(RuntimeError):
        setup_demo(db)
