"""
Shared configuration utilities for both bots.
Provides safe env-var loading with validation.
"""

import os
import sys


def _validate_required(keys):
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        print(f"[FATAL] Missing required env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def env(key: str, default: str = "", required: bool = False) -> str:
    """Load an environment variable."""
    value = os.environ.get(key, default)
    if required and not value:
        _validate_required([key])
    return value


def env_int(key: str, default: int = 0) -> int:
    """Load an integer environment variable."""
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def env_float(key: str, default: float = 0.0) -> float:
    """Load a float environment variable."""
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


def env_list(key: str, separator: str = ",") -> list[str]:
    """Load a comma-separated list from an environment variable."""
    raw = os.environ.get(key, "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(separator) if item.strip()]
