"""Auth worker - account creation, token extraction, credential management."""

import json
import time
import asyncio
from datetime import datetime, timezone

from src.workers.celery_app import app
from src.models.vllm_client import get_llm
from src.database.models import WorkerLogDB
from src.database.redis_manager import TaskQueue
from src.services.credential_manager import (
    load_credentials, save_credentials, is_token_expired,
    get_creds_path, list_targets_with_creds, update_token,
)
from src.services.browser import BrowserService, run_curl


@app.task(name="src.workers.auth.setup_auth", bind=True, queue="auth")
def setup_auth(self, target_id: int, hunt_id: int, domain: str, signup_url: str = ""):
    """Create test accounts and extract auth tokens for a target."""
    start = time.time()
    TaskQueue.set_worker_status(f"auth-{self.request.id}", {
        "task": "auth", "target": domain, "status": "running"
    })

    llm = get_llm()

    # Check if credentials already exist and are valid
    existing = load_credentials(domain)
    if existing.get("USER_A_AUTH_TOKEN") and not is_token_expired(domain, "A"):
        return {"status": "existing_valid", "message": "Existing credentials are still valid"}

    # Ask LLM to plan the auth approach
    plan_prompt = f"""Plan how to create test accounts on {domain}.

Signup URL (if known): {signup_url or 'unknown - need to discover'}

Determine:
1. What signup method is likely? (email/password, OAuth, SSO, invite-only)
2. What common signup paths to try: /signup, /register, /join, /api/auth/register
3. What fields are typically required?
4. Any common obstacles (CAPTCHA, email verification)?

Return JSON:
{{
  "auth_type": "email_password|oauth|sso|api|invite_only",
  "signup_paths": ["/signup", "/register"],
  "required_fields": ["email", "password", "username"],
  "obstacles": ["captcha", "email_verification"],
  "api_endpoint": "direct API registration endpoint if likely"
}}"""

    plan = llm.chat_json("auth", [{"role": "user", "content": plan_prompt}],
                         temperature=0.3, max_tokens=1024)

    # Generate test identities
    import random
    import string
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

    user_a = {
        "USER_A_EMAIL": f"bh.test.{rand}@gmail.com",
        "USER_A_PASSWORD": f"BhTest!{rand}#Secure",
        "USER_A_USERNAME": f"bhtest_{rand}",
    }
    user_b = {
        "USER_B_EMAIL": f"bh.test2.{rand}@gmail.com",
        "USER_B_PASSWORD": f"BhTest2!{rand}#Secure",
        "USER_B_USERNAME": f"bhtest2_{rand}",
    }

    # Try API-based registration first
    api_endpoint = plan.get("api_endpoint", "")
    if api_endpoint:
        for prefix, user in [("USER_A", user_a), ("USER_B", user_b)]:
            reg_result = run_curl(
                f'curl -s -X POST -H "Content-Type: application/json" '
                f'-d \'{{"email":"{user[f"{prefix}_EMAIL"]}","password":"{user[f"{prefix}_PASSWORD"]}"}}\' '
                f'"https://{domain}{api_endpoint}"',
                timeout=15
            )
            if reg_result["returncode"] == 0 and "error" not in reg_result["stdout"].lower():
                try:
                    resp = json.loads(reg_result["stdout"])
                    token = resp.get("token") or resp.get("access_token") or resp.get("accessToken")
                    if token:
                        user[f"{prefix}_AUTH_TOKEN"] = f"Bearer {token}" if not token.startswith("Bearer") else token
                except (json.JSONDecodeError, KeyError):
                    pass

    # Save whatever we have
    all_creds = {**user_a, **user_b}
    all_creds["GRAPHQL_ENDPOINT"] = f"https://{domain}/graphql"
    save_credentials(domain, all_creds)

    # Verify tokens work
    verification = {}
    for prefix in ["USER_A", "USER_B"]:
        token = all_creds.get(f"{prefix}_AUTH_TOKEN", "")
        if token:
            verify_result = run_curl(
                f'curl -s -o /dev/null -w "%{{http_code}}" '
                f'-H "Authorization: {token}" '
                f'"https://{domain}/api/me"',
                timeout=10
            )
            verification[prefix] = verify_result["stdout"].strip()

    duration = time.time() - start
    WorkerLogDB.log("auth", "qwen-14b", "setup_auth", hunt_id=hunt_id,
                    output_summary=f"Auth setup for {domain}: {json.dumps(verification)}",
                    duration_seconds=duration)

    TaskQueue.set_worker_status(f"auth-{self.request.id}", {"status": "complete"})
    return {
        "status": "complete",
        "credentials_path": str(get_creds_path(domain)),
        "verification": verification,
        "auth_type": plan.get("auth_type", "unknown"),
        "duration": duration,
    }


@app.task(name="src.workers.auth.refresh_tokens", bind=True, queue="auth")
def refresh_tokens(self, domain: str):
    """Refresh expired tokens for a target."""
    creds = load_credentials(domain)
    refresh_token = creds.get("USER_A_REFRESH_TOKEN")

    if refresh_token:
        result = run_curl(
            f'curl -s -X POST -H "Content-Type: application/json" '
            f'-d \'{{"refreshToken":"{refresh_token}"}}\' '
            f'"https://{domain}/api/auth/refresh"',
            timeout=15
        )
        try:
            resp = json.loads(result["stdout"])
            new_token = resp.get("token") or resp.get("access_token") or resp.get("accessToken")
            if new_token:
                update_token(domain, "USER_A_AUTH_TOKEN",
                             f"Bearer {new_token}" if not new_token.startswith("Bearer") else new_token)
                return {"status": "refreshed", "method": "api"}
        except (json.JSONDecodeError, KeyError):
            pass

    return {"status": "needs_browser_reauth", "domain": domain}


@app.task(name="src.workers.auth.check_all_token_expiry")
def check_all_token_expiry():
    """Periodic task: check all targets for expired tokens."""
    targets = list_targets_with_creds()
    expired = []
    for t in targets:
        if t["user_a_expired"] or t["user_b_expired"]:
            expired.append(t["target"])
            refresh_tokens.delay(t["target"])

    return {"checked": len(targets), "expired": len(expired), "refreshing": expired}
