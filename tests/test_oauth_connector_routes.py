from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from aegismind_connector_sdk.network_guard import (
    NetworkEgressGuard,
    get_network_guard,
    set_network_guard,
)
from aegismind_connector_sdk.ports import ConnectorPort, ConnectorSpec
from aegismind_types import Record
from httpx import ASGITransport, AsyncClient

from aegismind_core.app import create_app
from aegismind_core.routes import CoreState


class TokenConnector(ConnectorPort):
    def __init__(self, name: str = "github") -> None:
        self.name = name
        self.token: str | None = None
        self._selected_repos: list[str] = []
        self._network_guard: Any = None

    def spec(self) -> ConnectorSpec:
        return ConnectorSpec(
            name=self.name,
            version="1.0.0",
            description="Token test connector",
            network_required=True,
            air_gapped_capable=False,
        )

    def set_token(self, access_token: str) -> None:
        self.token = access_token

    def apply_runtime_config(self, config: dict[str, Any]) -> None:
        repos = config.get("repositories")
        if isinstance(repos, str):
            self._selected_repos = [part.strip() for part in repos.split(",") if part.strip()]

    async def check(self) -> bool:
        return bool(self.token)

    async def read(self, state: dict[str, Any] | None = None) -> AsyncIterator[Record]:
        if False:
            yield  # pragma: no cover


def _state() -> CoreState:
    return CoreState(retrieval_pipeline=MagicMock())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_connect_hydrates_token_and_config() -> None:
    state = _state()
    conn = TokenConnector("github")
    state.connectors["github"] = conn
    state.connector_registry.register("github", conn)
    app = create_app(state)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/connectors/github/connect",
            json={"token": "gho_test_token", "config": {"repositories": "octo/hello"}},
        )
        assert resp.status_code == 200
        assert conn.token == "gho_test_token"
        assert conn._selected_repos == ["octo/hello"]
        stored = await state.secret_store.get_secret("github_token")
        assert stored == "gho_test_token"

    conn.token = None
    conn._selected_repos = []
    from aegismind_core.connector_runtime import hydrate_connector

    await hydrate_connector(conn, state.secret_store, "github")
    assert conn.token == "gho_test_token"
    assert conn._selected_repos == ["octo/hello"]


@pytest.mark.asyncio
async def test_system_mode_refreshes_network_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIR_GAPPED", raising=False)
    set_network_guard(NetworkEgressGuard(air_gapped=False))
    state = _state()
    conn = TokenConnector("github")
    state.connectors["github"] = conn
    app = create_app(state)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/system/mode", json={"air_gapped": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["air_gapped"] is True
        assert body["external_connectors_enabled"] is False
        assert get_network_guard().is_air_gapped is True
        assert os.environ.get("AIR_GAPPED") == "true"


@pytest.mark.asyncio
async def test_oauth_start_without_credentials_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("AIR_GAPPED", "false")
    state = _state()
    app = create_app(state)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/oauth/github/start", follow_redirects=False)
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_oauth_start_blocked_in_sovereign(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIR_GAPPED", "true")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "secret")
    state = _state()
    app = create_app(state)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/oauth/github/start", follow_redirects=False)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_oauth_start_redirects_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIR_GAPPED", "false")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "gh_client")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "gh_secret")
    state = _state()
    app = create_app(state)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/oauth/github/start", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        assert "github.com/login/oauth/authorize" in location
        assert "client_id=gh_client" in location
        assert "scope=" in location
