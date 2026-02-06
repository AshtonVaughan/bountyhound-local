"""Browser automation service using Playwright."""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime


FINDINGS_DIR = Path.home() / "bounty-findings"


class BrowserService:
    """Wraps Playwright for headless browser automation in hunting tasks."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def start(self):
        from playwright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self._page = await self._context.new_page()

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def navigate(self, url: str, timeout: int = 30000) -> dict:
        try:
            response = await self._page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            return {
                "url": self._page.url,
                "status": response.status if response else None,
                "ok": response.ok if response else False,
            }
        except Exception as e:
            return {"url": url, "status": None, "ok": False, "error": str(e)}

    async def snapshot(self) -> str:
        return await self._page.content()

    async def screenshot(self, target: str, name: str) -> str:
        ss_dir = FINDINGS_DIR / target / "screenshots"
        ss_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = ss_dir / f"{name}_{ts}.png"
        await self._page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def get_page_text(self) -> str:
        return await self._page.inner_text("body")

    async def find_forms(self) -> list[dict]:
        forms = await self._page.evaluate("""() => {
            return Array.from(document.querySelectorAll('form')).map(f => ({
                action: f.action,
                method: f.method,
                id: f.id,
                inputs: Array.from(f.querySelectorAll('input, textarea, select')).map(i => ({
                    name: i.name,
                    type: i.type,
                    id: i.id,
                    value: i.value,
                    placeholder: i.placeholder
                }))
            }))
        }""")
        return forms

    async def find_links(self) -> list[dict]:
        links = await self._page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: a.innerText.trim().substring(0, 100)
            })).filter(l => l.href.startsWith('http'))
        }""")
        return links

    async def extract_tokens(self) -> dict:
        """Extract all auth tokens from cookies, localStorage, sessionStorage."""
        cookies = await self._context.cookies()
        local_storage = await self._page.evaluate("""() => {
            const items = {};
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                items[key] = localStorage.getItem(key);
            }
            return items;
        }""")
        session_storage = await self._page.evaluate("""() => {
            const items = {};
            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                items[key] = sessionStorage.getItem(key);
            }
            return items;
        }""")
        return {
            "cookies": cookies,
            "local_storage": local_storage,
            "session_storage": session_storage,
        }

    async def test_xss(self, url: str, param: str, payloads: list[str]) -> list[dict]:
        """Test XSS payloads against a URL parameter."""
        results = []
        for payload in payloads:
            test_url = f"{url}?{param}={payload}" if "?" not in url else f"{url}&{param}={payload}"
            await self._page.goto(test_url, wait_until="domcontentloaded", timeout=10000)
            title = await self._page.title()
            content = await self._page.content()

            reflected = payload in content
            xss_fired = title == "XSS-FIRED"

            results.append({
                "payload": payload,
                "url": test_url,
                "reflected": reflected,
                "executed": xss_fired,
                "status": "VULNERABLE" if xss_fired else ("REFLECTED" if reflected else "SAFE"),
            })

            if xss_fired:
                break

        return results

    async def fill_and_submit_form(self, form_data: dict) -> dict:
        """Fill form fields and submit."""
        for selector, value in form_data.items():
            try:
                await self._page.fill(selector, value)
            except Exception:
                try:
                    await self._page.type(selector, value)
                except Exception as e:
                    return {"error": f"Failed to fill {selector}: {e}"}

        try:
            await self._page.click('button[type="submit"], input[type="submit"]')
            await self._page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            await self._page.keyboard.press("Enter")
            await self._page.wait_for_load_state("domcontentloaded", timeout=10000)

        return {
            "url": self._page.url,
            "title": await self._page.title(),
        }

    async def intercept_api_calls(self, duration_seconds: int = 5) -> list[dict]:
        """Capture API calls made by the page."""
        requests = []

        async def on_request(request):
            if request.resource_type in ("xhr", "fetch"):
                requests.append({
                    "url": request.url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data,
                })

        self._page.on("request", on_request)
        await asyncio.sleep(duration_seconds)
        self._page.remove_listener("request", on_request)
        return requests


def run_curl(command: str, timeout: int = 30) -> dict:
    """Run a curl command and return the result."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "TIMEOUT", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}


def run_bountyhound(subcommand: str, *args, timeout: int = 600) -> dict:
    """Run a bountyhound CLI command."""
    cmd = f"bountyhound {subcommand} " + " ".join(str(a) for a in args)
    return run_curl(cmd, timeout=timeout)
