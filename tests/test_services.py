"""Tests for service layer."""

import os
import tempfile
import pytest
from pathlib import Path

from src.services.scope_parser import is_in_scope, parse_scope, check_finding_eligibility
from src.services.credential_manager import save_credentials, load_credentials, mask_value, is_token_expired


def test_scope_wildcard():
    scope = {"in_scope": ["*.example.com"], "out_of_scope": []}
    assert is_in_scope("api.example.com", scope) is True
    assert is_in_scope("sub.api.example.com", scope) is True
    assert is_in_scope("example.com", scope) is True
    assert is_in_scope("other.com", scope) is False


def test_scope_exclusion():
    scope = {
        "in_scope": ["*.example.com"],
        "out_of_scope": ["staging.example.com"],
    }
    assert is_in_scope("api.example.com", scope) is True
    assert is_in_scope("staging.example.com", scope) is False


def test_scope_url():
    scope = {"in_scope": ["*.example.com"], "out_of_scope": []}
    assert is_in_scope("https://api.example.com/v1/users", scope) is True
    assert is_in_scope("https://other.com/api", scope) is False


def test_finding_eligibility():
    result = check_finding_eligibility("sqli", "hackerone")
    assert result["eligible"] is True

    result = check_finding_eligibility("missing_headers", "hackerone")
    assert result["eligible"] is False


def test_credentials_save_load(tmp_path):
    os.environ["HOME"] = str(tmp_path)

    from src.services import credential_manager
    credential_manager.FINDINGS_DIR = tmp_path / "bounty-findings"

    creds = {
        "USER_A_EMAIL": "test@example.com",
        "USER_A_PASSWORD": "secret123",
        "USER_A_AUTH_TOKEN": "Bearer eyJhbGciOiJIUzI1NiJ9.test",
        "USER_B_EMAIL": "test2@example.com",
    }

    save_credentials("target.com", creds)
    loaded = load_credentials("target.com")

    assert loaded["USER_A_EMAIL"] == "test@example.com"
    assert loaded["USER_A_AUTH_TOKEN"] == "Bearer eyJhbGciOiJIUzI1NiJ9.test"
    assert loaded["USER_B_EMAIL"] == "test2@example.com"


def test_mask_value():
    assert mask_value("short") == "*****"
    assert mask_value("a_very_long_secret_token_value").startswith("a_very_l")
    assert mask_value("a_very_long_secret_token_value").endswith("...alue")
