"""Scope parser - validates targets against bug bounty program scope."""

import re
import fnmatch
from urllib.parse import urlparse


def parse_scope(scope_config: dict) -> dict:
    """Parse scope configuration into normalized in/out scope lists."""
    in_scope = [s.strip().lower() for s in scope_config.get("in_scope", [])]
    out_of_scope = [s.strip().lower() for s in scope_config.get("out_of_scope", [])]
    return {"in_scope": in_scope, "out_of_scope": out_of_scope}


def is_in_scope(url_or_domain: str, scope: dict) -> bool:
    """Check if a URL or domain is within the defined scope."""
    domain = _extract_domain(url_or_domain).lower()

    for pattern in scope.get("out_of_scope", []):
        if _matches_pattern(domain, pattern):
            return False

    for pattern in scope.get("in_scope", []):
        if _matches_pattern(domain, pattern):
            return True

    return False


def _extract_domain(url_or_domain: str) -> str:
    if "://" in url_or_domain:
        return urlparse(url_or_domain).hostname or url_or_domain
    return url_or_domain.split("/")[0].split(":")[0]


def _matches_pattern(domain: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if pattern.startswith("*."):
        base = pattern[2:]
        return domain == base or domain.endswith("." + base)
    return fnmatch.fnmatch(domain, pattern)


def validate_url_in_scope(url: str, scope: dict) -> dict:
    """Validate a URL and return detailed scope check result."""
    domain = _extract_domain(url)
    in_scope = is_in_scope(url, scope)

    result = {
        "url": url,
        "domain": domain,
        "in_scope": in_scope,
        "matched_rule": None,
    }

    for pattern in scope.get("out_of_scope", []):
        if _matches_pattern(domain, pattern):
            result["matched_rule"] = f"OUT: {pattern}"
            return result

    for pattern in scope.get("in_scope", []):
        if _matches_pattern(domain, pattern):
            result["matched_rule"] = f"IN: {pattern}"
            return result

    result["matched_rule"] = "NO MATCH (default: out of scope)"
    return result


COMMON_EXCLUSIONS = [
    "DoS/DDoS attacks",
    "Social engineering / phishing",
    "Physical attacks",
    "Third-party services (analytics, CDNs)",
    "Recently patched vulnerabilities",
    "Self-XSS without chaining",
    "Missing security headers without impact",
    "Username/email enumeration",
    "Rate limiting issues without impact",
    "Stack traces / verbose error messages",
    "SPF/DKIM/DMARC misconfiguration",
    "Clickjacking on non-sensitive pages",
    "CSRF on login/logout",
    "Content injection without XSS",
]


def check_finding_eligibility(finding_type: str, platform: str = "hackerone") -> dict:
    """Check if a finding type is generally eligible for bounty."""
    ineligible_types = {
        "hackerone": [
            "missing_headers", "error_messages", "stack_traces",
            "username_enumeration", "email_enumeration", "rate_limiting",
            "clickjacking_no_state_change", "csrf_login", "csrf_logout",
            "self_xss", "content_injection_no_xss", "open_ports",
            "banner_grabbing", "dns_misconfiguration",
        ],
        "bugcrowd": [
            "missing_headers", "error_messages", "stack_traces",
            "username_enumeration", "rate_limiting", "self_xss",
            "clickjacking_no_state_change", "dns_misconfiguration",
        ],
        "intigriti": [
            "missing_headers", "error_messages", "stack_traces",
            "username_enumeration", "rate_limiting", "self_xss",
        ],
    }

    platform_exclusions = ineligible_types.get(platform, [])
    eligible = finding_type.lower() not in platform_exclusions

    return {
        "finding_type": finding_type,
        "platform": platform,
        "eligible": eligible,
        "reason": f"'{finding_type}' is typically excluded by {platform}" if not eligible else "Eligible",
    }
