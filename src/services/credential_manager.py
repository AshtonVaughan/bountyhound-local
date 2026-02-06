"""Credential manager - .env file storage, loading, and token refresh."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path


FINDINGS_DIR = Path.home() / "bounty-findings"


def get_creds_path(target: str) -> Path:
    return FINDINGS_DIR / target / "credentials" / f"{target}-creds.env"


def ensure_creds_dir(target: str) -> Path:
    path = FINDINGS_DIR / target / "credentials"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_credentials(target: str) -> dict:
    """Load credentials from .env file into dict."""
    path = get_creds_path(target)
    if not path.exists():
        return {}

    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                creds[key.strip()] = value.strip()
    return creds


def save_credentials(target: str, creds: dict):
    """Save credentials dict to .env file."""
    ensure_creds_dir(target)
    path = get_creds_path(target)

    lines = [
        f"# Target: {target}",
        f"# Created: {datetime.now(timezone.utc).isoformat()}",
        f"# Last refreshed: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    user_a_keys = sorted([k for k in creds if k.startswith("USER_A_")])
    user_b_keys = sorted([k for k in creds if k.startswith("USER_B_")])
    other_keys = sorted([k for k in creds if not k.startswith("USER_A_") and not k.startswith("USER_B_")])

    if user_a_keys:
        lines.append("# --- User A (primary) ---")
        for k in user_a_keys:
            lines.append(f"{k}={creds[k]}")
        lines.append("")

    if user_b_keys:
        lines.append("# --- User B (IDOR testing) ---")
        for k in user_b_keys:
            lines.append(f"{k}={creds[k]}")
        lines.append("")

    if other_keys:
        lines.append("# --- API Keys / Extras ---")
        for k in other_keys:
            lines.append(f"{k}={creds[k]}")
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))


def update_token(target: str, key: str, value: str):
    """Update a single token value in the .env file."""
    creds = load_credentials(target)
    creds[key] = value
    creds["_LAST_REFRESHED"] = datetime.now(timezone.utc).isoformat()
    save_credentials(target, creds)


def is_token_expired(target: str, user: str = "A") -> bool:
    """Check if a user's token has expired."""
    creds = load_credentials(target)
    expiry_key = f"USER_{user}_TOKEN_EXPIRY"
    expiry = creds.get(expiry_key)
    if not expiry:
        return True
    try:
        expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= expiry_dt
    except (ValueError, TypeError):
        return True


def get_curl_headers(target: str, user: str = "A") -> str:
    """Get curl-ready auth headers for a user."""
    creds = load_credentials(target)
    headers = []
    token = creds.get(f"USER_{user}_AUTH_TOKEN")
    if token:
        headers.append(f'-H "Authorization: {token}"')
    cookie = creds.get(f"USER_{user}_SESSION_COOKIE")
    if cookie:
        headers.append(f'-H "Cookie: {cookie}"')
    csrf = creds.get(f"USER_{user}_CSRF_TOKEN")
    if csrf:
        headers.append(f'-H "X-CSRF-Token: {csrf}"')
    return " ".join(headers)


def list_targets_with_creds() -> list[dict]:
    """List all targets that have saved credentials."""
    results = []
    if not FINDINGS_DIR.exists():
        return results

    for target_dir in FINDINGS_DIR.iterdir():
        if not target_dir.is_dir():
            continue
        creds_file = target_dir / "credentials" / f"{target_dir.name}-creds.env"
        if creds_file.exists():
            creds = load_credentials(target_dir.name)
            results.append({
                "target": target_dir.name,
                "has_user_a": bool(creds.get("USER_A_AUTH_TOKEN")),
                "has_user_b": bool(creds.get("USER_B_AUTH_TOKEN")),
                "user_a_expired": is_token_expired(target_dir.name, "A"),
                "user_b_expired": is_token_expired(target_dir.name, "B"),
                "path": str(creds_file),
            })
    return results


def mask_value(value: str, show_chars: int = 8) -> str:
    """Mask a sensitive value, showing only first and last few chars."""
    if len(value) <= show_chars * 2:
        return "*" * len(value)
    return value[:show_chars] + "..." + value[-4:]
