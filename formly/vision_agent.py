"""Vision-guided page understanding for the Formly agent.

Uses Anthropic Claude vision (claude-opus-4-5) to observe a page screenshot
and determine what state the form is in, what fields are visible, and what
action to take next.

Falls back to Groq text-only analysis when ANTHROPIC_API_KEY is not set.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import Page


# ─── Data model ─────────────────────────────────────────────────────────────

@dataclass
class PageObservation:
    state: str  # "filling" | "error" | "otp_required" | "captcha" | "login_required" | "success" | "unknown"
    page_summary: str
    has_next_button: bool
    next_button_text: Optional[str]
    visible_unfilled_fields: list[dict]
    validation_errors: list[str]
    is_otp_page: bool
    is_captcha_page: bool
    is_success_page: bool
    is_login_page: bool
    action: str  # "fill_visible_fields" | "click_next" | "click_submit" | "wait_otp" | "solve_captcha" | "login" | "done" | "ask_user"
    screenshot_b64: str  # the screenshot taken


# ─── Screenshot helpers ──────────────────────────────────────────────────────

async def take_screenshot_b64(page: Page) -> str:
    """Take a Playwright screenshot and return it as a base64 string."""
    png_bytes = await page.screenshot(full_page=False, type="png")
    return base64.b64encode(png_bytes).decode("utf-8")


# ─── System prompt for vision analysis ──────────────────────────────────────

_VISION_SYSTEM = """You are a web form analysis agent. Analyze the screenshot and return ONLY valid JSON with no markdown fences.

Return exactly this JSON structure:
{
  "state": "filling|error|otp_required|captcha|login_required|success|unknown",
  "page_summary": "one sentence describing the page",
  "has_next_button": true/false,
  "next_button_text": "text on the next button or null",
  "visible_unfilled_fields": [{"label": "...", "type": "text|select|checkbox|radio|textarea", "required": true/false}],
  "validation_errors": ["list of any validation error messages visible on page"],
  "is_otp_page": true/false,
  "is_captcha_page": true/false,
  "is_success_page": true/false,
  "is_login_page": true/false,
  "action": "fill_visible_fields|click_next|click_submit|wait_otp|solve_captcha|login|done|ask_user"
}

Rules:
- state=otp_required if you see fields asking for a verification code / OTP
- state=captcha if reCAPTCHA/hCaptcha/image challenge is visible
- state=login_required if there is a password field alongside an email field
- state=success if the page shows a confirmation/thank-you message
- action=click_next if there are unfilled fields and a Next/Continue button
- action=click_submit if the form looks complete and has a Submit/Apply button
- action=done if state=success
- action=wait_otp if state=otp_required
- Return ONLY valid JSON, no markdown, no extra text."""


# ─── Core observation function ───────────────────────────────────────────────

async def observe_screenshot(
    screenshot_b64: str,
    context: str = "",
    profile_hint: str = "",
) -> PageObservation:
    """Send screenshot to Claude vision and return a PageObservation.

    Falls back to Groq text analysis if ANTHROPIC_API_KEY is not set.
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    if anthropic_key:
        return await _observe_with_claude(screenshot_b64, context, profile_hint)
    else:
        return await _observe_with_groq_text(screenshot_b64, context, profile_hint)


async def _observe_with_claude(
    screenshot_b64: str,
    context: str,
    profile_hint: str,
) -> PageObservation:
    """Use Claude claude-opus-4-5 vision API."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install anthropic>=0.40.0"
        )

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    user_content = []
    user_content.append({
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": screenshot_b64,
        },
    })
    user_text = "Analyze this form screenshot."
    if context:
        user_text += f"\nPage context: {context[:300]}"
    if profile_hint:
        user_text += f"\nProfile hint: {profile_hint[:200]}"
    user_content.append({"type": "text", "text": user_text})

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        system=_VISION_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = response.content[0].text if response.content else "{}"
    return _parse_observation(raw, screenshot_b64)


async def _observe_with_groq_text(
    screenshot_b64: str,
    context: str,
    profile_hint: str,
) -> PageObservation:
    """Fallback: use Groq with a text description of the page state."""
    # We can't do vision with Groq, so we build a description from context
    from .groq_client import chat

    description = context or "Unknown page content"
    if profile_hint:
        description += f"\nProfile: {profile_hint[:200]}"

    system = """You are a web form analysis agent. Based on the page text description, return ONLY valid JSON with no markdown fences.

Return exactly this JSON structure:
{
  "state": "filling|error|otp_required|captcha|login_required|success|unknown",
  "page_summary": "one sentence describing the page",
  "has_next_button": true/false,
  "next_button_text": "text on the next/continue button or null",
  "visible_unfilled_fields": [],
  "validation_errors": [],
  "is_otp_page": true/false,
  "is_captcha_page": true/false,
  "is_success_page": true/false,
  "is_login_page": true/false,
  "action": "fill_visible_fields|click_next|click_submit|wait_otp|solve_captcha|login|done|ask_user"
}

Detect otp_required from keywords: "verification code", "check your email", "OTP", "one-time".
Detect success from: "thank you", "submitted", "application received", "confirmation".
Detect login from: presence of password field with email.
Return ONLY valid JSON."""

    user = f"Page description:\n{description[:2000]}"

    try:
        # Run sync groq chat in thread to avoid blocking
        raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: chat(system, user, max_tokens=512)
        )
    except Exception as exc:
        raw = "{}"

    return _parse_observation(raw, screenshot_b64)


def _parse_observation(raw: str, screenshot_b64: str) -> PageObservation:
    """Parse the JSON response from vision/text model into a PageObservation."""
    # Strip markdown fences
    clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean.strip())
    clean = clean.strip()

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        # Try to extract first {...} block
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    state = data.get("state", "unknown")
    action = data.get("action", "fill_visible_fields")

    # Sanity-check action against state
    if state == "success" and action not in ("done",):
        action = "done"
    if state == "otp_required":
        action = "wait_otp"
    if state == "captcha":
        action = "solve_captcha"
    if state == "login_required":
        action = "login"

    return PageObservation(
        state=state,
        page_summary=data.get("page_summary", ""),
        has_next_button=bool(data.get("has_next_button", False)),
        next_button_text=data.get("next_button_text"),
        visible_unfilled_fields=data.get("visible_unfilled_fields", []),
        validation_errors=data.get("validation_errors", []),
        is_otp_page=bool(data.get("is_otp_page", state == "otp_required")),
        is_captcha_page=bool(data.get("is_captcha_page", state == "captcha")),
        is_success_page=bool(data.get("is_success_page", state == "success")),
        is_login_page=bool(data.get("is_login_page", state == "login_required")),
        action=action,
        screenshot_b64=screenshot_b64,
    )


# ─── OTP detection and filling ───────────────────────────────────────────────

async def detect_otp_field(page: Page) -> bool:
    """Check if the current page has an OTP / verification code input."""
    try:
        # Check for autocomplete=one-time-code
        otp_input = await page.query_selector('input[autocomplete="one-time-code"]')
        if otp_input:
            return True

        # Check for short max-length inputs (4 or 6 digit codes)
        for maxlen in ("4", "6"):
            el = await page.query_selector(f'input[maxlength="{maxlen}"]')
            if el:
                return True

        # Check page text for OTP keywords
        text = await page.evaluate("() => document.body.innerText.toLowerCase()")
        otp_keywords = [
            "verification code", "check your email", "otp",
            "one-time", "enter the code", "we sent a code",
            "6-digit", "4-digit", "confirm your email",
        ]
        for kw in otp_keywords:
            if kw in text:
                return True

        return False
    except Exception:
        return False


async def fill_otp(page: Page, code: str) -> bool:
    """Fill an OTP code into the page.

    Handles both:
    - Single input: types the full code
    - Multi-box: one digit per input (common in React OTP components)
    """
    try:
        code_digits = [c for c in code if c.isdigit()]

        # Try multi-box pattern first: multiple short inputs
        digit_inputs = await page.query_selector_all(
            'input[maxlength="1"], input[type="tel"][maxlength="1"]'
        )
        if len(digit_inputs) >= len(code_digits) and len(digit_inputs) <= 8:
            for i, digit in enumerate(code_digits):
                if i < len(digit_inputs):
                    await digit_inputs[i].click()
                    await digit_inputs[i].fill(digit)
                    await asyncio.sleep(0.05)
            return True

        # Single input with autocomplete=one-time-code
        single = await page.query_selector('input[autocomplete="one-time-code"]')
        if single:
            await single.click()
            await single.fill(code)
            return True

        # Single input with max-length matching code length
        for maxlen in (str(len(code)), "4", "6", "8"):
            el = await page.query_selector(f'input[maxlength="{maxlen}"]')
            if el:
                await el.click()
                await el.fill(code)
                return True

        # Last resort: find any visible single-line text input that looks like OTP
        inputs = await page.query_selector_all('input[type="text"], input[type="number"], input:not([type])')
        for inp in inputs:
            is_visible = await inp.is_visible()
            if not is_visible:
                continue
            maxlen_attr = await inp.get_attribute("maxlength")
            if maxlen_attr and maxlen_attr in ("4", "6", "8"):
                await inp.click()
                await inp.fill(code)
                return True

        return False
    except Exception:
        return False


# ─── Login detection and filling ────────────────────────────────────────────

async def detect_login_form(page: Page) -> bool:
    """Detect if the page has a login form (email + password inputs together)."""
    try:
        password_input = await page.query_selector('input[type="password"]')
        if not password_input:
            return False
        # Also needs an email or text input
        email_input = await page.query_selector(
            'input[type="email"], input[name*="email"], input[id*="email"], '
            'input[name*="username"], input[type="text"]'
        )
        return email_input is not None
    except Exception:
        return False


async def fill_login(page: Page, email: str, password: str) -> bool:
    """Fill a login form with email and password, then submit."""
    try:
        # Find email/username field
        email_sel = await page.query_selector(
            'input[type="email"], input[name*="email"], input[id*="email"], '
            'input[name*="username"], input[id*="username"]'
        )
        if not email_sel:
            email_sel = await page.query_selector('input[type="text"]')
        if email_sel:
            await email_sel.click()
            await email_sel.fill(email)
            await asyncio.sleep(0.2)

        # Find password field
        pass_sel = await page.query_selector('input[type="password"]')
        if pass_sel:
            await pass_sel.click()
            await pass_sel.fill(password)
            await asyncio.sleep(0.2)

        # Submit — try button first, then Enter
        submit_btn = await page.query_selector(
            'button[type="submit"], input[type="submit"], '
            'button:has-text("Sign in"), button:has-text("Log in"), '
            'button:has-text("Login"), button:has-text("Continue")'
        )
        if submit_btn:
            await submit_btn.click()
        elif pass_sel:
            await pass_sel.press("Enter")

        await asyncio.sleep(1.5)
        return True
    except Exception:
        return False


# ─── Navigation helpers ──────────────────────────────────────────────────────

# Words that mean "go to next step" — safe to click
_NEXT_WORDS = {
    "next", "continue", "proceed", "forward", "go", "advance",
    "next step", "continue to", "next page", "save and continue",
    "save & continue", "save and next",
}

# Words that should NEVER be clicked by click_next_button
_DANGER_WORDS = {
    "submit", "apply", "pay", "register", "sign up", "signup",
    "create account", "finish", "complete", "send", "confirm",
}


async def click_next_button(page: Page, button_text: Optional[str] = None) -> bool:
    """Find and click a Next/Continue button. Never clicks Submit/Apply/Pay/Register."""
    try:
        # If caller specified exact text, try that first
        if button_text:
            btn_lower = button_text.lower().strip()
            if any(danger in btn_lower for danger in _DANGER_WORDS):
                return False  # Refuse to click dangerous buttons
            try:
                loc = page.get_by_role("button", name=re.compile(re.escape(button_text), re.IGNORECASE))
                count = await loc.count()
                if count > 0:
                    await loc.first.click()
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                pass

        # Try each safe next-word
        for word in ("Next", "Continue", "Proceed", "Next Step", "Save and Continue", "Save & Continue"):
            try:
                loc = page.get_by_role("button", name=re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE))
                count = await loc.count()
                if count > 0:
                    btn_text = await loc.first.inner_text()
                    if any(danger in btn_text.lower() for danger in _DANGER_WORDS):
                        continue
                    await loc.first.click()
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                continue

        # Fallback: query all buttons and find one with safe text
        buttons = await page.query_selector_all("button, input[type='button'], a[role='button']")
        for btn in buttons:
            try:
                is_visible = await btn.is_visible()
                if not is_visible:
                    continue
                text = (await btn.inner_text()).lower().strip()
                if not text:
                    val = await btn.get_attribute("value") or ""
                    text = val.lower().strip()
                if any(danger in text for danger in _DANGER_WORDS):
                    continue
                if any(safe in text for safe in _NEXT_WORDS):
                    await btn.click()
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                continue

        return False
    except Exception:
        return False


async def click_submit_button(page: Page) -> bool:
    """Find and click the final Submit/Apply/Send/Finish button."""
    submit_patterns = [
        "Submit", "Apply", "Send", "Finish", "Complete",
        "Submit Application", "Apply Now", "Send Application",
    ]
    try:
        for pattern in submit_patterns:
            try:
                loc = page.get_by_role("button", name=re.compile(rf"\b{re.escape(pattern)}\b", re.IGNORECASE))
                count = await loc.count()
                if count > 0:
                    await loc.first.click()
                    await asyncio.sleep(1.5)
                    return True
            except Exception:
                continue

        # Also try input[type=submit]
        submit_input = await page.query_selector('input[type="submit"]')
        if submit_input and await submit_input.is_visible():
            await submit_input.click()
            await asyncio.sleep(1.5)
            return True

        return False
    except Exception:
        return False
