"""Playwright-based form filler and submitter.

Fills form fields, handles CAPTCHAs gracefully, and logs submissions."""
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright, Page

from .matcher import FieldMatch
from . import db
from .config import UPLOADS_DIR


async def _detect_captcha(page: Page) -> bool:
    """Check for common CAPTCHA indicators."""
    captcha_selectors = [
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[src*="turnstile"]',
        ".g-recaptcha",
        ".h-captcha",
        "#cf-turnstile",
    ]
    for sel in captcha_selectors:
        el = await page.query_selector(sel)
        if el:
            return True
    return False


async def _fill_form(
    url: str,
    matches: list[FieldMatch],
    auto_submit: bool = False,
    cv_path: Path | None = None,
) -> dict:
    """Fill a form using Playwright and optionally submit.

    Returns a status dict with results.
    """
    result = {"status": "draft", "filled": 0, "errors": [], "captcha": False}

    async with async_playwright() as p:
        # Launch visible browser so user can watch and intervene
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Fill each matched field
        for match in matches:
            if match.value is None:
                continue

            try:
                sel = match.selector
                if match.field_type in ("text", "email", "tel", "number", "url", "date", "password"):
                    await page.fill(sel, match.value, timeout=5000)
                    result["filled"] += 1

                elif match.field_type == "textarea":
                    await page.fill(sel, match.value, timeout=5000)
                    result["filled"] += 1

                elif match.field_type == "select":
                    try:
                        await page.select_option(sel, label=match.value, timeout=5000)
                    except Exception:
                        # Try by value
                        await page.select_option(sel, match.value, timeout=5000)
                    result["filled"] += 1

                elif match.field_type == "radio":
                    # Find the radio with matching value/label
                    radio = await page.query_selector(f'{sel}[value="{match.value}"]')
                    if radio:
                        await radio.check()
                        result["filled"] += 1

                elif match.field_type == "checkbox":
                    await page.check(sel, timeout=5000)
                    result["filled"] += 1

                elif match.field_type == "file":
                    # Upload CV if we have one
                    file_path = cv_path or _find_latest_cv()
                    if file_path and file_path.exists():
                        await page.set_input_files(sel, str(file_path), timeout=5000)
                        result["filled"] += 1

            except Exception as e:
                result["errors"].append({"field": match.label, "error": str(e)})

        # Check for CAPTCHA
        if await _detect_captcha(page):
            result["captcha"] = True
            result["status"] = "captcha_detected"
            # Keep browser open for user to solve
            # Wait up to 5 minutes for CAPTCHA to be solved
            await page.wait_for_timeout(300000)

        # Submit if requested
        if auto_submit and not result["captcha"]:
            submit_btn = await page.query_selector(
                'button[type="submit"], input[type="submit"], '
                'button:has-text("Submit"), button:has-text("Apply"), '
                'button:has-text("Send")'
            )
            if submit_btn:
                await submit_btn.click()
                await page.wait_for_timeout(3000)
                result["status"] = "submitted"
            else:
                result["status"] = "no_submit_button"
        elif not result["captcha"]:
            result["status"] = "filled"

        # Screenshot for records
        screenshot_path = UPLOADS_DIR / "last_submission.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        result["screenshot"] = str(screenshot_path)

        await browser.close()

    return result


def _find_latest_cv() -> Path | None:
    """Find the most recently uploaded CV."""
    pdfs = list(UPLOADS_DIR.glob("*.pdf"))
    if not pdfs:
        return None
    return max(pdfs, key=lambda p: p.stat().st_mtime)


def fill_and_submit(
    url: str,
    matches: list[FieldMatch],
    auto_submit: bool = False,
    cv_path: Path | None = None,
) -> dict:
    """Synchronous wrapper for form filling."""
    return asyncio.run(_fill_form(url, matches, auto_submit, cv_path))
