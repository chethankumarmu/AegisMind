from __future__ import annotations

import json
import logging
from typing import Any

from aegismind_infra.ports import SecretStorePort

logger = logging.getLogger(__name__)

CONNECTOR_OAUTH_MAP: dict[str, str] = {
    "github": "github",
    "google": "gmail",
}

GITHUB_OAUTH_SCOPES = "repo read:user user:email"
GOOGLE_OAUTH_SCOPES = (
    "openid email https://www.googleapis.com/auth/gmail.readonly"
)


def split_csv(value: str | list[str] | None) -> list[str]:
    """Split a comma-separated string or pass through a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    cleaned = value.replace("repos=", "").replace("watch_paths=", "").replace("label_filter=", "")
    return [part.strip() for part in cleaned.split(",") if part.strip()]


async def store_secret(secret_store: SecretStorePort, key: str, value: str) -> None:
    """Persist a secret using set_secret, with set as a compatibility fallback."""
    setter = getattr(secret_store, "set_secret", None) or getattr(secret_store, "set", None)
    if setter is None:
        raise RuntimeError("Secret store does not expose set_secret")
    await setter(key, value)


async def load_secret(secret_store: SecretStorePort, key: str) -> str | None:
    """Load a secret using get_secret, with get as a compatibility fallback."""
    getter = getattr(secret_store, "get_secret", None) or getattr(secret_store, "get", None)
    if getter is None:
        return None
    result = await getter(key)
    return str(result) if result is not None else None


def apply_connector_config(connector: Any, config: dict[str, Any]) -> None:
    """Apply runtime config onto a live connector instance."""
    if not config:
        return
    if hasattr(connector, "apply_runtime_config"):
        connector.apply_runtime_config(config)
        return

    if "watch_paths" in config and hasattr(connector, "watch_paths"):
        from pathlib import Path

        paths = split_csv(config.get("watch_paths"))  # type: ignore[arg-type]
        connector.watch_paths = [Path(p).resolve() for p in paths]
    if "repositories" in config and hasattr(connector, "_selected_repos"):
        connector._selected_repos = split_csv(config.get("repositories"))  # type: ignore[arg-type]
    if "default_branch" in config and hasattr(connector, "_branch"):
        connector._branch = str(config["default_branch"])
    if "label_filter" in config and hasattr(connector, "_label_filter"):
        connector._label_filter = split_csv(config.get("label_filter"))  # type: ignore[arg-type]


async def hydrate_connector(
    connector: Any,
    secret_store: SecretStorePort,
    connector_id: str,
    config: dict[str, Any] | None = None,
) -> None:
    """Load vault token and apply config onto the live connector before sync."""
    stored_config: dict[str, Any] = {}
    for key in ("watch_paths", "repositories", "default_branch", "label_filter"):
        stored = await load_secret(secret_store, f"{connector_id}_{key}")
        if stored:
            stored_config[key] = stored
    if stored_config:
        apply_connector_config(connector, stored_config)

    if config:
        apply_connector_config(connector, config)
        extra = config.get("extra")
        if isinstance(extra, str) and extra.strip():
            parsed: dict[str, Any] = {}
            if extra.strip().startswith("{"):
                try:
                    loaded = json.loads(extra)
                    if isinstance(loaded, dict):
                        parsed = loaded
                except json.JSONDecodeError:
                    parsed = {}
            if not parsed:
                if extra.startswith("repos=") or "/" in extra:
                    parsed["repositories"] = extra
                elif extra.startswith("label"):
                    parsed["label_filter"] = extra
                else:
                    parsed["watch_paths"] = extra
            apply_connector_config(connector, parsed)

    token = await load_secret(secret_store, f"{connector_id}_token")
    if token and hasattr(connector, "set_token"):
        connector.set_token(token)
        logger.info("Hydrated access token for connector '%s'", connector_id)
