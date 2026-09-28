"""Local Filesystem Connector — Sovereign, air-gapped, incremental knowledge source.

This connector indexes local files into the AegisMind RAG pipeline without any
external network calls. It is the primary connector for sovereign / air-gapped mode.

Key features:
- Sensitive path denylist (never indexes credentials, private keys, .env files)
- Ignore patterns (.git, node_modules, __pycache__, dist, build, etc.)
- Supported text extensions allowlist
- SHA-256 content-hash incremental sync (skip unchanged files)
- Tombstone records for deleted files (enables soft-delete in vector store)
- Rich source metadata preserved through the ingestion pipeline
- Full compatibility with the existing AegisMind Record → ingestion pipeline flow

Security:
- This connector NEVER makes network requests.
- It NEVER indexes files matching the sensitive denylist.
- air_gapped_capable = True (always permitted in sovereign mode).
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aegismind_connector_sdk.ports import ConnectorPort, ConnectorSpec
from aegismind_connector_sdk.source_identity import (
    IncrementalSyncState,
    compute_document_id,
    detect_change,
    detect_deletions,
)
from aegismind_types import ACL, Record

logger = logging.getLogger(__name__)

DEFAULT_SENSITIVE_DENYLIST: list[str] = [
    ".ssh/*",
    "*.ssh/*",
    ".env*",
    "*.env*",
    "*credential*",
    "*secret*",
    "*.pem",
    "*.key",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    ".aws/*",
    "*.aws/*",
    ".git/config*",
    "*.git/config*",
]

DEFAULT_IGNORE_PATTERNS: list[str] = [
    "node_modules/*",
    "*/node_modules/*",
    ".git/*",
    "*/.git/*",
    "__pycache__/*",
    "*/__pycache__/*",
    ".venv/*",
    "*/.venv/*",
    "dist/*",
    "*/dist/*",
    "build/*",
    "*/build/*",
    "*.pyc",
    "*.tmp",
    "*.swp",
    "*.lock",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.bmp",
    "*.webp",
    "*.tiff",
    "*.svg",
    "*.ico",
    "*.raw",
]

SUPPORTED_TEXT_EXTENSIONS: set[str] = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".html",
    ".css",
    ".log",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
}


def _matches_any_pattern(path_str: str, patterns: list[str]) -> bool:
    """Check if normalized path matches any glob pattern."""
    normalized = path_str.replace("\\", "/")
    for pat in patterns:
        normalized_pat = pat.replace("\\", "/")
        if fnmatch.fnmatch(normalized, normalized_pat) or fnmatch.fnmatch(
            normalized.split("/")[-1], normalized_pat
        ):
            return True
        # Also match subpath components
        if "/" in normalized_pat and normalized_pat in normalized:
            return True
    return False


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of file content."""
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


class LocalFilesystemConnector(ConnectorPort):
    """Sovereign local filesystem connector with sensitive path denylist and incremental sync.

    This connector is:
    - air_gapped_capable = True (always allowed in sovereign mode)
    - network_required = False (never makes external network calls)
    - source_type = "filesystem"

    Metadata preserved in every Record for RAG citations:
        source_type = "filesystem"
        file_path = /absolute/path/to/file
        relative_path = path/relative/to/watch_root
        uri = file:///absolute/path/to/file
        content_hash = sha256(content)
        size_bytes = int
    """

    CONNECTOR_TYPE = "local_filesystem"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        raw_watch_paths = self.config.get("watch_paths") or ["./storage/docs"]
        self.watch_paths = [Path(p).resolve() for p in raw_watch_paths]
        self.sensitive_denylist = list(
            self.config.get("sensitive_denylist", DEFAULT_SENSITIVE_DENYLIST)
        )
        self.ignore_patterns = list(self.config.get("ignore_patterns", DEFAULT_IGNORE_PATTERNS))
        self.allowed_users = list(self.config.get("allowed_users", ["local_user", "alice"]))
        self.supported_extensions = set(
            self.config.get("supported_extensions", SUPPORTED_TEXT_EXTENSIONS)
        )
        self._watcher: Any = None

    def spec(self) -> ConnectorSpec:
        return ConnectorSpec(
            name=self.CONNECTOR_TYPE,
            version="0.1.0",
            description=(
                "Sovereign local filesystem connector with sensitive file denylist "
                "and SHA-256 content-hash incremental synchronization. "
                "Fully air-gapped capable. No network access required."
            ),
            config_schema={
                "type": "object",
                "properties": {
                    "watch_paths": {"type": "array", "items": {"type": "string"}},
                    "sensitive_denylist": {"type": "array", "items": {"type": "string"}},
                    "ignore_patterns": {"type": "array", "items": {"type": "string"}},
                    "allowed_users": {"type": "array", "items": {"type": "string"}},
                },
            },
            supports_incremental=True,
            supported_auth=["none"],
            network_required=False,
            air_gapped_capable=True,
            processing_location="local",
            embedding_location="local",
            vector_store_location="local",
        )

    async def check(self) -> bool:
        """Verify that at least one watch path exists or can be created."""
        for p in self.watch_paths:
            try:
                p.mkdir(parents=True, exist_ok=True)
                return True
            except Exception as exc:
                logger.warning("Could not access or create watch path %s: %s", p, exc)
        return False

    def is_sensitive(self, file_path: Path) -> bool:
        """Check if file matches sensitive denylist."""
        path_str = str(file_path).replace("\\", "/")
        return _matches_any_pattern(path_str, self.sensitive_denylist)

    def is_ignored(self, file_path: Path) -> bool:
        """Check if file matches ignore patterns."""
        path_str = str(file_path).replace("\\", "/")
        return _matches_any_pattern(path_str, self.ignore_patterns)

    async def read(
        self,
        state: dict[str, Any] | None = None,
    ) -> AsyncIterator[Record]:
        """Read files incrementally using SHA-256 content hashing.

        State dict keys (for incremental sync):
            content_hashes: dict[canonical_path, sha256_hash]

        Yields:
            Record with source_type=filesystem metadata for each new/modified file.
            Tombstone Records for deleted files (is_tombstone=True).
        """
        sync_state = IncrementalSyncState.from_cursor_dict(state)
        previous_hashes: dict[str, str] = dict(sync_state.content_hashes)
        seen_paths: set[str] = set()

        for root in self.watch_paths:
            if not root.exists():
                logger.warning("Watch path '%s' does not exist; skipping", root)
                continue

            for file_path in root.rglob("*"):
                if not file_path.is_file():
                    continue

                rel_str = str(file_path.relative_to(root)).replace("\\", "/")
                canonical_path = str(file_path.resolve())
                seen_paths.add(canonical_path)

                # 1. Access control: Sensitive path denylist check at ingestion time
                if self.is_sensitive(file_path):
                    logger.warning(
                        "SECURITY: Skipping sensitive file matching denylist: %s",
                        file_path,
                    )
                    continue

                # 2. Ignore patterns check (.gitignore style)
                if self.is_ignored(file_path):
                    logger.debug("Skipping ignored file: %s", file_path)
                    continue

                # 3. Text extension check
                if file_path.suffix.lower() not in self.supported_extensions:
                    logger.debug("Skipping unsupported extension: %s", file_path)
                    continue

                # 4. Content hashing to avoid no-op re-indexing
                try:
                    content_hash = compute_file_hash(file_path)
                except Exception as exc:
                    logger.warning("Could not read file %s for hashing: %s", file_path, exc)
                    continue

                change_state = detect_change(canonical_path, content_hash, previous_hashes)
                if change_state == "UNCHANGED":
                    logger.debug("File %s unchanged (hash: %s); skipping", file_path, content_hash)
                    continue

                # Read content
                try:
                    text_content = file_path.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    logger.warning("Failed reading file %s content: %s", file_path, exc)
                    continue

                stat = file_path.stat()
                mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                ctime_dt = datetime.fromtimestamp(stat.st_ctime, tz=UTC)

                document_id = compute_document_id(self.CONNECTOR_TYPE, canonical_path)

                yield Record(
                    id=document_id,
                    source=self.CONNECTOR_TYPE,
                    external_id=canonical_path,
                    payload={
                        "title": file_path.name,
                        "content": text_content,
                        "uri": file_path.as_uri(),
                        "path": canonical_path,
                        "relative_path": rel_str,
                        "content_hash": content_hash,
                        "size_bytes": stat.st_size,
                        # Source identity metadata for RAG citations
                        "source_type": "filesystem",
                        "file_path": canonical_path,
                        "change_state": change_state,
                    },
                    acl=ACL(
                        is_public=False,
                        allowed_principals=[f"user:{u}" for u in self.allowed_users],
                    ),
                    created_at=ctime_dt,
                    updated_at=mtime_dt,
                )

        # Detect deleted files for soft-delete tombstones
        deleted_ids = detect_deletions(seen_paths, previous_hashes)
        for prev_path in deleted_ids:
            document_id = compute_document_id(self.CONNECTOR_TYPE, prev_path)
            logger.info("File %s deleted from local filesystem; yielding tombstone", prev_path)
            yield Record(
                id=document_id,
                source=self.CONNECTOR_TYPE,
                external_id=prev_path,
                payload={
                    "title": Path(prev_path).name,
                    "content": "",
                    "is_tombstone": True,
                    "path": prev_path,
                    "source_type": "filesystem",
                    "file_path": prev_path,
                    "change_state": "DELETED",
                },
                acl=ACL(is_public=False, allowed_principals=[]),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
