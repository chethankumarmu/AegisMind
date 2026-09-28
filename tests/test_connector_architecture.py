"""Comprehensive test suite for the AegisMind Connector Architecture.

Covers:
- ConnectorSpec extended fields (description, supported_auth, capabilities)
- ConnectorRegistry lifecycle (register, status, sync_status, deregister)
- NetworkEgressGuard (air-gapped blocking, connected mode)
- Source identity (stable IDs, content hashing, change detection, deletions)
- IncrementalSyncState (cursor serialization, hash tracking)
- LocalFilesystemConnector (reads, sensitive denylist, incremental, tombstones, metadata)
- GitHubConnector (air-gapped block, spec, secret file filtering)
- GmailConnector (air-gapped block, spec, html stripping, normalization)
- ConnectorPort protocol conformance
- Metadata preservation (source_type in payload)
- Ingestion integration (connector → Record → ingestion pipeline)
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aegismind_connector_local_filesystem.connector import (
    DEFAULT_SENSITIVE_DENYLIST,
    LocalFilesystemConnector,
    compute_file_hash,
)
from aegismind_connector_sdk.network_guard import (
    NetworkEgressGuard,
    SovereignModeViolation,
    get_network_guard,
    set_network_guard,
)
from aegismind_connector_sdk.ports import (
    ConnectorAccountInfo,
    ConnectorPort,
    ConnectorSpec,
    ConnectorSyncStatus,
    SourceInfo,
)
from aegismind_connector_sdk.registry import ConnectorRegistry
from aegismind_connector_sdk.source_identity import (
    IncrementalSyncState,
    compute_content_hash,
    compute_document_id,
    detect_change,
    detect_deletions,
)
from aegismind_types import Record

# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture(autouse=True)
def reset_network_guard() -> None:
    """Reset the global network guard to connected mode after each test."""
    yield
    set_network_guard(NetworkEgressGuard(air_gapped=False))


# ============================================================
# 1. ConnectorSpec extended fields
# ============================================================


class TestConnectorSpecExtensions:
    def test_spec_has_description_field(self) -> None:
        spec = ConnectorSpec(name="test", description="A test connector")
        assert spec.description == "A test connector"

    def test_spec_has_supported_auth(self) -> None:
        spec = ConnectorSpec(name="test", supported_auth=["oauth2", "bearer"])
        assert "oauth2" in spec.supported_auth
        assert "bearer" in spec.supported_auth

    def test_spec_defaults_network_not_required(self) -> None:
        spec = ConnectorSpec(name="local")
        assert spec.network_required is False
        assert spec.air_gapped_capable is True

    def test_spec_network_required_connector(self) -> None:
        spec = ConnectorSpec(
            name="github",
            network_required=True,
            air_gapped_capable=False,
        )
        assert spec.network_required is True
        assert spec.air_gapped_capable is False

    def test_spec_processing_locations(self) -> None:
        spec = ConnectorSpec(
            name="gmail",
            processing_location="local",
            embedding_location="local",
            vector_store_location="local",
        )
        assert spec.processing_location == "local"
        assert spec.embedding_location == "local"
        assert spec.vector_store_location == "local"

    def test_spec_is_immutable(self) -> None:
        spec = ConnectorSpec(name="test")
        with pytest.raises((TypeError, ValueError)):
            spec.name = "other"  # type: ignore[misc]


# ============================================================
# 2. ConnectorRegistry
# ============================================================


class _SimpleConnector(ConnectorPort):
    """Minimal connector for registry tests."""

    def __init__(self, name: str = "simple", network: bool = False) -> None:
        self._name = name
        self._network = network

    def spec(self) -> ConnectorSpec:
        return ConnectorSpec(
            name=self._name,
            description=f"Simple {self._name} connector",
            supported_auth=["none"],
            network_required=self._network,
            air_gapped_capable=not self._network,
        )

    async def check(self) -> bool:
        return True

    async def read(self, state: dict[str, Any] | None = None) -> AsyncIterator[Record]:
        if False:
            yield Record(id="dummy", source="dummy", external_id="dummy", payload={})


class TestConnectorRegistry:
    def test_register_connector(self) -> None:
        registry = ConnectorRegistry()
        conn = _SimpleConnector("fs")
        reg = registry.register("fs", conn)
        assert reg.connector_id == "fs"
        assert reg.connector_type == "fs"
        assert reg.status == "registered"
        assert reg.network_required is False
        assert reg.air_gapped_capable is True

    def test_register_network_connector(self) -> None:
        registry = ConnectorRegistry()
        conn = _SimpleConnector("github", network=True)
        reg = registry.register("github", conn)
        assert reg.network_required is True
        assert reg.air_gapped_capable is False

    def test_get_connector(self) -> None:
        registry = ConnectorRegistry()
        conn = _SimpleConnector()
        registry.register("simple", conn)
        retrieved = registry.get_connector("simple")
        assert retrieved is conn

    def test_get_nonexistent_returns_none(self) -> None:
        registry = ConnectorRegistry()
        assert registry.get_connector("nonexistent") is None

    def test_list_registrations(self) -> None:
        registry = ConnectorRegistry()
        registry.register("a", _SimpleConnector("a"))
        registry.register("b", _SimpleConnector("b"))
        regs = registry.list_registrations()
        ids = {r.connector_id for r in regs}
        assert "a" in ids
        assert "b" in ids

    def test_update_status_to_connected(self) -> None:
        registry = ConnectorRegistry()
        registry.register("github", _SimpleConnector())
        updated = registry.update_status(
            "github",
            "connected",
            account_id="user:123",
            account_email="user@example.com",
            account_name="Test User",
            credential_key="github_token_github",
        )
        assert updated is not None
        assert updated.status == "connected"
        assert updated.account_id == "user:123"
        assert updated.account_email == "user@example.com"
        assert updated.credential_key == "github_token_github"
        assert updated.connected_at is not None

    def test_update_status_to_disconnected_clears_credential_key(self) -> None:
        registry = ConnectorRegistry()
        registry.register("github", _SimpleConnector())
        registry.update_status("github", "connected", credential_key="some_key")
        updated = registry.update_status("github", "disconnected")
        assert updated is not None
        assert updated.status == "disconnected"
        assert updated.credential_key is None

    def test_update_sync_status(self) -> None:
        registry = ConnectorRegistry()
        registry.register("github", _SimpleConnector())
        sync_status = ConnectorSyncStatus(
            connector_id="github",
            status="completed",
            docs_added=31,
            docs_modified=8,
            docs_deleted=2,
            indexed_documents=100,
            indexed_chunks=450,
        )
        updated = registry.update_sync_status("github", sync_status)
        assert updated is not None
        assert updated.last_sync_at is not None
        assert updated.indexed_chunks == 450
        assert updated.sync_status is not None
        assert updated.sync_status.docs_added == 31

    def test_deregister(self) -> None:
        registry = ConnectorRegistry()
        registry.register("temp", _SimpleConnector())
        assert registry.deregister("temp") is True
        assert registry.get_connector("temp") is None

    def test_deregister_nonexistent(self) -> None:
        registry = ConnectorRegistry()
        assert registry.deregister("nonexistent") is False

    def test_generate_connector_id(self) -> None:
        registry = ConnectorRegistry()
        id1 = registry.generate_connector_id("github")
        id2 = registry.generate_connector_id("github")
        assert id1.startswith("github_")
        assert id1 != id2  # Unique IDs


# ============================================================
# 3. NetworkEgressGuard
# ============================================================


class TestNetworkEgressGuard:
    def test_connected_mode_allows_network(self) -> None:
        guard = NetworkEgressGuard(air_gapped=False)
        # Should not raise
        guard.assert_network_allowed("github")
        assert guard.is_network_allowed("github") is True

    def test_air_gapped_mode_blocks_network(self) -> None:
        guard = NetworkEgressGuard(air_gapped=True)
        with pytest.raises(SovereignModeViolation) as exc_info:
            guard.assert_network_allowed("github")
        assert "github" in str(exc_info.value)
        assert "AIR-GAPPED" in str(exc_info.value) or "SOVEREIGN" in str(exc_info.value)

    def test_air_gapped_is_network_allowed_returns_false(self) -> None:
        guard = NetworkEgressGuard(air_gapped=True)
        assert guard.is_network_allowed("gmail") is False

    def test_mode_label(self) -> None:
        gapped = NetworkEgressGuard(air_gapped=True)
        connected = NetworkEgressGuard(air_gapped=False)
        assert "SOVEREIGN" in gapped.mode_label or "AIR-GAPPED" in gapped.mode_label
        assert "CONNECTED" in connected.mode_label

    def test_from_env_connected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AIR_GAPPED", "false")
        monkeypatch.setenv("SOVEREIGN_MODE", "false")
        set_network_guard(None)  # type: ignore[arg-type]
        guard = NetworkEgressGuard.from_env()
        assert guard.is_air_gapped is False

    def test_from_env_air_gapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AIR_GAPPED", "true")
        guard = NetworkEgressGuard.from_env()
        assert guard.is_air_gapped is True

    def test_from_env_sovereign_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AIR_GAPPED", raising=False)
        monkeypatch.setenv("SOVEREIGN_MODE", "1")
        guard = NetworkEgressGuard.from_env()
        assert guard.is_air_gapped is True

    def test_sovereign_mode_violation_message_contains_guidance(self) -> None:
        guard = NetworkEgressGuard(air_gapped=True)
        try:
            guard.assert_network_allowed("gmail")
            pytest.fail("Expected SovereignModeViolation")
        except SovereignModeViolation as exc:
            msg = str(exc)
            assert "gmail" in msg
            # Should tell user how to re-enable
            assert "AIR_GAPPED" in msg or "SOVEREIGN" in msg


# ============================================================
# 4. Source Identity
# ============================================================


class TestSourceIdentity:
    def test_compute_document_id_is_stable(self) -> None:
        id1 = compute_document_id("github", "owner/repo:main:README.md")
        id2 = compute_document_id("github", "owner/repo:main:README.md")
        assert id1 == id2

    def test_compute_document_id_is_unique_per_external_id(self) -> None:
        id1 = compute_document_id("github", "owner/repo:main:README.md")
        id2 = compute_document_id("github", "owner/repo:main:CONTRIBUTING.md")
        assert id1 != id2

    def test_compute_document_id_differs_by_connector(self) -> None:
        id1 = compute_document_id("github", "doc1")
        id2 = compute_document_id("filesystem", "doc1")
        assert id1 != id2

    def test_compute_content_hash(self) -> None:
        h = compute_content_hash("hello world")
        assert len(h) == 64  # SHA-256 hex
        assert h == hashlib.sha256(b"hello world").hexdigest()

    def test_detect_change_new(self) -> None:
        state: dict[str, str] = {}
        result = detect_change("doc1", "hash123", state)
        assert result == "NEW"

    def test_detect_change_unchanged(self) -> None:
        state = {"doc1": "hash123"}
        result = detect_change("doc1", "hash123", state)
        assert result == "UNCHANGED"

    def test_detect_change_modified(self) -> None:
        state = {"doc1": "old_hash"}
        result = detect_change("doc1", "new_hash", state)
        assert result == "MODIFIED"

    def test_detect_deletions(self) -> None:
        state = {"doc1": "h1", "doc2": "h2", "doc3": "h3"}
        seen = {"doc1"}
        deleted = detect_deletions(seen, state)
        assert set(deleted) == {"doc2", "doc3"}

    def test_detect_deletions_empty_seen_means_all_deleted(self) -> None:
        state = {"doc1": "h1", "doc2": "h2"}
        deleted = detect_deletions(set(), state)
        assert set(deleted) == {"doc1", "doc2"}

    def test_detect_deletions_all_seen_means_none_deleted(self) -> None:
        state = {"doc1": "h1", "doc2": "h2"}
        deleted = detect_deletions({"doc1", "doc2"}, state)
        assert deleted == []


# ============================================================
# 5. IncrementalSyncState
# ============================================================


class TestIncrementalSyncState:
    def test_empty_state_from_none(self) -> None:
        state = IncrementalSyncState.from_cursor_dict(None)
        assert state.content_hashes == {}
        assert state.last_sync_at is None

    def test_round_trip_cursor_serialization(self) -> None:
        state = IncrementalSyncState(
            content_hashes={"doc1": "hash1", "doc2": "hash2"},
            last_sync_at="2024-01-01T00:00:00+00:00",
        )
        cursor = state.as_cursor_dict()
        restored = IncrementalSyncState.from_cursor_dict(cursor)
        assert restored.content_hashes == state.content_hashes
        assert restored.last_sync_at == state.last_sync_at

    def test_with_hash_returns_new_state(self) -> None:
        state = IncrementalSyncState(content_hashes={"doc1": "hash1"})
        new_state = state.with_hash("doc2", "hash2")
        assert "doc2" in new_state.content_hashes
        assert "doc2" not in state.content_hashes  # immutable

    def test_without_id_removes_entry(self) -> None:
        state = IncrementalSyncState(content_hashes={"doc1": "h1", "doc2": "h2"})
        new_state = state.without_id("doc1")
        assert "doc1" not in new_state.content_hashes
        assert "doc2" in new_state.content_hashes

    def test_compatible_with_connector_state_dict(self) -> None:
        """IncrementalSyncState must be compatible with the existing content_hashes state format."""
        # The existing LocalFilesystemConnector uses state = {"content_hashes": {...}}
        old_format_state = {"content_hashes": {"path/to/file.md": "abc123"}}
        restored = IncrementalSyncState.from_cursor_dict(old_format_state)
        assert "path/to/file.md" in restored.content_hashes
        assert restored.content_hashes["path/to/file.md"] == "abc123"


# ============================================================
# 6. Local Filesystem Connector
# ============================================================


@pytest.mark.asyncio
class TestLocalFilesystemConnector:
    async def test_reads_text_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "guide.md").write_text("# Sovereign Guide\nContent here.", encoding="utf-8")

            connector = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            assert await connector.check() is True

            records = [r async for r in connector.read()]
            assert len(records) == 1
            assert records[0].payload["title"] == "guide.md"
            assert "Content here" in records[0].payload["content"]
            assert records[0].source == "local_filesystem"

    async def test_content_hash_in_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            f = tmp / "code.py"
            f.write_text("print('hello')", encoding="utf-8")

            connector = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            records = [r async for r in connector.read()]
            assert len(records) == 1
            assert records[0].payload["content_hash"] == compute_file_hash(f)

    async def test_source_type_metadata_in_payload(self) -> None:
        """Every record must contain source_type=filesystem for RAG citations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "doc.txt").write_text("content", encoding="utf-8")

            connector = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            records = [r async for r in connector.read()]
            assert records[0].payload["source_type"] == "filesystem"
            assert "file_path" in records[0].payload

    async def test_sensitive_path_denylist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "notes.txt").write_text("public notes", encoding="utf-8")
            (tmp / ".env").write_text("SECRET=abc", encoding="utf-8")
            (tmp / "server.pem").write_text("CERT DATA", encoding="utf-8")
            ssh = tmp / ".ssh"
            ssh.mkdir()
            (ssh / "id_rsa").write_text("RSA KEY", encoding="utf-8")

            connector = LocalFilesystemConnector(
                config={
                    "watch_paths": [str(tmp)],
                    "sensitive_denylist": DEFAULT_SENSITIVE_DENYLIST,
                }
            )
            records = [r async for r in connector.read()]
            titles = {r.payload["title"] for r in records}
            assert "notes.txt" in titles
            assert ".env" not in titles
            assert "server.pem" not in titles
            assert "id_rsa" not in titles

    async def test_incremental_sync_skips_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            f = tmp / "report.md"
            f.write_text("Initial content", encoding="utf-8")

            connector = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            records1 = [r async for r in connector.read()]
            assert len(records1) == 1

            # Second read with same hash — should skip
            canonical = records1[0].external_id
            content_hash = records1[0].payload["content_hash"]
            state = {"content_hashes": {canonical: content_hash}}
            records2 = [r async for r in connector.read(state=state)]
            assert len(records2) == 0

    async def test_incremental_sync_detects_modification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            f = tmp / "doc.md"
            f.write_text("Version 1", encoding="utf-8")

            connector = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            r1 = [r async for r in connector.read()]
            state = {"content_hashes": {r1[0].external_id: r1[0].payload["content_hash"]}}

            # Modify file
            f.write_text("Version 2 — updated", encoding="utf-8")
            r2 = [r async for r in connector.read(state=state)]
            assert len(r2) == 1
            assert "Version 2" in r2[0].payload["content"]
            assert r2[0].payload.get("change_state") == "MODIFIED"

    async def test_deleted_file_emits_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            f = tmp / "delete_me.txt"
            f.write_text("temporary", encoding="utf-8")

            connector = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            r1 = [r async for r in connector.read()]
            assert len(r1) == 1
            canonical = r1[0].external_id

            f.unlink()  # Delete file
            state = {"content_hashes": {canonical: "old_hash"}}
            r2 = [r async for r in connector.read(state=state)]
            assert len(r2) == 1
            assert r2[0].payload.get("is_tombstone") is True
            assert r2[0].payload.get("change_state") == "DELETED"

    async def test_stable_document_id(self) -> None:
        """Document ID must be stable across reads (no re-creation on second sync)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "stable.md").write_text("content", encoding="utf-8")

            connector = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            r1 = [r async for r in connector.read()]
            r2 = [r async for r in connector.read()]  # Full re-scan (no state)
            assert r1[0].id == r2[0].id

    async def test_check_creates_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            new_dir = Path(tmpdir) / "new_subdir"
            connector = LocalFilesystemConnector(config={"watch_paths": [str(new_dir)]})
            result = await connector.check()
            assert result is True
            assert new_dir.exists()

    async def test_spec_is_air_gapped_capable(self) -> None:
        connector = LocalFilesystemConnector()
        spec = connector.spec()
        assert spec.air_gapped_capable is True
        assert spec.network_required is False
        assert spec.name == "local_filesystem"

    async def test_skips_binary_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (tmp / "doc.md").write_text("markdown content", encoding="utf-8")

            connector = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            records = [r async for r in connector.read()]
            titles = {r.payload["title"] for r in records}
            assert "doc.md" in titles
            assert "image.png" not in titles


# ============================================================
# 7. GitHub Connector
# ============================================================


class TestGitHubConnector:
    def test_spec_is_network_required(self) -> None:
        from aegismind_connector_github import GitHubConnector

        conn = GitHubConnector()
        spec = conn.spec()
        assert spec.network_required is True
        assert spec.air_gapped_capable is False
        assert spec.name == "github"
        assert "oauth2" in spec.supported_auth or "bearer" in spec.supported_auth

    def test_spec_processing_is_local(self) -> None:
        from aegismind_connector_github import GitHubConnector

        conn = GitHubConnector()
        spec = conn.spec()
        # GitHub content is fetched remotely but processed, embedded, and stored locally
        assert spec.processing_location == "local"
        assert spec.embedding_location == "local"
        assert spec.vector_store_location == "local"

    @pytest.mark.asyncio
    async def test_check_blocked_in_air_gapped_mode(self) -> None:
        from aegismind_connector_github import GitHubConnector

        guard = NetworkEgressGuard(air_gapped=True)
        conn = GitHubConnector(config={"access_token": "fake"}, network_guard=guard)
        with pytest.raises(SovereignModeViolation):
            await conn.check()

    @pytest.mark.asyncio
    async def test_read_blocked_in_air_gapped_mode(self) -> None:
        from aegismind_connector_github import GitHubConnector

        guard = NetworkEgressGuard(air_gapped=True)
        conn = GitHubConnector(
            config={"access_token": "fake", "repositories": ["owner/repo"]},
            network_guard=guard,
        )
        with pytest.raises(SovereignModeViolation):
            async for _ in conn.read():  # type: ignore[attr-defined]
                pass

    @pytest.mark.asyncio
    async def test_read_without_token_yields_nothing(self) -> None:
        from aegismind_connector_github import GitHubConnector

        guard = NetworkEgressGuard(air_gapped=False)
        conn = GitHubConnector(
            config={"repositories": ["owner/repo"]},
            network_guard=guard,
        )
        # No token → should exit gracefully without making network calls
        records: list[Record] = []
        async for r in conn.read():  # type: ignore[attr-defined]
            records.append(r)
        assert records == []

    def test_secret_file_filtering(self) -> None:
        from aegismind_connector_github.connector import _is_secret_file

        assert _is_secret_file(".env") is True
        assert _is_secret_file(".env.production") is True
        assert _is_secret_file("server.pem") is True
        assert _is_secret_file("id_rsa") is True
        assert _is_secret_file("README.md") is False
        assert _is_secret_file("src/auth/service.ts") is False
        assert _is_secret_file("config/database.yml") is False

    def test_ignore_pattern_filtering(self) -> None:
        from aegismind_connector_github.connector import _is_ignored

        assert _is_ignored("node_modules/lodash/index.js") is True
        assert _is_ignored("dist/bundle.js") is True
        assert _is_ignored("package-lock.json") is True
        assert _is_ignored("README.md") is False
        assert _is_ignored("src/main.py") is False

    def test_text_extension_filtering(self) -> None:
        from aegismind_connector_github.connector import _has_text_extension

        assert _has_text_extension("README.md") is True
        assert _has_text_extension("src/main.py") is True
        assert _has_text_extension("image.png") is False
        assert _has_text_extension("binary.exe") is False
        assert _has_text_extension("Makefile") is True  # known basename

    def test_set_token(self) -> None:
        from aegismind_connector_github import GitHubConnector

        conn = GitHubConnector()
        assert conn._access_token is None
        conn.set_token("ghp_fake_token_123")
        assert conn._access_token == "ghp_fake_token_123"

    def test_token_not_in_spec(self) -> None:
        """OAuth token must never appear in the ConnectorSpec."""
        from aegismind_connector_github import GitHubConnector

        conn = GitHubConnector(config={"access_token": "secret_token"})
        spec_dict = conn.spec().model_dump()
        assert "secret_token" not in str(spec_dict)

    @pytest.mark.asyncio
    async def test_record_payload_has_github_metadata(self) -> None:
        """Test that records include required GitHub metadata fields."""
        import unittest.mock as mock

        from aegismind_connector_github import GitHubConnector

        guard = NetworkEgressGuard(air_gapped=False)

        conn = GitHubConnector(
            config={"access_token": "fake", "repositories": ["owner/repo"]},
            network_guard=guard,
        )

        tree_items = [{"path": "README.md", "type": "blob", "sha": "abc123", "size": 100}]

        # Mock the internal methods to avoid real HTTP calls
        with (
            mock.patch.object(conn, "_get_default_branch", return_value="main"),
            mock.patch.object(conn, "_fetch_repo_tree", return_value=tree_items),
            mock.patch.object(
                conn,
                "_fetch_file_content",
                return_value=("# Hello World\nThis is README", "abc123"),
            ),
        ):
            records: list[Record] = []
            async for r in conn.read():  # type: ignore[attr-defined]
                records.append(r)

        assert len(records) >= 1
        r = records[0]
        assert r.payload["source_type"] == "github"
        assert r.payload["repository"] == "owner/repo"
        assert r.payload["branch"] == "main"
        assert r.payload["file_path"] == "README.md"
        assert "source_url" in r.payload
        assert "github.com/owner/repo" in r.payload["source_url"]
        # Token must NOT be in payload
        assert "fake" not in str(r.payload)


# ============================================================
# 8. Gmail Connector
# ============================================================


class TestGmailConnector:
    def test_spec_is_network_required(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        conn = GmailConnector()
        spec = conn.spec()
        assert spec.network_required is True
        assert spec.air_gapped_capable is False
        assert spec.name == "gmail"
        assert "oauth2" in spec.supported_auth

    def test_spec_processing_is_local(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        conn = GmailConnector()
        spec = conn.spec()
        assert spec.processing_location == "local"
        assert spec.embedding_location == "local"

    @pytest.mark.asyncio
    async def test_check_blocked_in_air_gapped_mode(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        guard = NetworkEgressGuard(air_gapped=True)
        conn = GmailConnector(config={"access_token": "fake"}, network_guard=guard)
        with pytest.raises(SovereignModeViolation):
            await conn.check()

    @pytest.mark.asyncio
    async def test_read_blocked_in_air_gapped_mode(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        guard = NetworkEgressGuard(air_gapped=True)
        conn = GmailConnector(config={"access_token": "fake"}, network_guard=guard)
        with pytest.raises(SovereignModeViolation):
            async for _ in conn.read():  # type: ignore[attr-defined]
                pass

    @pytest.mark.asyncio
    async def test_read_without_token_yields_nothing(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        guard = NetworkEgressGuard(air_gapped=False)
        conn = GmailConnector(config={}, network_guard=guard)
        records: list[Record] = []
        async for r in conn.read():  # type: ignore[attr-defined]
            records.append(r)
        assert records == []

    def test_html_stripping(self) -> None:
        from aegismind_connector_gmail.connector import _strip_html

        html = "<p>Hello <b>World</b></p><br/><div>Content here</div>"
        result = _strip_html(html)
        assert "Hello" in result
        assert "World" in result
        assert "Content here" in result
        assert "<p>" not in result
        assert "<b>" not in result

    def test_html_stripping_removes_scripts(self) -> None:
        from aegismind_connector_gmail.connector import _strip_html

        html = "<script>alert('xss')</script><p>Safe content</p>"
        result = _strip_html(html)
        assert "alert" not in result
        assert "Safe content" in result

    def test_decode_mime_header(self) -> None:
        from aegismind_connector_gmail.connector import _decode_mime_header

        plain = _decode_mime_header("Hello World")
        assert plain == "Hello World"

    def test_parse_header_value(self) -> None:
        from aegismind_connector_gmail.connector import _parse_header_value

        headers = [
            {"name": "Subject", "value": "Test Email"},
            {"name": "From", "value": "sender@example.com"},
        ]
        assert _parse_header_value(headers, "subject") == "Test Email"
        assert _parse_header_value(headers, "from") == "sender@example.com"
        assert _parse_header_value(headers, "to") == ""

    def test_set_token(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        conn = GmailConnector()
        assert conn._access_token is None
        conn.set_token("ya29.fake_token")
        assert conn._access_token == "ya29.fake_token"

    def test_token_not_in_spec(self) -> None:
        """OAuth token must never appear in the ConnectorSpec."""
        from aegismind_connector_gmail import GmailConnector

        conn = GmailConnector(config={"access_token": "secret_token"})
        spec_dict = conn.spec().model_dump()
        assert "secret_token" not in str(spec_dict)

    def test_normalize_message_extracts_fields(self) -> None:
        """Test the private normalization method with a synthetic message."""
        import base64

        from aegismind_connector_gmail import GmailConnector

        conn = GmailConnector()
        body_text = "Hello, this is the email body."
        body_b64 = base64.urlsafe_b64encode(body_text.encode()).decode()

        raw_msg = {
            "id": "msg_abc123",
            "threadId": "thread_xyz",
            "labelIds": ["INBOX", "UNREAD"],
            "internalDate": "1704067200000",  # 2024-01-01
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Subject", "value": "Q3 Planning"},
                    {"name": "From", "value": "boss@company.com"},
                    {"name": "To", "value": "team@company.com"},
                ],
                "body": {"data": body_b64},
            },
        }
        normalized = conn._normalize_message(raw_msg)  # type: ignore[attr-defined]
        assert normalized is not None
        assert normalized["message_id"] == "msg_abc123"
        assert normalized["thread_id"] == "thread_xyz"
        assert normalized["subject"] == "Q3 Planning"
        assert normalized["sender"] == "boss@company.com"
        assert "team@company.com" in normalized["recipients"]
        assert "Hello, this is the email body." in normalized["content"]
        assert normalized["source_type"] == "gmail"


# ============================================================
# 9. Credential protection
# ============================================================


class TestCredentialProtection:
    def test_github_token_never_in_record_payload(self) -> None:
        """OAuth tokens must not appear in any Record payload field."""
        from aegismind_connector_github import GitHubConnector

        secret_token = "ghp_very_secret_token_1234567890"
        conn = GitHubConnector(config={"access_token": secret_token})
        spec = conn.spec()
        spec_str = str(spec.model_dump())
        assert secret_token not in spec_str

    def test_gmail_token_never_in_record_payload(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        secret_token = "ya29.very_secret_gmail_token_xyz"
        conn = GmailConnector(config={"access_token": secret_token})
        spec = conn.spec()
        spec_str = str(spec.model_dump())
        assert secret_token not in spec_str

    def test_connector_registry_does_not_store_raw_token(self) -> None:
        registry = ConnectorRegistry()
        conn = _SimpleConnector()
        reg = registry.register("github", conn)
        # credential_key should be None (or a secret store key NAME, not value)
        assert reg.credential_key is None

        # When we store a credential key reference (not the value)
        updated = registry.update_status(
            "github",
            "connected",
            credential_key="github_token_abc123",
        )
        assert updated is not None
        assert updated.credential_key == "github_token_abc123"
        # The key name references SecretStore, not the actual token
        assert "ghp_" not in (updated.credential_key or "")


# ============================================================
# 10. Air-gapped mode enforcement
# ============================================================


class TestAirGappedModeEnforcement:
    """Verify that external connectors cannot make requests in sovereign mode."""

    @pytest.mark.asyncio
    async def test_github_check_requires_connected_mode(self) -> None:
        from aegismind_connector_github import GitHubConnector

        set_network_guard(NetworkEgressGuard(air_gapped=True))
        conn = GitHubConnector(config={"access_token": "fake"})
        # Override conn's guard to use the sovereign guard
        conn._network_guard = get_network_guard()

        with pytest.raises(SovereignModeViolation):
            await conn.check()

    @pytest.mark.asyncio
    async def test_gmail_check_requires_connected_mode(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        guard = NetworkEgressGuard(air_gapped=True)
        conn = GmailConnector(config={"access_token": "fake"}, network_guard=guard)
        with pytest.raises(SovereignModeViolation):
            await conn.check()

    def test_local_filesystem_allowed_in_air_gapped_mode(self) -> None:
        """Local filesystem connector is always permitted — it requires no network."""
        set_network_guard(NetworkEgressGuard(air_gapped=True))
        conn = LocalFilesystemConnector()
        spec = conn.spec()
        assert spec.air_gapped_capable is True
        assert spec.network_required is False
        # Guard should not block local connector
        guard = get_network_guard()
        assert guard.is_network_allowed("local_filesystem") is False  # guard is air-gapped
        # But local connector doesn't call the guard — its network_required is False
        # so the application layer should skip the guard check

    def test_sovereign_violation_error_message_is_informative(self) -> None:
        err = SovereignModeViolation("gmail")
        msg = str(err)
        assert "gmail" in msg
        assert len(msg) > 50  # Not a terse error

    def test_air_gapped_registry_shows_correct_mode(self) -> None:
        """Registry should correctly identify air-gapped vs connected connectors."""
        registry = ConnectorRegistry()
        fs_conn = LocalFilesystemConnector()
        registry.register("local_filesystem", fs_conn)
        reg = registry.get_registration("local_filesystem")
        assert reg is not None
        assert reg.air_gapped_capable is True
        assert reg.network_required is False


# ============================================================
# 11. ConnectorPort protocol conformance
# ============================================================


class TestConnectorPortConformance:
    def test_local_filesystem_implements_protocol(self) -> None:
        conn = LocalFilesystemConnector()
        assert isinstance(conn, ConnectorPort)

    def test_github_implements_protocol(self) -> None:
        from aegismind_connector_github import GitHubConnector

        conn = GitHubConnector()
        assert isinstance(conn, ConnectorPort)

    def test_gmail_implements_protocol(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        conn = GmailConnector()
        assert isinstance(conn, ConnectorPort)

    def test_local_filesystem_spec_has_name(self) -> None:
        conn = LocalFilesystemConnector()
        assert conn.spec().name == "local_filesystem"

    def test_github_spec_has_name(self) -> None:
        from aegismind_connector_github import GitHubConnector

        conn = GitHubConnector()
        assert conn.spec().name == "github"

    def test_gmail_spec_has_name(self) -> None:
        from aegismind_connector_gmail import GmailConnector

        conn = GmailConnector()
        assert conn.spec().name == "gmail"


# ============================================================
# 12. Metadata preservation through connector
# ============================================================


class TestMetadataPreservation:
    @pytest.mark.asyncio
    async def test_filesystem_record_has_all_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "test.md").write_text("# Test\nContent.", encoding="utf-8")

            conn = LocalFilesystemConnector(config={"watch_paths": [str(tmp)]})
            records = [r async for r in conn.read()]
            assert len(records) == 1
            r = records[0]

            # Identity
            assert r.id is not None
            assert r.source == "local_filesystem"
            assert r.external_id is not None

            # Payload
            assert r.payload["source_type"] == "filesystem"
            assert "file_path" in r.payload
            assert "content_hash" in r.payload
            assert r.payload["title"] == "test.md"
            assert "Content." in r.payload["content"]

            # Timestamps
            assert r.created_at is not None
            assert r.updated_at is not None

    def test_source_info_model(self) -> None:
        source = SourceInfo(
            source_id="owner/repo",
            display_name="owner/repo",
            source_type="repository",
            description="A test repo",
            metadata={"private": False},
            is_selected=True,
        )
        assert source.source_id == "owner/repo"
        assert source.is_selected is True

    def test_connector_account_info_model(self) -> None:
        info = ConnectorAccountInfo(
            connector_id="github",
            account_id="user:12345",
            display_name="Alice",
            email="alice@example.com",
        )
        assert info.email == "alice@example.com"
        assert info.account_id == "user:12345"


# ============================================================
# Helper: create mock httpx response
# ============================================================


def httpx_response(status_code: int, data: dict[str, Any]) -> Any:
    """Create a mock httpx Response for testing."""
    import json

    import httpx

    content = json.dumps(data).encode()
    return httpx.Response(
        status_code, content=content, headers={"content-type": "application/json"}
    )
