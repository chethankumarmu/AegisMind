from __future__ import annotations

import os

import httpx
import pytest
from aegismind_connector_sdk.network_guard import (
    NetworkEgressGuard,
    get_network_guard,
    set_network_guard,
)

from aegismind_core.app import create_app
from aegismind_core.routes import CoreState


@pytest.mark.asyncio
async def test_oauth_status_reports_unconfigured_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    app = create_app(CoreState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/v1/oauth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["github"]["configured"] is False
        assert data["google"]["configured"] is False


@pytest.mark.asyncio
async def test_oauth_start_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIR_GAPPED", "false")
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    app = create_app(CoreState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.get("/api/v1/oauth/github/start")
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_oauth_start_blocked_in_sovereign_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIR_GAPPED", "true")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "secret")
    app = create_app(CoreState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.get("/api/v1/oauth/github/start")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_oauth_start_redirects_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIR_GAPPED", "false")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh_client")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh_secret")
    app = create_app(CoreState())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.get("/api/v1/oauth/github/start")
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "github.com/login/oauth/authorize" in location
        assert "client_id=gh_client" in location
        assert "repo" in location


@pytest.mark.asyncio
async def test_system_mode_updates_network_guard() -> None:
    previous = get_network_guard()
    try:
        app = create_app(CoreState())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post("/api/v1/system/mode", json={"air_gapped": True})
            assert resp.status_code == 200
            body = resp.json()
            assert body["air_gapped"] is True
            assert body["external_connectors_enabled"] is False
            assert get_network_guard().is_air_gapped is True
            os.environ["AIR_GAPPED"] = "false"
            os.environ["SOVEREIGN_MODE"] = "false"
            set_network_guard(NetworkEgressGuard(air_gapped=False))
    finally:
        set_network_guard(previous)
