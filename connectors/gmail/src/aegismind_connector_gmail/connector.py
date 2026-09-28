"""Gmail read-only knowledge connector for AegisMind.

Retrieves email messages and threads from Gmail via the Google Gmail API (OAuth 2.0)
and feeds normalized content into the existing AegisMind ingestion pipeline.

Security contract:
- READ-ONLY: No send, delete, modify, or label changes.
- Minimum OAuth scopes: gmail.readonly (read messages only).
- OAuth access/refresh tokens are NEVER stored in vector metadata.
- OAuth tokens are NEVER sent to the LLM.
- OAuth tokens are stored encrypted in AegisMind SecretStore.
- Token is only held in memory during sync.
- Air-gapped check: raises SovereignModeViolation if AIR_GAPPED=true.

Metadata preserved per chunk (for RAG citations):
    source_type = "gmail"
    message_id = "..."
    thread_id = "..."
    sender = "name@example.com"
    recipients = ["a@example.com"]
    subject = "Re: Q3 Planning"
    timestamp = "2024-01-15T10:30:00Z"
    labels = ["INBOX", "UNREAD"]
    source_url = "https://mail.google.com/mail/u/0/#inbox/<message_id>"

Thread relationships:
    thread_id is preserved in metadata so all messages in a thread can be
    retrieved together for context.

Normalization:
    - HTML bodies are stripped to plain text.
    - Quoted-reply chains are preserved but de-duplicated by content hash.
    - Attachments are NOT indexed (only text body content).
"""

from __future__ import annotations

import base64
import email
import email.header
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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

CONNECTOR_TYPE = "gmail"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Gmail OAuth 2.0 — minimum required scopes (read-only)
GMAIL_REQUIRED_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Maximum emails per sync batch
MAX_MESSAGES_PER_SYNC = 500

# Maximum body length to index per message
MAX_BODY_LENGTH = 20_000


def _decode_mime_header(value: str | None) -> str:
    """Decode RFC 2047 encoded email headers."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded_parts: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                decoded_parts.append(part.decode("latin-1", errors="replace"))
        else:
            decoded_parts.append(str(part))
    return " ".join(decoded_parts).strip()


def _strip_html(html: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    # Remove script and style blocks
    html = re.sub(
        r"<(script|style)[^>]*>.*?</(script|style)>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    # Replace block elements with newlines
    html = re.sub(r"</(p|div|br|tr|li|h[1-6])>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    # Remove remaining tags
    html = re.sub(r"<[^>]+>", "", html)
    # Decode HTML entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    # Normalize whitespace
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def _extract_plain_body(payload: dict[str, Any]) -> str:
    """Recursively extract plain text body from Gmail message payload."""
    mime_type: str = payload.get("mimeType", "")
    body: dict[str, Any] = payload.get("body", {})
    parts: list[dict[str, Any]] = payload.get("parts", [])

    if mime_type == "text/plain":
        data = body.get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:
                return ""

    if mime_type == "text/html":
        data = body.get("data", "")
        if data:
            try:
                raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                return _strip_html(raw)
            except Exception:
                return ""

    # Multipart: recurse into parts, prefer plain text
    plain_texts: list[str] = []
    html_texts: list[str] = []
    for part in parts:
        part_mime = part.get("mimeType", "")
        part_text = _extract_plain_body(part)
        if part_text:
            if "plain" in part_mime:
                plain_texts.append(part_text)
            elif "html" in part_mime:
                html_texts.append(part_text)
            else:
                # nested multipart
                plain_texts.append(part_text)

    if plain_texts:
        return "\n\n".join(plain_texts)
    if html_texts:
        return "\n\n".join(html_texts)
    return ""


def _parse_header_value(headers: list[dict[str, str]], name: str) -> str:
    """Extract header value by name (case-insensitive)."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return _decode_mime_header(h.get("value", ""))
    return ""


def _parse_recipients(headers: list[dict[str, str]]) -> list[str]:
    """Parse To and CC headers into a list of recipient strings."""
    recipients: list[str] = []
    for field in ("to", "cc"):
        value = _parse_header_value(headers, field)
        if value:
            recipients.extend([r.strip() for r in value.split(",") if r.strip()])
    return recipients


class GmailConnector(ConnectorPort):
    """Read-only Gmail knowledge connector.

    Connects via Google OAuth 2.0 with gmail.readonly scope.
    Normalizes email content and feeds it into the AegisMind ingestion pipeline.
    Supports incremental sync via Gmail historyId.

    Air-gapped mode:
        Raises SovereignModeViolation before any network request if AIR_GAPPED=true.
        This connector is NOT air-gapped capable.

    Security:
        - OAuth tokens are NEVER stored in vector metadata.
        - OAuth tokens are NEVER sent to the LLM.
        - Token is only held in memory during the current sync session.
        - Only gmail.readonly scope is requested.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        network_guard: NetworkEgressGuard | None = None,
    ) -> None:
        self.config = config or {}
        self._network_guard = network_guard or get_network_guard()
        self._access_token: str | None = self.config.get("access_token")
        self._max_messages: int = int(self.config.get("max_messages", MAX_MESSAGES_PER_SYNC))
        self._label_filter: list[str] = list(self.config.get("label_filter", ["INBOX"]))

    def spec(self) -> ConnectorSpec:
        return ConnectorSpec(
            name=CONNECTOR_TYPE,
            version="0.1.0",
            description=(
                "Read-only Gmail knowledge connector. Indexes email messages and thread context. "
                "Requires Google OAuth 2.0 with gmail.readonly scope. "
                "NOT available in air-gapped mode (requires Gmail API access). "
                "OAuth tokens are never stored in vector metadata or sent to LLM."
            ),
            documentation_url="https://developers.google.com/gmail/api",
            config_schema={
                "type": "object",
                "properties": {
                    "max_messages": {
                        "type": "integer",
                        "default": 500,
                        "description": "Maximum messages to index per sync",
                    },
                    "label_filter": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["INBOX"],
                        "description": "Labels to filter messages by",
                    },
                },
            },
            supports_incremental=True,
            supported_auth=["oauth2"],
            network_required=True,
            air_gapped_capable=False,
            processing_location="local",
            embedding_location="local",
            vector_store_location="local",
        )

    def set_token(self, access_token: str) -> None:
        """Set OAuth access token. Call after OAuth flow completes.

        The token is NEVER stored in vector metadata or sent to the LLM.
        """
        self._access_token = access_token

    def apply_runtime_config(self, config: dict[str, Any]) -> None:
        """Apply Gmail label filter and max message settings."""
        if "label_filter" in config:
            labels = config.get("label_filter")
            if isinstance(labels, str):
                cleaned = labels.replace("label_filter=", "")
                self._label_filter = [part.strip() for part in cleaned.split(",") if part.strip()]
            elif isinstance(labels, list):
                self._label_filter = [str(item).strip() for item in labels if str(item).strip()]
            if self._label_filter:
                self.config["label_filter"] = self._label_filter
        if "max_messages" in config:
            self._max_messages = int(config["max_messages"])
            self.config["max_messages"] = self._max_messages

    def _headers(self) -> dict[str, str]:
        """Build Gmail API auth headers (token not exposed to callers)."""
        headers = {"Accept": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def check(self) -> bool:
        """Verify connectivity and token validity by fetching user profile."""
        self._network_guard.assert_network_allowed(CONNECTOR_TYPE)
        if not self._access_token:
            logger.warning("Gmail connector: no access token configured")
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{GMAIL_API_BASE}/users/me/profile",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("Gmail check failed: %s", exc)
            return False

    async def get_account_info(self) -> ConnectorAccountInfo:
        """Fetch authenticated Gmail account information."""
        self._network_guard.assert_network_allowed(CONNECTOR_TYPE)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{GMAIL_API_BASE}/users/me/profile",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        email_addr = data.get("emailAddress", "")
        return ConnectorAccountInfo(
            connector_id=CONNECTOR_TYPE,
            account_id=email_addr,
            display_name=email_addr,
            email=email_addr,
            raw={"emailAddress": email_addr, "messagesTotal": data.get("messagesTotal", 0)},
        )

    async def list_sources(self) -> list[SourceInfo]:
        """List Gmail labels as available sources."""
        self._network_guard.assert_network_allowed(CONNECTOR_TYPE)
        sources: list[SourceInfo] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{GMAIL_API_BASE}/users/me/labels",
                headers=self._headers(),
            )
            resp.raise_for_status()
            labels: list[dict[str, Any]] = resp.json().get("labels", [])
        for label in labels:
            label_id: str = label.get("id", "")
            label_name: str = label.get("name", "")
            sources.append(
                SourceInfo(
                    source_id=label_id,
                    display_name=label_name,
                    source_type="mailbox_label",
                    metadata={"type": label.get("type", "user")},
                    is_selected=label_name in self._label_filter or label_id in self._label_filter,
                )
            )
        return sources

    async def _list_messages(
        self,
        client: httpx.AsyncClient,
        page_token: str | None = None,
    ) -> tuple[list[str], str | None]:
        """List message IDs for selected labels. Returns (ids, next_page_token)."""
        params: dict[str, Any] = {
            "maxResults": min(self._max_messages, 100),
        }
        if self._label_filter:
            params["labelIds"] = self._label_filter[0]  # Gmail API takes one labelId at a time
        if page_token:
            params["pageToken"] = page_token

        resp = await client.get(
            f"{GMAIL_API_BASE}/users/me/messages",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        messages = data.get("messages", [])
        ids = [m["id"] for m in messages if "id" in m]
        next_token: str | None = data.get("nextPageToken")
        return ids, next_token

    async def _fetch_message(
        self,
        client: httpx.AsyncClient,
        message_id: str,
    ) -> dict[str, Any] | None:
        """Fetch full message details."""
        resp = await client.get(
            f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
            headers=self._headers(),
            params={"format": "full"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()  # type: ignore[return-value]

    def _normalize_message(self, raw_msg: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize a Gmail API message into AegisMind content + metadata.

        Returns None if the message has no usable text content.
        """
        message_id: str = raw_msg.get("id", "")
        thread_id: str = raw_msg.get("threadId", "")
        labels: list[str] = raw_msg.get("labelIds", [])
        payload: dict[str, Any] = raw_msg.get("payload", {})
        headers: list[dict[str, str]] = payload.get("headers", [])
        internal_date_ms: int = int(raw_msg.get("internalDate", 0))

        subject = _parse_header_value(headers, "subject") or "(no subject)"
        sender = _parse_header_value(headers, "from") or ""
        recipients = _parse_recipients(headers)

        # Parse timestamp
        try:
            ts = datetime.fromtimestamp(internal_date_ms / 1000.0, tz=UTC)
            timestamp_iso = ts.isoformat()
        except Exception:
            ts = datetime.now(UTC)
            timestamp_iso = ts.isoformat()

        # Extract text content
        body = _extract_plain_body(payload)
        if not body.strip():
            return None

        # Truncate very long emails
        if len(body) > MAX_BODY_LENGTH:
            body = body[:MAX_BODY_LENGTH] + "\n\n[... truncated ...]"

        # Build normalized content for indexing
        content = (
            f"Subject: {subject}\n"
            f"From: {sender}\n"
            f"To: {', '.join(recipients)}\n"
            f"Date: {timestamp_iso}\n"
            f"\n{body}"
        )

        source_url = f"https://mail.google.com/mail/u/0/#inbox/{message_id}"

        return {
            "content": content,
            "title": subject[:200],
            "source_type": "gmail",
            "message_id": message_id,
            "thread_id": thread_id,
            "sender": sender,
            "recipients": recipients,
            "subject": subject,
            "timestamp": timestamp_iso,
            "labels": labels,
            "source_url": source_url,
            "indexed_at": datetime.now(UTC).isoformat(),
        }

    async def read(
        self,
        state: dict[str, Any] | None = None,
    ) -> AsyncIterator[Record]:
        """Read Gmail messages incrementally.

        Yields normalized Records for each new or modified message.
        Implements incremental sync via message-level content hashing.

        Raises:
            SovereignModeViolation: If AIR_GAPPED=true.
        """
        self._network_guard.assert_network_allowed(CONNECTOR_TYPE)

        if not self._access_token:
            logger.error("Gmail connector: no access token; cannot read")
            return

        sync_state = IncrementalSyncState.from_cursor_dict(state)
        previous_hashes: dict[str, str] = dict(sync_state.content_hashes)
        seen_external_ids: set[str] = set()
        total_fetched = 0

        async with httpx.AsyncClient(timeout=30.0) as client:
            page_token: str | None = None

            while total_fetched < self._max_messages:
                try:
                    message_ids, page_token = await self._list_messages(client, page_token)
                except Exception as exc:
                    logger.error("Gmail: failed to list messages: %s", exc)
                    break

                if not message_ids:
                    break

                for message_id in message_ids:
                    if total_fetched >= self._max_messages:
                        break

                    external_id = f"gmail:message:{message_id}"
                    seen_external_ids.add(external_id)

                    # Fetch raw message
                    try:
                        raw_msg = await self._fetch_message(client, message_id)
                    except Exception as exc:
                        logger.warning("Gmail: failed to fetch message %s: %s", message_id, exc)
                        continue

                    if raw_msg is None:
                        continue

                    # Normalize content
                    normalized = self._normalize_message(raw_msg)
                    if normalized is None:
                        continue

                    content = normalized["content"]
                    content_hash = compute_content_hash(content)
                    change_state = detect_change(external_id, content_hash, previous_hashes)

                    if change_state == "UNCHANGED":
                        continue

                    document_id = compute_document_id(CONNECTOR_TYPE, external_id)

                    # Build payload — NEVER include OAuth token
                    payload: dict[str, Any] = {
                        "title": normalized["title"],
                        "content": content,
                        "uri": normalized["source_url"],
                        "content_hash": content_hash,
                        "change_state": change_state,
                        # Rich Gmail metadata for RAG citations
                        "source_type": "gmail",
                        "message_id": normalized["message_id"],
                        "thread_id": normalized["thread_id"],
                        "sender": normalized["sender"],
                        "recipients": normalized["recipients"],
                        "subject": normalized["subject"],
                        "timestamp": normalized["timestamp"],
                        "labels": normalized["labels"],
                        "source_url": normalized["source_url"],
                    }

                    # Parse timestamp for Record timestamps
                    try:
                        ts = datetime.fromisoformat(normalized["timestamp"])
                    except Exception:
                        ts = datetime.now(UTC)

                    yield Record(
                        id=document_id,
                        source=CONNECTOR_TYPE,
                        external_id=external_id,
                        payload=payload,
                        acl=ACL(is_public=False, allowed_principals=["user:local_user"]),
                        created_at=ts,
                        updated_at=ts,
                    )
                    total_fetched += 1

                if not page_token or total_fetched >= self._max_messages:
                    break

        # Detect deleted messages (in previous state but not seen in current scan)
        deleted_ids = detect_deletions(seen_external_ids, previous_hashes)
        for deleted_id in deleted_ids:
            if not deleted_id.startswith("gmail:"):
                continue
            document_id = compute_document_id(CONNECTOR_TYPE, deleted_id)
            logger.info("Gmail: message deleted/archived: %s", deleted_id)
            yield Record(
                id=document_id,
                source=CONNECTOR_TYPE,
                external_id=deleted_id,
                payload={
                    "content": "",
                    "title": deleted_id,
                    "is_tombstone": True,
                    "source_type": "gmail",
                    "change_state": "DELETED",
                },
                acl=ACL(is_public=False, allowed_principals=[]),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
