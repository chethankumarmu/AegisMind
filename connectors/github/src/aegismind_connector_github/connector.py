"""GitHub read-only knowledge connector for AegisMind.

Retrieves repository content (README, documentation, source files) from GitHub
and feeds it into the existing AegisMind ingestion pipeline.

Security contract:
- READ-ONLY: No commits, pushes, PRs, issue modifications, or writes of any kind.
- OAuth token is NEVER stored in vector metadata.
- OAuth token is NEVER sent to the LLM.
- OAuth token is stored in AegisMind SecretStore (encrypted at rest).
- Secret files are filtered at indexing time (same denylist as filesystem connector).
- Air-gapped check: raises SovereignModeViolation if AIR_GAPPED=true.

Metadata preserved per chunk (for RAG citations):
    source_type = "github"
    repository = "owner/repo"
    branch = "main"
    file_path = "src/auth/service.ts"
    commit_sha = "abc123..."
    source_url = "https://github.com/owner/repo/blob/main/src/auth/service.ts"
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from fnmatch import fnmatch
from typing import Any

import httpx
from aegismind_connector_sdk.network_guard import NetworkEgressGuard, get_network_guard
from aegismind_connector_sdk.ports import (
    ConnectorAccountInfo,
    ConnectorPort,
    ConnectorSpec,
    SourceInfo,
)
from aegismind_connector_sdk.source_identity import (
    IncrementalSyncState,
    compute_content_hash,
    compute_document_id,
    detect_change,
    detect_deletions,
)
from aegismind_types import ACL, Record

logger = logging.getLogger(__name__)

CONNECTOR_TYPE = "github"
GITHUB_API_BASE = "https://api.github.com"

# Files to never index (secrets, credentials, binaries, generated artifacts)
GITHUB_SECRET_DENYLIST: list[str] = [
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "id_rsa*",
    "id_ed25519*",
    "*secret*",
    "*credential*",
    "*password*",
    "*.DS_Store",
    "Thumbs.db",
    ".git/*",
    ".gitignore",
]

# Patterns of paths to skip entirely (generated, binary, lock files)
GITHUB_IGNORE_PATTERNS: list[str] = [
    "*.pyc",
    "*.pyo",
    "*.class",
    "*.o",
    "*.so",
    "*.dll",
    "*.exe",
    "*.bin",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.svg",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.rar",
    "*.mp4",
    "*.mp3",
    "*.pdf",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "Pipfile.lock",
    "poetry.lock",
    "*.lock",
    "node_modules/*",
    ".git/*",
    "__pycache__/*",
    "dist/*",
    "build/*",
    ".venv/*",
]

# Allowed text file extensions
GITHUB_TEXT_EXTENSIONS: set[str] = {
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".html",
    ".css",
    ".xml",
    ".proto",
    ".graphql",
}

# Maximum file size to index (bytes) — skip large generated files
MAX_FILE_SIZE_BYTES = 512 * 1024  # 512 KB


def _is_secret_file(path: str) -> bool:
    """Check if a file path matches the secret/credential denylist."""
    basename = path.split("/")[-1]
    for pattern in GITHUB_SECRET_DENYLIST:
        if fnmatch(basename, pattern) or fnmatch(path, pattern):
            return True
    return False


def _is_ignored(path: str) -> bool:
    """Check if a file path should be ignored."""
    basename = path.split("/")[-1]
    for pattern in GITHUB_IGNORE_PATTERNS:
        if fnmatch(basename, pattern) or fnmatch(path, pattern):
            return True
    return False


def _has_text_extension(path: str) -> bool:
    """Check if a file has a text extension we can index."""
    dot_idx = path.rfind(".")
    if dot_idx == -1:
        # No extension: index if basename is known text file (README, Makefile, Dockerfile)
        basename = path.split("/")[-1].lower()
        return basename in {"readme", "makefile", "dockerfile", "license", "notice", "changelog"}
    ext = path[dot_idx:].lower()
    return ext in GITHUB_TEXT_EXTENSIONS


class GitHubConnector(ConnectorPort):
    """Read-only GitHub knowledge connector.

    Connects via GitHub OAuth token (Personal Access Token or OAuth App token).
    Indexes repository content into the AegisMind vector store via the
    existing ingestion pipeline.

    Air-gapped mode:
        Raises SovereignModeViolation before any network request if AIR_GAPPED=true.
        This connector is NOT air-gapped capable.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        network_guard: NetworkEgressGuard | None = None,
    ) -> None:
        self.config = config or {}
        self._network_guard = network_guard or get_network_guard()
        self._access_token: str | None = self.config.get("access_token")
        self._selected_repos: list[str] = list(self.config.get("repositories", []))
        self._branch: str = self.config.get("default_branch", "main")
        self._max_files_per_repo: int = int(self.config.get("max_files_per_repo", 500))

    def spec(self) -> ConnectorSpec:
        return ConnectorSpec(
            name=CONNECTOR_TYPE,
            version="0.1.0",
            description=(
                "Read-only GitHub knowledge connector. Indexes repository README, "
                "documentation, and source files. Requires a GitHub Personal Access Token "
                "or OAuth token with repo:read scope. "
                "NOT available in air-gapped mode (requires GitHub API access)."
            ),
            documentation_url="https://docs.github.com/en/rest",
            config_schema={
                "type": "object",
                "properties": {
                    "repositories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of repositories in 'owner/repo' format",
                    },
                    "default_branch": {"type": "string", "default": "main"},
                    "max_files_per_repo": {"type": "integer", "default": 500},
                },
                "required": [],
            },
            supports_incremental=True,
            supported_auth=["bearer", "oauth2"],
            network_required=True,
            air_gapped_capable=False,
            processing_location="local",
            embedding_location="local",
            vector_store_location="local",
        )

    def _headers(self) -> dict[str, str]:
        """Build GitHub API auth headers."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AegisMind-GitHub-Connector/0.1.0",
        }
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def set_token(self, access_token: str) -> None:
        """Set OAuth / PAT token. Call this after OAuth flow completes.

        The token is NEVER stored in vector metadata or sent to the LLM.
        It is only held in memory for API calls.
        """
        self._access_token = access_token

    def apply_runtime_config(self, config: dict[str, Any]) -> None:
        """Apply repositories and branch settings from a connect payload."""
        if "repositories" in config:
            repos = config.get("repositories")
            if isinstance(repos, str):
                cleaned = repos.replace("repos=", "")
                self._selected_repos = [part.strip() for part in cleaned.split(",") if part.strip()]
            elif isinstance(repos, list):
                self._selected_repos = [str(item).strip() for item in repos if str(item).strip()]
            self.config["repositories"] = self._selected_repos
        if "default_branch" in config and config["default_branch"]:
            self._branch = str(config["default_branch"])
            self.config["default_branch"] = self._branch

    async def check(self) -> bool:
        """Verify connectivity and token validity by calling /user."""
        self._network_guard.assert_network_allowed(CONNECTOR_TYPE)
        if not self._access_token:
            logger.warning("GitHub connector: no access token configured")
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/user",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("GitHub check failed: %s", exc)
            return False

    async def get_account_info(self) -> ConnectorAccountInfo:
        """Fetch authenticated GitHub account information."""
        self._network_guard.assert_network_allowed(CONNECTOR_TYPE)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{GITHUB_API_BASE}/user", headers=self._headers())
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        return ConnectorAccountInfo(
            connector_id=CONNECTOR_TYPE,
            account_id=str(data["id"]),
            display_name=data.get("name") or data.get("login"),
            email=data.get("email"),
            avatar_url=data.get("avatar_url"),
            raw={"login": data.get("login"), "id": data.get("id")},
        )

    async def list_sources(self) -> list[SourceInfo]:
        """List repositories accessible to the authenticated user."""
        self._network_guard.assert_network_allowed(CONNECTOR_TYPE)
        sources: list[SourceInfo] = []
        page = 1
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                resp = await client.get(
                    f"{GITHUB_API_BASE}/user/repos",
                    headers=self._headers(),
                    params={"per_page": 100, "page": page, "sort": "updated"},
                )
                resp.raise_for_status()
                repos: list[dict[str, Any]] = resp.json()
                if not repos:
                    break
                for repo in repos:
                    full_name: str = repo["full_name"]
                    sources.append(
                        SourceInfo(
                            source_id=full_name,
                            display_name=full_name,
                            source_type="repository",
                            description=repo.get("description"),
                            metadata={
                                "private": repo.get("private", False),
                                "default_branch": repo.get("default_branch", "main"),
                                "language": repo.get("language"),
                                "size": repo.get("size", 0),
                                "updated_at": repo.get("updated_at"),
                            },
                            is_selected=full_name in self._selected_repos,
                        )
                    )
                if len(repos) < 100:
                    break
                page += 1
        return sources

    async def _fetch_repo_tree(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        branch: str,
    ) -> list[dict[str, Any]]:
        """Fetch the flat file tree for a repository branch."""
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        resp = await client.get(url, headers=self._headers())
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return [item for item in data.get("tree", []) if item.get("type") == "blob"]  # type: ignore[return-value]

    async def _fetch_file_content(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        file_path: str,
        ref: str,
    ) -> tuple[str, str] | None:
        """Fetch file content from GitHub. Returns (content_text, sha) or None."""
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{file_path}"
        resp = await client.get(
            url,
            headers=self._headers(),
            params={"ref": ref},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        if data.get("encoding") == "base64":
            try:
                raw = base64.b64decode(data["content"].replace("\n", ""))
                # Decode with fallback
                for enc in ("utf-8", "latin-1"):
                    try:
                        return raw.decode(enc), data.get("sha", "")
                    except UnicodeDecodeError:
                        continue
                # Not decodable as text
                return None
            except Exception as exc:
                logger.debug("Could not decode file %s: %s", file_path, exc)
                return None

        # Direct content (should not happen for large files but handle it)
        content = data.get("content", "")
        return content, data.get("sha", "")

    async def _get_default_branch(
        self,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
    ) -> str:
        """Retrieve the default branch name for a repository."""
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return str(resp.json().get("default_branch", "main"))

    async def read(
        self,
        state: dict[str, Any] | None = None,
    ) -> AsyncIterator[Record]:
        """Read repository files incrementally.

        Yields Record objects with GitHub metadata for each new/modified file.
        Yields tombstone Records for deleted files.

        Raises:
            SovereignModeViolation: If AIR_GAPPED=true.
        """
        self._network_guard.assert_network_allowed(CONNECTOR_TYPE)

        if not self._access_token:
            logger.error("GitHub connector: no access token; cannot read")
            return

        sync_state = IncrementalSyncState.from_cursor_dict(state)
        previous_hashes: dict[str, str] = dict(sync_state.content_hashes)
        seen_external_ids: set[str] = set()

        repos_to_index = list(self._selected_repos)
        if not repos_to_index:
            sources = await self.list_sources()
            repos_to_index = [source.source_id for source in sources[:20]]
            logger.info(
                "GitHub connector: no repositories configured, defaulting to %d accessible repos",
                len(repos_to_index),
            )
        if not repos_to_index:
            logger.warning("GitHub connector: no repositories selected; nothing to index")
            return

        async with httpx.AsyncClient(timeout=60.0) as client:
            for repo_full_name in repos_to_index:
                if "/" not in repo_full_name:
                    logger.warning(
                        "Invalid repository format (expected owner/repo): %s", repo_full_name
                    )
                    continue

                owner, repo = repo_full_name.split("/", 1)

                try:
                    branch = await self._get_default_branch(client, owner, repo)
                except Exception as exc:
                    logger.error("Could not fetch repo metadata for %s: %s", repo_full_name, exc)
                    continue

                try:
                    tree = await self._fetch_repo_tree(client, owner, repo, branch)
                except Exception as exc:
                    logger.error(
                        "Could not fetch file tree for %s@%s: %s", repo_full_name, branch, exc
                    )
                    continue

                files_indexed = 0
                for item in tree:
                    if files_indexed >= self._max_files_per_repo:
                        logger.info(
                            "GitHub connector: max_files_per_repo=%d reached for %s; stopping",
                            self._max_files_per_repo,
                            repo_full_name,
                        )
                        break

                    file_path: str = item.get("path", "")
                    file_sha: str = item.get("sha", "")
                    file_size: int = item.get("size", 0)

                    # Security: skip secret files
                    if _is_secret_file(file_path):
                        logger.warning(
                            "SECURITY: Skipping secret file in %s: %s", repo_full_name, file_path
                        )
                        continue

                    # Skip ignored patterns
                    if _is_ignored(file_path):
                        logger.debug("Skipping ignored path: %s/%s", repo_full_name, file_path)
                        continue

                    # Skip non-text files
                    if not _has_text_extension(file_path):
                        logger.debug("Skipping non-text file: %s/%s", repo_full_name, file_path)
                        continue

                    # Skip large files
                    if file_size > MAX_FILE_SIZE_BYTES:
                        logger.debug(
                            "Skipping large file (%d bytes): %s/%s",
                            file_size,
                            repo_full_name,
                            file_path,
                        )
                        continue

                    # Use file SHA from GitHub tree as the version (stable across same content)
                    external_id = f"{repo_full_name}:{branch}:{file_path}"
                    seen_external_ids.add(external_id)

                    # Incremental check: use GitHub's blob SHA as version indicator
                    previous_version = sync_state.source_versions.get(external_id)
                    if previous_version == file_sha:
                        logger.debug("File unchanged (git sha match): %s", external_id)
                        continue

                    # Fetch content
                    try:
                        result = await self._fetch_file_content(
                            client, owner, repo, file_path, branch
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to fetch %s from %s: %s", file_path, repo_full_name, exc
                        )
                        continue

                    if result is None:
                        continue

                    text_content, content_sha = result
                    content_hash = compute_content_hash(text_content)

                    change_state = detect_change(external_id, content_hash, previous_hashes)
                    if change_state == "UNCHANGED":
                        continue

                    source_url = f"https://github.com/{repo_full_name}/blob/{branch}/{file_path}"
                    document_id = compute_document_id(CONNECTOR_TYPE, external_id)

                    # Build rich metadata for RAG citations
                    payload: dict[str, Any] = {
                        "title": f"{repo_full_name}/{file_path}",
                        "content": text_content,
                        "uri": source_url,
                        # Source identity metadata
                        "source_type": "github",
                        "repository": repo_full_name,
                        "branch": branch,
                        "file_path": file_path,
                        "commit_sha": file_sha,
                        "source_url": source_url,
                        "content_hash": content_hash,
                        "change_state": change_state,
                        "size_bytes": file_size,
                    }

                    yield Record(
                        id=document_id,
                        source=CONNECTOR_TYPE,
                        external_id=external_id,
                        payload=payload,
                        acl=ACL(is_public=False, allowed_principals=["user:local_user"]),
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                    files_indexed += 1

                # Detect deleted files (were in previous state but not in current tree)
                repo_prefix = f"{repo_full_name}:"
                prev_repo_ids = {k for k in previous_hashes if k.startswith(repo_prefix)}
                deleted_ids = detect_deletions(
                    seen_external_ids, {k: previous_hashes[k] for k in prev_repo_ids}
                )
                for deleted_id in deleted_ids:
                    document_id = compute_document_id(CONNECTOR_TYPE, deleted_id)
                    parts = deleted_id.split(":", 2)
                    deleted_path = parts[2] if len(parts) == 3 else deleted_id
                    logger.info("GitHub: file deleted from %s: %s", repo_full_name, deleted_path)
                    yield Record(
                        id=document_id,
                        source=CONNECTOR_TYPE,
                        external_id=deleted_id,
                        payload={
                            "title": deleted_path,
                            "content": "",
                            "is_tombstone": True,
                            "source_type": "github",
                            "repository": repo_full_name,
                            "file_path": deleted_path,
                            "change_state": "DELETED",
                        },
                        acl=ACL(is_public=False, allowed_principals=[]),
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
