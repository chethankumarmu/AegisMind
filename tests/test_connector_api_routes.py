"""Tests for the new connector API endpoints in AegisMind Core routes.

Covers:
- GET /api/v1/connectors — enhanced listing with registry metadata
- POST /api/v1/connectors — existing sync/configure actions (regression)
- GET /api/v1/connectors/{id} — individual connector detail
- POST /api/v1/connectors/{id}/sync — direct sync trigger
- GET /api/v1/connectors/{id}/status — sync status
- GET /api/v1/connectors/{id}/sources — list sources (filesystem fallback)
- Air-gapped mode reflected in connector list response
- Audit log entries for SYNC_START/SYNC_COMPLETE/SYNC_FAILED
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from aegismind_connector_sdk.ports import ConnectorPort, ConnectorSpec
from aegismind_ingestion.ports import IngestionPipelinePort, IngestionSummary
from aegismind_types import ACL, Record

from aegismind_core.app import create_app
from aegismind_core.routes import CoreState

# ============================================================
# Helpers / Fixtures
# ============================================================


class MockConnector(ConnectorPort):
    def __init__(
        self,
        name: str = "mock_connector",
        records: list[Record] | None = None,
        network_required: bool = False,
    ) -> None:
        self.name = name
        self.records = records or []
        self._network_required = network_required

    def spec(self) -> ConnectorSpec:
        return ConnectorSpec(
            name=self.name,
            version="1.0.0",
            description=f"Mock {self.name} connector for testing",
            supported_auth=["bearer"],
            network_required=self._network_required,
            air_gapped_capable=not self._network_required,
        )

    async def check(self) -> bool:
        return True

    async def read(self, state: dict[str, Any] | None = None) -> AsyncIterator[Record]:
        for rec in self.records:
            yield rec


class MockIngestionPipeline(IngestionPipelinePort):
    def __init__(self) -> None:
        self.ingested: list[Record] = []

    async def ingest_records(self, records: list[Record]) -> IngestionSummary:
        self.ingested.extend(records)
        return IngestionSummary(
            total_records=len(records),
            chunks_indexed=len(records) * 3,
            tuples_written=len(records),
            errors=[],
        )


def make_sample_records(n: int = 2) -> list[Record]:
    return [
        Record(
            id=f"rec_{i}",
            source="test",
            external_id=f"EXT-{i:03d}",
            payload={"title": f"Document {i}", "content": f"Content of doc {i}"},
            acl=ACL(allowed_principals=["user:alice"]),
            updated_at=datetime.now(UTC),
        )
        for i in range(1, n + 1)
    ]


# ============================================================
# 1. GET /api/v1/connectors — enhanced listing
# ============================================================


@pytest.mark.asyncio
async def test_list_connectors_with_registry_data() -> None:
    """GET /connectors should return connectors merged from registry and raw dict."""
    pipeline = MockIngestionPipeline()
    state = CoreState(ingestion_pipeline=pipeline)
    conn = MockConnector("test_jira")
    state.connectors["test_jira"] = conn
    # Also register in registry
    state.connector_registry.register("test_jira", conn)

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/connectors")
        assert resp.status_code == 200
        data = resp.json()
        assert "connectors" in data
        assert "total" in data
        assert "mode" in data
        connectors = data["connectors"]
        assert len(connectors) >= 1
        names = [c["name"] for c in connectors]
        assert "test_jira" in names

        # Should have spec
        jira_entry = next(c for c in connectors if c["name"] == "test_jira")
        assert "spec" in jira_entry
        assert jira_entry["spec"]["version"] == "1.0.0"

        # Should have registration from registry
        assert "registration" in jira_entry
        assert jira_entry["registration"]["status"] == "registered"


@pytest.mark.asyncio
async def test_list_connectors_shows_mode_label() -> None:
    """Connector list should reflect air-gapped vs connected mode."""
    state = CoreState()
    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/connectors")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        # In test environment, AIR_GAPPED is not set so should be connected
        assert "CONNECTED" in data["mode"] or "SOVEREIGN" in data["mode"]


# ============================================================
# 2. GET /api/v1/connectors/{id} — individual connector detail
# ============================================================


@pytest.mark.asyncio
async def test_get_connector_detail() -> None:
    """GET /connectors/{id} should return spec and registration."""
    state = CoreState()
    conn = MockConnector("slack_connector")
    state.connectors["slack_connector"] = conn
    state.connector_registry.register("slack_connector", conn)

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/connectors/slack_connector")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connector_id"] == "slack_connector"
        assert "spec" in data
        assert data["spec"]["name"] == "slack_connector"
        assert "registration" in data
        assert data["registration"]["status"] == "registered"


@pytest.mark.asyncio
async def test_get_connector_detail_not_found() -> None:
    """GET /connectors/{id} should return 404 for unknown connector."""
    state = CoreState()
    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/connectors/nonexistent_connector")
        assert resp.status_code == 404


# ============================================================
# 3. POST /api/v1/connectors/{id}/sync — direct sync trigger
# ============================================================


@pytest.mark.asyncio
async def test_direct_sync_trigger() -> None:
    """POST /connectors/{id}/sync should trigger sync and return report."""
    records = make_sample_records(3)
    pipeline = MockIngestionPipeline()
    state = CoreState(ingestion_pipeline=pipeline)
    conn = MockConnector("confluence", records=records)
    state.connectors["confluence"] = conn

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/connectors/confluence/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["report"]["records_synced"] == 3
        assert data["report"]["chunks_indexed"] == 9
        assert len(pipeline.ingested) == 3


@pytest.mark.asyncio
async def test_direct_sync_trigger_not_found() -> None:
    """POST /connectors/{id}/sync should return 404 for unknown connector."""
    state = CoreState()
    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/connectors/nonexistent/sync")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_direct_sync_without_pipeline_returns_503() -> None:
    """POST /connectors/{id}/sync should return 503 if no ingestion pipeline."""
    state = CoreState()  # No ingestion_pipeline → no scribe_worker
    state.connectors["orphan"] = MockConnector("orphan")
    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/connectors/orphan/sync")
        assert resp.status_code == 503


# ============================================================
# 4. GET /api/v1/connectors/{id}/status
# ============================================================


@pytest.mark.asyncio
async def test_connector_status_endpoint() -> None:
    """GET /connectors/{id}/status should return sync state."""
    state = CoreState()
    conn = MockConnector("notion")
    state.connectors["notion"] = conn
    state.connector_registry.register("notion", conn)

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/connectors/notion/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connector_id"] == "notion"
        assert "status" in data
        assert "indexed_documents" in data
        assert "indexed_chunks" in data
        assert "last_sync_at" in data


@pytest.mark.asyncio
async def test_connector_status_not_found() -> None:
    """GET /connectors/{id}/status returns 404 for unknown connector."""
    state = CoreState()
    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/connectors/ghost/status")
        assert resp.status_code == 404


# ============================================================
# 5. GET /api/v1/connectors/{id}/sources
# ============================================================


@pytest.mark.asyncio
async def test_connector_sources_fallback_for_filesystem() -> None:
    """GET /connectors/{id}/sources should return fallback for connectors without list_sources."""
    state = CoreState()
    conn = MockConnector("local_fs")
    state.connectors["local_fs"] = conn

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/connectors/local_fs/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connector_id"] == "local_fs"
        assert "sources" in data
        assert len(data["sources"]) >= 1
        assert data["total"] >= 1


@pytest.mark.asyncio
async def test_connector_sources_not_found() -> None:
    state = CoreState()
    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/connectors/nonexistent/sources")
        assert resp.status_code == 404


# ============================================================
# 6. Audit log integration for sync events
# ============================================================


@pytest.mark.asyncio
async def test_sync_creates_audit_entries() -> None:
    """Direct sync should create SYNC_START and SYNC_COMPLETE audit entries."""
    records = make_sample_records(1)
    pipeline = MockIngestionPipeline()
    state = CoreState(ingestion_pipeline=pipeline)
    state.connectors["jira"] = MockConnector("jira", records=records)

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        sync_resp = await client.post("/api/v1/connectors/jira/sync")
        assert sync_resp.status_code == 200

        audit_resp = await client.get("/api/v1/audit?event_type=connector")
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["entries"]
        actions = {e["action"] for e in entries}
        assert "SYNC_START" in actions
        assert "SYNC_COMPLETE" in actions


# ============================================================
# 7. Existing connector route regression
# ============================================================


@pytest.mark.asyncio
async def test_existing_post_connectors_sync_regression() -> None:
    """Existing POST /connectors with sync action must still work (backward compat)."""
    records = make_sample_records(2)
    pipeline = MockIngestionPipeline()
    state = CoreState(ingestion_pipeline=pipeline)
    state.connectors["jira_tickets"] = MockConnector("jira_tickets", records=records)

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/connectors",
            json={
                "connector_name": "jira_tickets",
                "action": "sync",
                "initial_cursor": {"page": 1},
            },
        )
        assert resp.status_code == 200
        report = resp.json()["report"]
        assert report["status"] == "COMPLETED"
        assert report["records_synced"] == 2


@pytest.mark.asyncio
async def test_existing_post_connectors_configure_regression() -> None:
    """Existing POST /connectors with configure action must still work."""
    state = CoreState()
    state.connectors["slack"] = MockConnector("slack")

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/connectors",
            json={
                "connector_name": "slack",
                "action": "configure",
                "config": {"workspace_url": "https://example.slack.com"},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "configured"
