from __future__ import annotations
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "tracecoder.local.json"
ALLOWED_KEYS = {"api_key", "base_url", "model", "max_turns", "context_chars"}

class ConfigError(ValueError):
    pass

def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load a local JSON config without ever logging its secret values."""
    target = (path or DEFAULT_CONFIG).expanduser().resolve()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read config {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Config root must be a JSON object")
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        raise ConfigError("Unknown config keys: " + ", ".join(sorted(unknown)))
    for key in ("api_key", "base_url", "model"):
        if key in data and not isinstance(data[key], str):
            raise ConfigError(f"{key} must be a string")
    for key in ("max_turns", "context_chars"):
        if key in data and (not isinstance(data[key], int) or isinstance(data[key], bool)):
            raise ConfigError(f"{key} must be an integer")
    return data