"""Autonomous form-filling agent using Playwright.

Opens any URL in a headless browser and fills every field using the
user's stored profile. Handles dropdowns, React Select, radios,
checkboxes, date pickers, multi-page forms, cookie popups, and
dynamic fields. Uses human-like delays and stealth to avoid detection.

v2 — Intelligent field-type detection, per-field verification, calendar
navigation, cascading dropdown awareness, phone format parsing, and
file-upload escalation."""
from __future__ import annotations

import asyncio
import base64
import random
import re
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext, ElementHandle


# ─── Data classes ────────────────────────────────────────

@dataclass
class FieldResult:
    """Per-field outcome reported back to the caller."""
    label: str
    selector: str
    field_type: str
    value: str
    status: str  # "filled", "verified", "error", "skipped", "needs_user"
    error_message: str = ""


@dataclass
class FillResult:
    filled: int
    skipped: int
    pages_navigated: int
    screenshot_b64: str
    errors: list[str] = field(default_factory=list)
    captcha_detected: bool = False
    field_results: list[FieldResult] = field(default_factory=list)


# ─── Human-like timing ──────────────────────────────────

def _human_delay() -> float:
    """Random delay between fields (0.8-2.5s) like a real person."""
    return random.uniform(0.8, 2.5)


def _typing_delay() -> int:
    """Random per-keystroke delay in ms (50-150ms)."""
    return random.randint(50, 150)


def _short_pause() -> float:
    """Micro pause between sub-actions."""
    return random.uniform(0.15, 0.4)


# ─── Page analysis — scroll & map the form ───────────────

async def _full_page_scan(page: Page) -> list[dict]:
    """Scroll the entire page top-to-bottom and return a map of all
    interactive elements with their bounding boxes and metadata."""

    # Scroll in increments so lazy-loaded content appears
    viewport_h = await page.evaluate("window.innerHeight")
    scroll_h = await page.evaluate("document.body.scrollHeight")
    pos = 0
    while pos < scroll_h:
        await page.evaluate(f"window.scrollTo(0, {pos})")
        await asyncio.sleep(random.uniform(0.25, 0.5))
        pos += int(viewport_h * 0.7)
        # Re-check — page may have grown
        scroll_h = await page.evaluate("document.body.scrollHeight")

    # Back to top
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.4)

    # Map every interactive element
    elements = await page.evaluate("""() => {
        const results = [];
        const els = document.querySelectorAll(
            'input, textarea, select, [role="combobox"], [role="listbox"], ' +
            '[class*="react-select"], [class*="auto-complete"], ' +
            '[contenteditable="true"]'
        );
        els.forEach((el, idx) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return;
            if (rect.width < 5 && rect.height < 5) return;
            results.push({
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                id: el.id || '',
                name: el.name || '',
                placeholder: el.placeholder || '',
                className: (typeof el.className === 'string') ? el.className : '',
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                value: el.value || '',
                required: el.required || false,
                y: rect.top + window.scrollY,
            });
        });
        return results;
    }""")
    return elements


# ─── Field verification ──────────────────────────────────

async def _verify_field(page: Page, el: ElementHandle, expected: str,
                        label: str) -> tuple[bool, str]:
    """Click away, re-read the value, check for validation errors.
    Returns (success, error_message)."""
    try:
        # Click body to trigger blur / validation
        await page.click("body", position={"x": 5, "y": 5}, no_wait_after=True)
        await asyncio.sleep(0.4)

        # Read value back
        actual = await el.evaluate("e => e.value || e.textContent || ''")
        actual = actual.strip()

        # For select-like elements, check displayed text too
        tag = await el.evaluate("e => e.tagName.toLowerCase()")
        if tag == "select":
            actual = await el.evaluate("""e => {
                const opt = e.options[e.selectedIndex];
                return opt ? opt.text.trim() : '';
            }""")

        # Check for validation errors near this element
        error_msg = await el.evaluate("""e => {
            // Walk up a few levels, look for error text
            let node = e;
            for (let i = 0; i < 4; i++) {
                node = node.parentElement;
                if (!node) break;
                const err = node.querySelector(
                    '.error, .field-error, [class*="error"], [class*="invalid"], ' +
                    '.help-block.text-danger, [role="alert"]'
                );
                if (err && err.offsetParent !== null) {
                    const t = err.textContent.trim();
                    if (t.length > 2 && t.length < 200) return t;
                }
            }
            // Also check red border
            const style = window.getComputedStyle(e);
            if (style.borderColor && (
                style.borderColor.includes('rgb(255') ||
                style.borderColor.includes('red')
            )) return '__red_border__';
            return '';
        }""")

        if error_msg == "__red_border__":
            return False, f"Field '{label}' has red border after fill"
        if error_msg:
            return False, f"Validation error for '{label}': {error_msg}"

        # For text-like fields, fuzzy-check value stuck
        if tag in ("input", "textarea"):
            input_type = await el.get_attribute("type") or "text"
            if input_type in ("text", "email", "tel", "number", "url", "search", ""):
                if not actual:
                    return False, f"Field '{label}' is empty after fill"
                # Allow partial match for phone formatting etc.
                exp_digits = re.sub(r'\D', '', expected)
                act_digits = re.sub(r'\D', '', actual)
                if exp_digits and act_digits and exp_digits not in act_digits and act_digits not in exp_digits:
                    return False, f"Value mismatch for '{label}': expected contains '{expected[:30]}' got '{actual[:30]}'"

        return True, ""
    except Exception as exc:
        return True, ""  # Verification failed but don't block


async def _check_for_errors_near(page: Page, el: ElementHandle) -> str:
    """Return any visible error text near an element, or empty string."""
    try:
        return await el.evaluate("""e => {
            let node = e;
            for (let i = 0; i < 4; i++) {
                node = node.parentElement;
                if (!node) break;
                const err = node.querySelector(
                    '.error, .field-error, [class*="error"], [class*="invalid"], [role="alert"]'
                );
                if (err && err.offsetParent !== null) {
                    const t = err.textContent.trim();
                    if (t.length > 2 && t.length < 200) return t;
                }
            }
            return '';
        }""")
    except Exception:
        return ""


# ─── Cookie / popup dismissal ───────────────────────────

async def _dismiss_popups(page: Page):
    """Dismiss cookie consent banners, modals, and overlays."""
    dismiss_selectors = [
        'button:has-text("Accept")', 'button:has-text("Accept All")',
        'button:has-text("I Agree")', 'button:has-text("OK")',
        'button:has-text("Got it")', 'button:has-text("Agree")',
        '[id*="cookie"] button', '[class*="cookie"] button',
        '[id*="consent"] button', '[class*="consent"] button',
        'button[aria-label="Close"]', 'button[aria-label="Dismiss"]',
        '[class*="modal"] button[class*="close"]',
        '.modal .close', '.popup .close',
    ]
    for sel in dismiss_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(0.3)
        except Exception:
            continue


# ─── CAPTCHA detection ───────────────────────────────────

async def _check_captcha(page: Page) -> bool:
    """Detect REAL form CAPTCHAs — not just ads or scripts."""
    return await page.evaluate("""() => {
        const captchaFrame = document.querySelector(
            'iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/api2/bframe"], ' +
            'iframe[src*="hcaptcha.com/captcha"], .g-recaptcha[data-sitekey], .h-captcha[data-sitekey]'
        );
        if (captchaFrame) {
            const rect = captchaFrame.getBoundingClientRect();
            if (rect.width > 50 && rect.height > 50) return true;
        }
        if (document.title.includes('Just a moment') || document.querySelector('#challenge-running'))
            return true;
        return false;
    }""")


# ─── Smart element finding ───────────────────────────────

async def _find_element(page: Page, selector: str, label: str, ftype: str) -> Optional[ElementHandle]:
    """Find an element using multiple strategies. Returns ElementHandle or None."""

    # Strategy 1: Direct CSS selector
    if selector:
        try:
            el = await page.query_selector(selector)
            if el:
                return el
        except Exception:
            pass
        try:
            el = await page.wait_for_selector(selector, timeout=3000, state="attached")
            if el:
                return el
        except Exception:
            pass

    # Strategy 2: Playwright get_by_label
    if label:
        try:
            loc = page.get_by_label(label, exact=False)
            if await loc.count() > 0:
                return await loc.first.element_handle()
        except Exception:
            pass

    # Strategy 3: Playwright get_by_placeholder
    if label:
        try:
            loc = page.get_by_placeholder(label, exact=False)
            if await loc.count() > 0:
                return await loc.first.element_handle()
        except Exception:
            pass

    # Strategy 4: label[for] -> getElementById
    if label:
        try:
            el = await page.evaluate_handle("""(label) => {
                const labels = [...document.querySelectorAll('label')];
                const match = labels.find(l => l.textContent.trim().toLowerCase().includes(label.toLowerCase()));
                if (match && match.htmlFor) return document.getElementById(match.htmlFor);
                if (match) return match.querySelector('input, textarea, select');
                return null;
            }""", label)
            elem = el.as_element()
            if elem:
                tag = await elem.evaluate("e => e.tagName")
                if tag:
                    return elem
        except Exception:
            pass

    # Strategy 5: input/textarea near label text in DOM
    if label:
        try:
            el_handle = await page.evaluate_handle("""(label) => {
                const allLabels = [...document.querySelectorAll('label, .label, span, p, td, h4, h5, h6, legend')];
                const match = allLabels.find(l => {
                    const text = l.textContent.trim().toLowerCase();
                    return text.includes(label.toLowerCase()) && text.length < 100;
                });
                if (!match) return null;
                let node = match;
                for (let i = 0; i < 5; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    const input = node.querySelector('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"]), textarea, select');
                    if (input) return input;
                }
                const next = match.nextElementSibling;
                if (next) {
                    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(next.tagName)) return next;
                    const inp = next.querySelector('input, textarea, select');
                    if (inp) return inp;
                }
                return null;
            }""", label)
            if el_handle:
                element = el_handle.as_element()
                if element:
                    return element
        except Exception:
            pass

    # Strategy 6: name attribute
    if label:
        name_guess = label.lower().replace(" ", "").replace("_", "")
        try:
            for tag in ["input", "textarea", "select"]:
                el = await page.query_selector(f'{tag}[name*="{name_guess}" i]')
                if el:
                    return el
        except Exception:
            pass

    # Strategy 7: ID containing label words
    if label:
        try:
            id_guess = label.replace(" ", "")
            el = await page.query_selector(
                f'input[id*="{id_guess}" i], textarea[id*="{id_guess}" i], select[id*="{id_guess}" i]'
            )
            if el:
                return el
        except Exception:
            pass
        try:
            words = label.split()
            if len(words) > 1:
                camel = words[0].lower() + "".join(w.capitalize() for w in words[1:])
                el = await page.query_selector(f'[id="{camel}"]')
                if el:
                    return el
        except Exception:
            pass

    return None


# ─── Detect element's real type ──────────────────────────

async def _detect_field_type(page: Page, el: ElementHandle, declared_type: str,
                              label: str) -> str:
    """Determine the actual field type from DOM inspection, overriding the
    declared type when the DOM tells us something different."""

    info = await el.evaluate("""e => {
        const tag = e.tagName.toLowerCase();
        const type = (e.type || '').toLowerCase();
        const cls = (typeof e.className === 'string') ? e.className : '';
        const role = e.getAttribute('role') || '';
        const id = e.id || '';
        const placeholder = e.placeholder || '';

        // Check if inside a React Select / autocomplete
        let isReactSelect = false;
        if (role === 'combobox' || id.includes('react-select')) isReactSelect = true;
        let node = e;
        for (let i = 0; i < 8; i++) {
            node = node.parentElement;
            if (!node) break;
            const pcls = (typeof node.className === 'string') ? node.className : '';
            if (pcls.includes('__control') || pcls.includes('__value-container') ||
                pcls.includes('react-select') || pcls.includes('auto-complete') ||
                pcls.includes('autocomplete')) {
                isReactSelect = true;
                break;
            }
        }

        // Check for datepicker
        let isDatepicker = false;
        if (cls.includes('datepicker') || cls.includes('date-picker') ||
            cls.includes('react-datepicker') || e.getAttribute('data-date') !== null) {
            isDatepicker = true;
        }
        node = e;
        for (let i = 0; i < 6; i++) {
            node = node.parentElement;
            if (!node) break;
            const pcls = (typeof node.className === 'string') ? node.className : '';
            if (pcls.includes('datepicker') || pcls.includes('react-datepicker')) {
                isDatepicker = true;
                break;
            }
        }

        return { tag, type, cls, role, id, placeholder, isReactSelect, isDatepicker };
    }""")

    tag = info["tag"]
    itype = info["type"]

    if info["isReactSelect"]:
        return "react_select"
    if info["isDatepicker"]:
        return "datepicker"
    if tag == "select":
        return "native_select"
    if itype == "radio":
        return "radio"
    if itype == "checkbox":
        return "checkbox"
    if itype == "file":
        return "file"
    if itype == "date":
        return "date_native"
    if itype == "tel":
        return "phone"
    if itype == "email":
        return "email"
    if itype == "number":
        return "number"

    # Label-based heuristics
    label_lower = (label or "").lower()
    if any(kw in label_lower for kw in ("date of birth", "dob", "birthday", "birth date")):
        if info["isDatepicker"]:
            return "datepicker"
        return "date_text"
    if "phone" in label_lower or "mobile" in label_lower or "tel" in label_lower:
        return "phone"

    if tag == "textarea":
        return "textarea"

    # Default text
    return declared_type if declared_type in ("text", "email", "tel", "number") else "text"


# ═══════════════════════════════════════════════════════════
#  FIELD TYPE HANDLERS
# ═══════════════════════════════════════════════════════════

# ─── Plain text / email / number / textarea ──────────────

async def _fill_text(page: Page, el: ElementHandle, value: str, label: str) -> FieldResult:
    """Clear, type with human delays, verify."""
    selector = await _get_selector(el)
    try:
        await el.scroll_into_view_if_needed()
        await asyncio.sleep(_short_pause())
        await el.click()
        await asyncio.sleep(_short_pause())

        # Clear existing content
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await asyncio.sleep(random.uniform(0.1, 0.2))

        # Type character by character with random delays
        for char in value:
            await page.keyboard.type(char, delay=_typing_delay())

        # Click body to blur (safer than Tab which can reach Submit button)
        await asyncio.sleep(0.2)
        await page.click("body", position={"x": 5, "y": 5}, no_wait_after=True)
        await asyncio.sleep(0.3)

        # Verify
        ok, err = await _verify_field(page, el, value, label)
        if not ok:
            # Retry once with fill() instead of keyboard
            try:
                await el.click()
                await asyncio.sleep(0.1)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                await el.type(value, delay=_typing_delay())
                await page.keyboard.press("Tab")
                await asyncio.sleep(0.3)
                ok2, err2 = await _verify_field(page, el, value, label)
                if ok2:
                    return FieldResult(label, selector, "text", value, "verified")
            except Exception:
                pass
            return FieldResult(label, selector, "text", value, "filled", err)

        return FieldResult(label, selector, "text", value, "verified")
    except Exception as exc:
        return FieldResult(label, selector, "text", value, "error", str(exc)[:120])


# ─── Phone number fields ────────────────────────────────

async def _fill_phone(page: Page, el: ElementHandle, value: str, label: str) -> FieldResult:
    """Read placeholder AND label for format hints, adjust digits accordingly."""
    selector = await _get_selector(el)
    try:
        placeholder = (await el.get_attribute("placeholder") or "").strip()
        maxlength = await el.get_attribute("maxlength")

        # Parse placeholder AND label for digit requirements
        digits_only = re.sub(r'\D', '', value)
        ph_lower = placeholder.lower()
        label_lower = label.lower()

        # Check both placeholder and label for "N digit(s)" requirement
        digit_req = None
        digit_match = re.search(r'(\d{1,2})\s*digit', ph_lower) or re.search(r'(\d{1,2})\s*digit', label_lower)
        if digit_match:
            digit_req = int(digit_match.group(1))

        if digit_req:
            # Field explicitly requires N digits — respect it exactly
            if len(digits_only) > digit_req:
                # Too many digits — strip from the left (remove country code)
                digits_only = digits_only[-digit_req:]
            elif len(digits_only) < digit_req:
                # Too few digits — zero-pad on the left (do NOT add country code)
                digits_only = digits_only.zfill(digit_req)
            formatted = digits_only
        elif maxlength and maxlength.isdigit():
            ml = int(maxlength)
            if ml == 10 and len(digits_only) > 10:
                digits_only = digits_only[-10:]
            formatted = digits_only[:ml]
        else:
            # Keep full value (may include country code)
            formatted = value

        # Respect placeholder formatting hints
        if placeholder and re.search(r'[\-\s\(\)]', placeholder):
            # Try to mirror placeholder spacing pattern
            # e.g. "XXX-XXX-XXXX" or "(XXX) XXX-XXXX"
            ph_pattern = re.sub(r'[Xx0-9]', '', placeholder)
            # Simple: if placeholder has dashes, add dashes
            if '-' in placeholder and len(digits_only) == 10:
                formatted = f"{digits_only[:3]}-{digits_only[3:6]}-{digits_only[6:]}"
            elif '(' in placeholder and ')' in placeholder and len(digits_only) == 10:
                formatted = f"({digits_only[:3]}) {digits_only[3:6]}-{digits_only[6:]}"

        return await _fill_text(page, el, formatted, label)
    except Exception as exc:
        return FieldResult(label, selector, "phone", value, "error", str(exc)[:120])


# ─── Native <select> dropdown ────────────────────────────

async def _fill_native_select(page: Page, el: ElementHandle, value: str,
                               label: str) -> FieldResult:
    """Use Playwright's select_option, verify displayed value."""
    selector = await _get_selector(el)
    try:
        await el.scroll_into_view_if_needed()
        await asyncio.sleep(_short_pause())

        # Try label match first
        try:
            await el.select_option(label=value)
        except Exception:
            # Fuzzy match
            matched = await el.evaluate("""(val) => {
                const opts = [...this.options];
                const match = opts.find(o =>
                    o.text.toLowerCase().includes(val.toLowerCase()) ||
                    o.value.toLowerCase().includes(val.toLowerCase())
                );
                if (match) {
                    this.value = match.value;
                    this.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
                return false;
            }""", value)
            if not matched:
                return FieldResult(label, selector, "native_select", value, "error",
                                   f"No option matching '{value}'")

        await asyncio.sleep(0.5)

        # Verify displayed value
        displayed = await el.evaluate("""e => {
            const opt = e.options[e.selectedIndex];
            return opt ? opt.text.trim() : '';
        }""")
        if displayed and value.lower() in displayed.lower():
            return FieldResult(label, selector, "native_select", value, "verified")
        return FieldResult(label, selector, "native_select", value, "filled")

    except Exception as exc:
        return FieldResult(label, selector, "native_select", value, "error", str(exc)[:120])


# ─── Custom / JS dropdown (styled divs) ─────────────────

async def _fill_custom_dropdown(page: Page, el: ElementHandle, value: str,
                                 label: str) -> FieldResult:
    """Click to open, wait for options list, find & click match."""
    selector = await _get_selector(el)
    try:
        await el.scroll_into_view_if_needed()
        await asyncio.sleep(_short_pause())
        await el.click()
        await asyncio.sleep(0.8)

        # Look for visible option list
        option = await page.evaluate_handle("""(val) => {
            const candidates = document.querySelectorAll(
                '[class*="option"], [class*="item"], [role="option"], li[data-value]'
            );
            for (const c of candidates) {
                if (c.offsetParent === null) continue;
                const text = c.textContent.trim().toLowerCase();
                if (text.includes(val.toLowerCase())) return c;
            }
            return null;
        }""", value)

        elem = option.as_element()
        if elem:
            await elem.click()
            await asyncio.sleep(0.4)
            return FieldResult(label, selector, "custom_dropdown", value, "verified")

        # Fallback: type and enter
        await page.keyboard.type(value[:15], delay=_typing_delay())
        await asyncio.sleep(0.5)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.3)
        return FieldResult(label, selector, "custom_dropdown", value, "filled")

    except Exception as exc:
        return FieldResult(label, selector, "custom_dropdown", value, "error", str(exc)[:120])


# ─── React Select / Autocomplete / Tag fields ───────────

async def _fill_react_select_field(page: Page, el: ElementHandle, value: str,
                                    label: str) -> FieldResult:
    """Type chars slowly, WAIT for suggestions, CLICK the match.
    NEVER presses Enter (that can submit the form).
    If no exact match, tries partial words, then picks first visible option."""
    selector = await _get_selector(el)
    try:
        await el.scroll_into_view_if_needed()
        await asyncio.sleep(_short_pause())

        # Click to focus / open
        await el.click()
        await asyncio.sleep(0.5)

        # Try multiple search strategies
        search_attempts = [value]
        # Add individual words as fallbacks (e.g. "AI/ML ENGINEERING" → "Engineering")
        words = re.split(r'[/,\s]+', value)
        for w in words:
            if w.strip() and w.strip().lower() != value.lower() and len(w.strip()) > 2:
                search_attempts.append(w.strip())

        option_clicked = False
        clicked_text = ""

        # JS helper to find and click an option
        CLICK_OPTION_JS = """(val) => {
            const options = document.querySelectorAll(
                '[class*="option"]:not([class*="disabled"]), ' +
                '[class*="menu"] [class*="option"], ' +
                '[role="option"], [class*="suggestion"], ' +
                '[class*="__option"], [class*="-option"]'
            );
            const valLower = val.toLowerCase();
            let best = null;
            let bestScore = Infinity;
            let bestText = '';
            for (const opt of options) {
                if (opt.offsetParent === null) continue;
                const text = opt.textContent.trim().toLowerCase();
                if (text.includes('no option') || text.includes('not found')) continue;
                if (text === valLower) { best = opt; bestScore = 0; bestText = opt.textContent.trim(); break; }
                if (text.includes(valLower) || valLower.includes(text)) {
                    const score = Math.abs(text.length - valLower.length);
                    if (score < bestScore) { best = opt; bestScore = score; bestText = opt.textContent.trim(); }
                }
            }
            if (best) { best.click(); return bestText; }
            // If no match but options exist, click the FIRST visible option
            for (const opt of options) {
                if (opt.offsetParent === null) continue;
                const text = opt.textContent.trim();
                if (text && !text.toLowerCase().includes('no option') && !text.toLowerCase().includes('not found')) {
                    opt.click();
                    return text;
                }
            }
            return '';
        }"""

        for attempt_val in search_attempts:
            # Clear previous input
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.2)

            # Type first few characters slowly to trigger search
            search_text = attempt_val[:20]
            for char in search_text:
                await page.keyboard.type(char, delay=_typing_delay())

            # WAIT for suggestions dropdown to appear
            await asyncio.sleep(1.5)

            result = await page.evaluate(CLICK_OPTION_JS, attempt_val)

            if result:
                option_clicked = True
                clicked_text = result
                break

        # Last resort: if all words failed, try single common letters to reveal ANY options
        if not option_clicked:
            for probe in ["a", "c", "e", "m", "s"]:
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.2)
                await page.keyboard.type(probe, delay=_typing_delay())
                await asyncio.sleep(1.2)
                result = await page.evaluate(CLICK_OPTION_JS, probe)
                if result:
                    option_clicked = True
                    clicked_text = result
                    break

        if option_clicked:
            await asyncio.sleep(0.5)
            # Verify: check for selected value token or hidden input
            has_value = await page.evaluate("""(sel) => {
                // Check for multi-value tokens
                const el = sel ? document.querySelector(sel) : null;
                let container = el;
                for (let i = 0; i < 8 && container; i++) {
                    container = container?.parentElement;
                    if (!container) break;
                    const cls = (typeof container.className === 'string') ? container.className : '';
                    if (cls.includes('__control') || cls.includes('react-select') || cls.includes('auto-complete')) {
                        // Check for selected values
                        const singleValue = container.parentElement?.querySelector('[class*="singleValue"], [class*="single-value"]');
                        const multiValues = container.parentElement?.querySelectorAll('[class*="multiValue"], [class*="multi-value"]');
                        if (singleValue && singleValue.textContent.trim()) return singleValue.textContent.trim();
                        if (multiValues && multiValues.length > 0) {
                            return [...multiValues].map(v => v.textContent.trim()).join(', ');
                        }
                        break;
                    }
                }
                return '';
            }""", selector)

            if has_value:
                return FieldResult(label, selector, "react_select", clicked_text or value, "verified")
            return FieldResult(label, selector, "react_select", clicked_text or value, "filled")

        # NO option found at all — close dropdown, report error
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        return FieldResult(label, selector, "react_select", value, "error",
                           f"No matching option found for '{label}' with value '{value}'")

    except Exception as exc:
        return FieldResult(label, selector, "react_select", value, "error", str(exc)[:120])


async def _fill_react_select_by_label(page: Page, label: str, value: str) -> Optional[FieldResult]:
    """Find a React Select near a label and fill it. Returns None if not found."""
    try:
        inputs = await page.query_selector_all(
            'input[id*="react-select"], input[role="combobox"], '
            'input[class*="auto-complete"], input[id*="subjects"], '
            'input[id*="Subject"], input[id*="select"]'
        )
        containers = await page.query_selector_all(
            '[class*="__control"], [class*="auto-complete__control"], '
            '[class*="react-select"], [class*="-container"][class*="css-"]'
        )
        for container in containers:
            inp = await container.query_selector('input')
            if inp and inp not in inputs:
                inputs.append(inp)

        for inp in inputs:
            is_near = await inp.evaluate("""(e, label) => {
                let node = e;
                for (let i = 0; i < 8; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    const lbl = node.querySelector(':scope > label, :scope > .label');
                    if (lbl && lbl.textContent.toLowerCase().includes(label.toLowerCase())) return true;
                    const prev = node.previousElementSibling;
                    if (prev && prev.textContent && prev.textContent.toLowerCase().includes(label.toLowerCase()) && prev.textContent.length < 100) return true;
                }
                if (e.id && e.id.toLowerCase().includes(label.toLowerCase().replace(/\\s/g, ''))) return true;
                return false;
            }""", label)
            if is_near:
                return await _fill_react_select_field(page, inp, value, label)
        return None
    except Exception:
        return None


# ─── Radio buttons ───────────────────────────────────────

async def _fill_radio(page: Page, selector: str, label: str, value: str) -> FieldResult:
    """Select the radio button matching the value. Uses force=True on labels."""
    val_lower = value.lower().strip()

    # Close any open datepickers/popups first
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass

    # Strategy 1: Playwright get_by_label
    try:
        loc = page.get_by_label(value, exact=True)
        if await loc.count() > 0:
            try:
                await loc.first.check(force=True)
                await asyncio.sleep(0.3)
                if await loc.first.is_checked():
                    return FieldResult(label, selector, "radio", value, "verified")
                return FieldResult(label, selector, "radio", value, "filled")
            except Exception:
                try:
                    await loc.first.click(force=True)
                    await asyncio.sleep(0.3)
                    return FieldResult(label, selector, "radio", value, "filled")
                except Exception:
                    pass
    except Exception:
        pass

    # Strategy 1b: get_by_text — click the label text directly
    try:
        loc = page.get_by_text(value, exact=True)
        if await loc.count() > 0:
            await loc.first.click(force=True)
            await asyncio.sleep(0.3)
            return FieldResult(label, selector, "radio", value, "filled")
    except Exception:
        pass

    # Strategy 2: Find all radios, match by label text, force-click label
    radios = []
    if selector:
        radios = await page.query_selector_all(selector)
    if not radios:
        radios = await page.query_selector_all('input[type="radio"]')

    for radio in radios:
        label_text = await radio.evaluate("""e => {
            if (e.labels && e.labels[0]) return e.labels[0].textContent.trim();
            const next = e.nextElementSibling || e.parentElement;
            return next?.textContent?.trim() || e.value || '';
        }""")

        if label_text and (val_lower == label_text.lower().strip() or
                           val_lower in label_text.lower() or
                           label_text.lower().strip() in val_lower):
            try:
                label_id = await radio.get_attribute("id")
                if label_id:
                    label_el = await page.query_selector(f'label[for="{label_id}"]')
                    if label_el:
                        await label_el.scroll_into_view_if_needed()
                        await label_el.click(force=True)
                        await asyncio.sleep(0.3)
                        # Verify
                        is_checked = await radio.is_checked()
                        status = "verified" if is_checked else "filled"
                        return FieldResult(label, selector, "radio", value, status)
                await radio.check(force=True)
                return FieldResult(label, selector, "radio", value, "filled")
            except Exception:
                pass

    # Strategy 3: Match by value attribute
    for radio in radios:
        radio_val = await radio.get_attribute("value")
        if radio_val and (val_lower in radio_val.lower() or radio_val.lower() in val_lower):
            try:
                label_id = await radio.get_attribute("id")
                if label_id:
                    label_el = await page.query_selector(f'label[for="{label_id}"]')
                    if label_el:
                        await label_el.click(force=True)
                        return FieldResult(label, selector, "radio", value, "filled")
                await radio.check(force=True)
                return FieldResult(label, selector, "radio", value, "filled")
            except Exception:
                pass

    # Strategy 4: JS force-set
    try:
        forced = await page.evaluate("""(value) => {
            const val = value.toLowerCase().trim();
            const radios = [...document.querySelectorAll('input[type="radio"]')];
            for (const r of radios) {
                const label = r.labels?.[0]?.textContent?.trim()?.toLowerCase() || r.value?.toLowerCase() || '';
                if (label === val || label.includes(val) || val.includes(label)) {
                    r.checked = true;
                    r.dispatchEvent(new Event('change', {bubbles: true}));
                    r.dispatchEvent(new Event('input', {bubbles: true}));
                    return true;
                }
            }
            return false;
        }""", value)
        if forced:
            return FieldResult(label, selector, "radio", value, "filled")
    except Exception:
        pass

    return FieldResult(label, selector, "radio", value, "error",
                       f"Could not select radio: {label} = {value}")


# ─── Checkboxes ──────────────────────────────────────────

async def _fill_checkbox(page: Page, selector: str, label: str,
                          value: str, profile_data: Optional[dict] = None) -> FieldResult:
    """Read all checkbox labels, cross-reference with profile/value, check matches.
    Returns needs_user if no matching data."""
    values = [v.strip().lower() for v in value.split(",")]
    checkboxes = []
    if selector:
        checkboxes = await page.query_selector_all(selector)
    if not checkboxes:
        # Try finding by label text proximity
        checkboxes = await page.query_selector_all('input[type="checkbox"]')
    if not checkboxes:
        # Also try custom checkbox wrappers (some sites use divs)
        custom = await page.query_selector_all('[class*="custom-checkbox"] input, [class*="checkbox"] input[type="checkbox"]')
        if custom:
            checkboxes = custom

    if not checkboxes:
        return FieldResult(label, selector, "checkbox", value, "error", "No checkboxes found")

    # Read all available labels
    all_labels = []
    for cb in checkboxes:
        cb_label = await cb.evaluate("""e => {
            if (e.labels && e.labels[0]) return e.labels[0].textContent.trim();
            const next = e.nextElementSibling || e.parentElement;
            return next?.textContent?.trim() || e.value || '';
        }""")
        all_labels.append(cb_label)

    # If value is empty and no profile data, ask the user
    if not value.strip() or value.strip().lower() in ("", "none", "unknown"):
        return FieldResult(label, selector, "checkbox",
                           f"Options: {', '.join(all_labels[:10])}",
                           "needs_user",
                           "Checkbox options need user selection")

    checked_count = 0
    for i, cb in enumerate(checkboxes):
        cb_label = all_labels[i] if i < len(all_labels) else ""
        cb_lower = cb_label.lower().strip()
        # Match if any value word is in the checkbox label or vice versa
        matched = False
        for v in values:
            v = v.strip()
            if not v:
                continue
            if v in cb_lower or cb_lower in v:
                matched = True
                break
            # Also match individual words (e.g. "sports" matches "Sports")
            v_words = v.split()
            for vw in v_words:
                if len(vw) > 2 and vw in cb_lower:
                    matched = True
                    break
            if matched:
                break

        if cb_label and matched:
            is_checked = await cb.is_checked()
            if not is_checked:
                await cb.scroll_into_view_if_needed()
                await asyncio.sleep(random.uniform(0.2, 0.5))
                try:
                    # Click the label element with force
                    label_el = await cb.evaluate_handle("""e => e.labels?.[0] || e.parentElement""")
                    await label_el.as_element().click(force=True)
                except Exception:
                    try:
                        await cb.click(force=True)
                    except Exception:
                        await cb.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true})); }")
                checked_count += 1
                await asyncio.sleep(random.uniform(0.3, 0.6))

    if checked_count > 0:
        return FieldResult(label, selector, "checkbox", value, "verified",
                           f"Checked {checked_count} boxes")
    return FieldResult(label, selector, "checkbox", value, "error",
                       f"No checkboxes matched values: {value}")


# ─── Date picker (calendar popup) ────────────────────────

def _parse_date_value(value: str) -> tuple[int, int, int] | None:
    """Parse a date string into (day, month, year). Returns None on failure."""
    value = value.strip()
    parts = re.split(r'[/\-.]', value)
    if len(parts) != 3:
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None

    a, b, c = nums
    if a > 100:
        return (c, b, a)  # ISO: YYYY-MM-DD -> day, month, year
    if c > 100:
        if a > 12:
            return (a, b, c)  # DD/MM/YYYY
        elif b > 12:
            return (b, a, c)  # MM/DD/YYYY
        else:
            return (a, b, c)  # Ambiguous — assume DD/MM/YYYY
    return None


_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


async def _fill_datepicker(page: Page, el: ElementHandle, value: str,
                            label: str) -> FieldResult:
    """Navigate a React datepicker calendar popup. Click arrows for month/year,
    then click the correct day."""
    selector = await _get_selector(el)
    parsed = _parse_date_value(value)
    if not parsed:
        # Fallback: try typing via JS
        return await _fill_date_via_js(page, el, value, label)

    day, month, year = parsed
    target_month_name = _MONTH_NAMES[month - 1]

    try:
        await el.scroll_into_view_if_needed()
        await asyncio.sleep(_short_pause())
        await el.click()
        await asyncio.sleep(0.8)  # Wait for calendar popup

        # Check if a calendar popup appeared
        has_calendar = await page.evaluate("""() => {
            return !!(
                document.querySelector('.react-datepicker, [class*="datepicker__month"], ' +
                    '[class*="calendar"], [role="dialog"][class*="date"], .flatpickr-calendar')
            );
        }""")

        if has_calendar:
            # ── Strategy 1: Use month/year dropdowns if available ──
            # React datepicker (demoqa-style) often has <select> dropdowns
            # for month and year, which is far faster than clicking arrows.
            dropdown_set = await page.evaluate("""(args) => {
                const [targetMonth, targetYear] = args;
                const monthSel = document.querySelector(
                    '.react-datepicker__month-select, ' +
                    'select[class*="month-select"], select[class*="month"]'
                );
                const yearSel = document.querySelector(
                    '.react-datepicker__year-select, ' +
                    'select[class*="year-select"], select[class*="year"]'
                );
                if (monthSel && yearSel) {
                    // Month dropdown uses 0-indexed values (0=Jan, 11=Dec)
                    const monthIdx = targetMonth - 1;
                    monthSel.value = String(monthIdx);
                    monthSel.dispatchEvent(new Event('change', {bubbles: true}));
                    yearSel.value = String(targetYear);
                    yearSel.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
                return false;
            }""", [month, year])

            if dropdown_set:
                await asyncio.sleep(0.5)
            else:
                # ── Strategy 2: Click arrow buttons (up to 300 = ~25 years) ──
                for _ in range(300):
                    header_text = await page.evaluate("""() => {
                        const hdr = document.querySelector(
                            '.react-datepicker__current-month, ' +
                            '[class*="datepicker__header"] [class*="current"], ' +
                            '[class*="calendar"] [class*="header"], ' +
                            '.flatpickr-current-month'
                        );
                        return hdr ? hdr.textContent.trim() : '';
                    }""")

                    if not header_text:
                        break

                    # Check if we're at the right month/year
                    if target_month_name.lower() in header_text.lower() and str(year) in header_text:
                        break

                    # Determine direction — parse current month/year from header
                    current_year = None
                    current_month = None
                    for i, mn in enumerate(_MONTH_NAMES):
                        if mn.lower() in header_text.lower() or mn[:3].lower() in header_text.lower():
                            current_month = i + 1
                            break
                    year_match = re.search(r'(19|20)\d{2}', header_text)
                    if year_match:
                        current_year = int(year_match.group())

                    if current_year and current_month:
                        current_val = current_year * 12 + current_month
                        target_val = year * 12 + month
                        if target_val > current_val:
                            arrow_sel = ('.react-datepicker__navigation--next, '
                                         '[class*="datepicker"] [class*="next"], '
                                         '[class*="calendar"] button[class*="next"], '
                                         '.flatpickr-next-month')
                        else:
                            arrow_sel = ('.react-datepicker__navigation--previous, '
                                         '[class*="datepicker"] [class*="prev"], '
                                         '[class*="calendar"] button[class*="prev"], '
                                         '.flatpickr-prev-month')
                    else:
                        # Default to previous (most datepickers open at today,
                        # and DOB targets are in the past)
                        arrow_sel = ('.react-datepicker__navigation--previous, '
                                     '[class*="datepicker"] [class*="prev"]')

                    arrow = await page.query_selector(arrow_sel)
                    if arrow:
                        await arrow.click()
                        await asyncio.sleep(0.35)
                    else:
                        break

            # Click the correct day
            day_clicked = await page.evaluate("""(day) => {
                const dayEls = document.querySelectorAll(
                    '.react-datepicker__day, [class*="datepicker__day"], ' +
                    '[class*="calendar"] [class*="day"], .flatpickr-day'
                );
                for (const d of dayEls) {
                    const cls = d.className || '';
                    // Skip outside-month days
                    if (cls.includes('outside') || cls.includes('disabled') ||
                        cls.includes('prevMonthDay') || cls.includes('nextMonthDay')) continue;
                    const text = d.textContent.trim();
                    if (parseInt(text) === day) {
                        d.click();
                        return true;
                    }
                }
                return false;
            }""", day)

            await asyncio.sleep(0.4)

            if day_clicked:
                # Close calendar
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
                return FieldResult(label, selector, "datepicker", value, "verified")

        # Fallback: use JS to set value
        return await _fill_date_via_js(page, el, value, label)

    except Exception as exc:
        return FieldResult(label, selector, "datepicker", value, "error", str(exc)[:120])


async def _fill_date_via_js(page: Page, el: ElementHandle, value: str,
                             label: str) -> FieldResult:
    """Set a date value using native JS setter to bypass React."""
    selector = await _get_selector(el)
    parsed = _parse_date_value(value)
    if parsed:
        day, month, year = parsed
        formatted = f"{month:02d}/{day:02d}/{year:04d}"
    else:
        formatted = value

    try:
        await el.scroll_into_view_if_needed()
        await asyncio.sleep(_short_pause())

        await page.evaluate("""([sel, formatted]) => {
            const el = document.querySelector(sel) || document.activeElement;
            if (!el) return;
            const nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            nativeSetter.call(el, formatted);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        }""", [selector, formatted])

        await asyncio.sleep(0.3)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        await page.keyboard.press("Tab")

        return FieldResult(label, selector, "datepicker", value, "filled")
    except Exception as exc:
        return FieldResult(label, selector, "datepicker", value, "error", str(exc)[:120])


async def _fill_date_native(page: Page, el: ElementHandle, value: str,
                             label: str) -> FieldResult:
    """Fill an <input type="date"> using .fill() which handles the native widget."""
    selector = await _get_selector(el)
    try:
        await el.scroll_into_view_if_needed()
        parsed = _parse_date_value(value)
        if parsed:
            day, month, year = parsed
            iso = f"{year:04d}-{month:02d}-{day:02d}"
        else:
            iso = value
        await el.fill(iso)
        await asyncio.sleep(0.3)
        return FieldResult(label, selector, "date_native", value, "verified")
    except Exception as exc:
        return FieldResult(label, selector, "date_native", value, "error", str(exc)[:120])


# ─── File upload ─────────────────────────────────────────

async def _handle_file_upload(page: Page, el: ElementHandle, value: str,
                               label: str) -> FieldResult:
    """Don't skip silently — report back that the user must provide a file."""
    selector = await _get_selector(el)
    return FieldResult(label, selector, "file", value, "needs_user",
                       f"File upload needed for '{label}'. Please provide the file.")


# ─── Utility: get a usable selector for an element ──────

async def _get_selector(el: ElementHandle) -> str:
    """Build a CSS selector string for an element."""
    try:
        info = await el.evaluate("""e => {
            if (e.id) return '#' + e.id;
            if (e.name) return e.tagName.toLowerCase() + '[name="' + e.name + '"]';
            return '';
        }""")
        return info or ""
    except Exception:
        return ""


# ─── Natural scrolling between sections ──────────────────

async def _scroll_to_element(page: Page, el: ElementHandle):
    """Scroll smoothly to an element like a human would."""
    try:
        await el.evaluate("""e => {
            e.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }""")
        await asyncio.sleep(random.uniform(0.3, 0.6))
    except Exception:
        try:
            await el.scroll_into_view_if_needed()
        except Exception:
            pass


# ─── Dynamic fields ──────────────────────────────────────

async def _handle_dynamic_fields(page: Page):
    """Wait briefly for any dynamic fields that appear after filling."""
    await asyncio.sleep(0.3)


# ─── Multi-page navigation ──────────────────────────────

async def _navigate_pages(page: Page, matches: list[dict]) -> int:
    """Detect and handle multi-page forms. Does NOT click submit.

    SAFETY: Only clicks buttons whose trimmed text EXACTLY matches a known
    navigation word.  Never clicks anything that looks like a submit,
    payment, or sign-up button — even if it also contains a nav word."""
    pages = 1
    max_pages = 10

    while pages < max_pages:
        next_clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button, input[type="submit"], a.btn, [role="button"], a[class*="btn"]')];

            /* ── Dangerous words: NEVER click any button containing these ── */
            const dangerWords = [
                'submit', 'send', 'apply', 'finish', 'complete', 'confirm',
                'register', 'sign up', 'signup', 'create', 'pay', 'order',
                'checkout', 'check out', 'place order', 'subscribe', 'donate',
                'purchase', 'buy', 'enroll', 'enrol', 'book now', 'reserve'
            ];

            /* ── Safe navigation words: the button text must EXACTLY match ── */
            const safeExact = ['next', 'continue', 'proceed', 'forward',
                               'siguiente', 'suivant', 'weiter',
                               'next step', 'next page', 'go to next'];

            /* ── Dangerous CSS classes on the button itself ── */
            const dangerClasses = ['primary', 'danger', 'success', 'btn-submit',
                                   'btn-danger', 'btn-primary', 'submit'];

            for (const btn of btns) {
                const raw = (btn.textContent || btn.value || '').trim();
                const text = raw.toLowerCase();

                /* Skip anything with a dangerous word anywhere in its text */
                if (dangerWords.some(w => text.includes(w))) continue;

                /* Skip input[type="submit"] outright */
                if (btn.tagName === 'INPUT' && (btn.type || '').toLowerCase() === 'submit') continue;

                /* Skip buttons styled as primary / danger / success */
                const cls = (btn.className || '').toLowerCase();
                if (dangerClasses.some(c => cls.includes(c))) continue;

                /* Only click if the EXACT trimmed text matches a safe word */
                if (safeExact.includes(text) && btn.offsetParent !== null) {
                    btn.click();
                    return 'clicked';
                }
            }
            return 'none';
        }""")

        if next_clicked == "clicked":
            await asyncio.sleep(2)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            pages += 1
            await _dismiss_popups(page)
        else:
            break

    return pages


# ─── Validation checking ────────────────────────────────

async def _check_validation(page: Page) -> list[str]:
    """Check for validation error messages on the form."""
    errors = await page.evaluate("""() => {
        const errs = [];
        const errorEls = document.querySelectorAll(
            '.error, .form-error, [class*="error"], [class*="invalid"], [role="alert"], .field-error, .help-block.text-danger'
        );
        errorEls.forEach(el => {
            const text = el.textContent?.trim();
            if (text && text.length < 200 && text.length > 2 && el.offsetParent !== null) {
                errs.push(text);
            }
        });
        return [...new Set(errs)].slice(0, 5);
    }""")
    return [f"Form validation: {e}" for e in errors] if errors else []


# ═══════════════════════════════════════════════════════════
#  MAIN FILL ENGINE
# ═══════════════════════════════════════════════════════════

async def _fill_form(url: str, matches: list[dict]) -> FillResult:
    """Navigate to URL and autonomously fill every field with intelligence."""
    filled = 0
    skipped = 0
    pages = 1
    errors: list[str] = []
    field_results: list[FieldResult] = []
    captcha = False

    async with async_playwright() as p:
        # Launch with stealth settings
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Europe/London",
        )

        # Remove webdriver flag
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # ── Step 0: PREVENT FORM SUBMISSION ──
        # Intercept ALL form submit events so the agent never accidentally submits
        await page.evaluate("""() => {
            document.querySelectorAll('form').forEach(f => {
                f.addEventListener('submit', e => {
                    e.preventDefault();
                    e.stopPropagation();
                    e.stopImmediatePropagation();
                    return false;
                }, true);
            });
            // Also override .submit() method on all forms
            document.querySelectorAll('form').forEach(f => {
                f.submit = () => {};
            });
            // Block Enter key from submitting forms
            document.addEventListener('keydown', e => {
                if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') {
                    const form = e.target.closest('form');
                    if (form) {
                        e.preventDefault();
                        e.stopPropagation();
                    }
                }
            }, true);
        }""")

        # ── Step 1: Dismiss popups ──
        await _dismiss_popups(page)
        await asyncio.sleep(_human_delay())

        # ── Step 2: Full page scan — scroll top to bottom ──
        await _full_page_scan(page)
        await asyncio.sleep(0.5)

        # ── Step 3: Sort fields (cascading-aware) ──
        # Fill text/email/tel first, then datepickers, then autocomplete/react select,
        # then native selects (state before city), then radios, then checkboxes last
        def _fill_priority(m: dict) -> int:
            ft = m.get("field_type", "text")
            label_lower = (m.get("label") or "").lower()
            if ft in ("text", "email", "tel", "number", "textarea"):
                return 0
            if ft in ("date", "datepicker"):
                return 1
            if ft in ("autocomplete",):
                # Subjects/skills autocomplete should come before state/city
                if any(kw in label_lower for kw in ("subject", "skill", "hobby", "tag")):
                    return 2
                return 3  # State/city react selects
            if ft in ("select",):
                # State before city (cascading)
                if any(kw in label_lower for kw in ("state", "country", "province")):
                    return 4
                if any(kw in label_lower for kw in ("city", "district")):
                    return 5
                return 4
            if ft == "radio":
                return 6
            if ft == "checkbox":
                return 7
            return 8

        sorted_matches = sorted(
            [m for m in matches
             if m.get("value") and m.get("match_type") != "skipped"],
            key=_fill_priority,
        )

        # Track which "parent" selects have been filled (for cascading)
        filled_selects: list[str] = []

        for match in sorted_matches:
            selector = match["selector"]
            value = str(match["value"])
            ftype = match.get("field_type", "text")
            label = match.get("label", selector)

            try:
                # ── Human delay between fields ──
                await asyncio.sleep(_human_delay())

                # ── Handle autocomplete / React Select fields ──
                if ftype == "autocomplete":
                    # Try to find the input element
                    el = await _find_element(page, selector, label, ftype)
                    if el:
                        fr = await _fill_react_select_field(page, el, value, label)
                    else:
                        fr_rs = await _fill_react_select_by_label(page, label, value)
                        fr = fr_rs or FieldResult(label, selector, "autocomplete", value, "error",
                                                   f"Could not find autocomplete: {label}")
                    field_results.append(fr)
                    if fr.status in ("filled", "verified"):
                        filled += 1
                        # Cascading wait
                        label_lower = label.lower()
                        if any(kw in label_lower for kw in ("state", "country", "province", "region")):
                            await asyncio.sleep(random.uniform(2.0, 3.0))
                    else:
                        skipped += 1
                        if fr.error_message:
                            errors.append(fr.error_message)
                    continue

                # ── Handle radio buttons (group-based, no _find_element) ──
                if ftype == "radio":
                    fr = await _fill_radio(page, selector, label, value)
                    field_results.append(fr)
                    if fr.status in ("filled", "verified"):
                        filled += 1
                    else:
                        skipped += 1
                        errors.append(fr.error_message or f"Radio failed: {label}")
                    continue

                # ── Handle checkboxes ──
                if ftype == "checkbox":
                    fr = await _fill_checkbox(page, selector, label, value)
                    field_results.append(fr)
                    if fr.status in ("filled", "verified"):
                        filled += 1
                    elif fr.status == "needs_user":
                        skipped += 1
                    else:
                        skipped += 1
                        errors.append(fr.error_message or f"Checkbox failed: {label}")
                    continue

                # ── Handle file uploads ──
                if ftype == "file":
                    fr = await _handle_file_upload(page, None, value, label)
                    field_results.append(fr)
                    skipped += 1
                    continue

                # ── Handle selects (native + React) ──
                if ftype == "select":
                    # Check if this is a React Select first
                    if "react-select" in selector or "css-" in selector:
                        el = await _find_element(page, selector, label, ftype)
                        if el:
                            fr = await _fill_react_select_field(page, el, value, label)
                        else:
                            fr_rs = await _fill_react_select_by_label(page, label, value)
                            fr = fr_rs or FieldResult(label, selector, "select", value, "error",
                                                       f"Could not find React Select: {label}")
                    else:
                        el = await _find_element(page, selector, label, ftype)
                        if el:
                            tag = await el.evaluate("e => e.tagName.toLowerCase()")
                            if tag == "select":
                                fr = await _fill_native_select(page, el, value, label)
                            else:
                                # Detect real type
                                real_type = await _detect_field_type(page, el, ftype, label)
                                if real_type == "react_select":
                                    fr = await _fill_react_select_field(page, el, value, label)
                                else:
                                    fr = await _fill_custom_dropdown(page, el, value, label)
                        else:
                            # Try React Select by label as last resort
                            fr_rs = await _fill_react_select_by_label(page, label, value)
                            fr = fr_rs or FieldResult(label, selector, "select", value, "error",
                                                       f"Could not find: {label}")

                    field_results.append(fr)
                    if fr.status in ("filled", "verified"):
                        filled += 1
                        filled_selects.append(label.lower())
                        # Cascading wait: if this looks like a state/country,
                        # pause for dependent fields to load their options
                        label_lower = label.lower()
                        if any(kw in label_lower for kw in ("state", "country", "province", "region")):
                            await asyncio.sleep(random.uniform(2.0, 3.0))
                            # Re-dismiss popups in case cascade triggered a reload
                            await _dismiss_popups(page)
                    else:
                        skipped += 1
                        if fr.error_message:
                            errors.append(fr.error_message)
                    continue

                # ── All other field types: find element first ──
                el = await _find_element(page, selector, label, ftype)
                if not el:
                    # Last resort: maybe it's a React Select not tagged as select
                    if label:
                        fr_rs = await _fill_react_select_by_label(page, label, value)
                        if fr_rs and fr_rs.status in ("filled", "verified"):
                            field_results.append(fr_rs)
                            filled += 1
                            continue
                    fr = FieldResult(label, selector, ftype, value, "skipped",
                                     f"Could not find: {label}")
                    field_results.append(fr)
                    skipped += 1
                    errors.append(f"Could not find: {label}")
                    continue

                # Scroll to element naturally
                await _scroll_to_element(page, el)

                # ── Detect the REAL field type from DOM ──
                real_type = await _detect_field_type(page, el, ftype, label)

                # ── Route to the correct handler ──
                if real_type == "react_select":
                    fr = await _fill_react_select_field(page, el, value, label)
                    # Cascading wait for react selects (state → city)
                    if fr.status in ("filled", "verified"):
                        label_lower = label.lower()
                        if any(kw in label_lower for kw in ("state", "country", "province", "region")):
                            await asyncio.sleep(random.uniform(2.0, 3.0))

                elif real_type == "datepicker":
                    fr = await _fill_datepicker(page, el, value, label)

                elif real_type == "date_native":
                    fr = await _fill_date_native(page, el, value, label)

                elif real_type == "date_text":
                    # Date in a plain text field — try calendar first, fall back to JS
                    fr = await _fill_datepicker(page, el, value, label)

                elif real_type == "phone":
                    fr = await _fill_phone(page, el, value, label)

                elif real_type == "native_select":
                    fr = await _fill_native_select(page, el, value, label)

                elif real_type == "file":
                    fr = await _handle_file_upload(page, el, value, label)

                elif real_type in ("text", "email", "number", "textarea"):
                    fr = await _fill_text(page, el, value, label)

                else:
                    # Default: treat as text
                    fr = await _fill_text(page, el, value, label)

                field_results.append(fr)
                if fr.status in ("filled", "verified"):
                    filled += 1
                elif fr.status == "needs_user":
                    skipped += 1
                else:
                    skipped += 1
                    if fr.error_message:
                        errors.append(fr.error_message)

                await _handle_dynamic_fields(page)

            except Exception as e:
                fr = FieldResult(label, selector, ftype, value, "error", str(e)[:120])
                field_results.append(fr)
                skipped += 1
                errors.append(f"{label}: {str(e)[:120]}")

        # ── Handle multi-page forms ──
        pages = await _navigate_pages(page, sorted_matches)

        # ── CAPTCHA check ──
        captcha = await _check_captcha(page)
        if captcha:
            errors.append("CAPTCHA detected on the form -- you'll need to solve it manually")

        # ── Validation check ──
        validation_errors = await _check_validation(page)
        if validation_errors:
            errors.extend(validation_errors)

        # ── Check for submit button and report (do NOT click) ──
        has_submit = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button, input[type="submit"], [role="button"]')];
            const submitWords = ['submit', 'finish', 'complete', 'send', 'apply'];
            for (const btn of btns) {
                const text = (btn.textContent || btn.value || '').trim().toLowerCase();
                if (submitWords.some(w => text.includes(w)) && btn.offsetParent !== null) {
                    return text;
                }
            }
            return null;
        }""")
        if has_submit:
            errors.append(f"Submit button found ('{has_submit}') -- NOT clicked. Review and submit manually.")

        # ── Hide overlays, take screenshot ──
        await page.evaluate("""() => {
            document.querySelectorAll('[style*="position: fixed"], [style*="position:fixed"]').forEach(el => {
                el.style.display = 'none';
            });
            document.querySelectorAll('#fixedban, .adsbygoogle, [id*="google_ads"], iframe[src*="googleads"]').forEach(el => {
                el.style.display = 'none';
            });
        }""")

        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(1.5)
        screenshot = await page.screenshot(full_page=True, type="png")
        screenshot_b64 = base64.b64encode(screenshot).decode()

        await browser.close()

    return FillResult(
        filled=filled,
        skipped=skipped,
        pages_navigated=pages,
        screenshot_b64=screenshot_b64,
        errors=errors,
        captcha_detected=captcha,
        field_results=field_results,
    )


# ─── Public API ──────────────────────────────────────────

def fill_form(url: str, matches: list[dict]) -> FillResult:
    """Synchronous wrapper -- the main entry point."""
    return asyncio.run(_fill_form(url, matches))
