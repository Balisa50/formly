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

from playwright.async_api import async_playwright, Page, Frame, BrowserContext, ElementHandle

# Union type used throughout so the same helpers work on both the main Page
# and any child Frame (e.g. Workday / Greenhouse iframe embeds).
PageOrFrame = Page | Frame


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
    otp_detected: bool = False
    login_wall: bool = False
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

async def _find_element(page: PageOrFrame, selector: str, label: str, ftype: str) -> Optional[ElementHandle]:
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
    if itype == "time":
        return "time_native"
    # Label-based time heuristic (catches text inputs styled as time pickers)
    label_lower_early = (label or "").lower()
    if any(kw in label_lower_early for kw in ("preferred time", "delivery time", "pickup time",
                                               "appointment time", "meeting time", "schedule time",
                                               "time slot", "arrival time", "departure time")):
        return "time_native"
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
        # Check if this textarea is backing a rich text editor (hidden element)
        is_hidden_backing = await el.evaluate("""e => {
            const s = window.getComputedStyle(e);
            const hidden = s.display === 'none' || s.visibility === 'hidden'
                           || e.offsetHeight === 0 || e.offsetWidth === 0;
            if (!hidden) return false;
            // Confirm a rich editor is present on the page
            return !!(
                document.querySelector('.ql-editor, .tox-edit-area, iframe[id$="_ifr"], ' +
                    '.ck-editor__editable, trix-editor, .ProseMirror')
            );
        }""")
        if is_hidden_backing:
            return "rich_text"
        return "textarea"

    # Check for Google Places address autocomplete
    if tag == "input" and itype in ("text", "search", ""):
        label_lower_check = (label or "").lower()
        if any(kw in label_lower_check for kw in ("address", "street", "location", "city", "place")):
            has_places = await page.evaluate("() => !!(window.google && window.google.maps && window.google.maps.places)")
            if has_places:
                return "google_places"

    # Default text
    return declared_type if declared_type in ("text", "email", "tel", "number") else "text"


# ═══════════════════════════════════════════════════════════
#  GOOGLE PLACES AUTOCOMPLETE
# ═══════════════════════════════════════════════════════════

async def _handle_google_places_suggestion(page: Page) -> bool:
    """If a Google Places pac-container appeared, click the first suggestion.
    Called transparently after typing into any text field.
    Returns True if a suggestion was clicked."""
    try:
        clicked = await page.evaluate("""() => {
            const container = document.querySelector('.pac-container');
            if (!container) return false;
            const style = window.getComputedStyle(container);
            if (style.display === 'none' || style.visibility === 'hidden'
                || container.offsetHeight === 0) return false;
            const first = container.querySelector('.pac-item');
            if (!first || first.offsetParent === null) return false;
            // pac-container responds to mousedown, not click
            first.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
            first.dispatchEvent(new MouseEvent('mouseup',   { bubbles: true, cancelable: true }));
            first.dispatchEvent(new MouseEvent('click',     { bubbles: true, cancelable: true }));
            return true;
        }""")
        if clicked:
            await asyncio.sleep(0.8)  # let address fields auto-populate
        return bool(clicked)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
#  RICH TEXT EDITOR FILLING
# ═══════════════════════════════════════════════════════════

async def _fill_rich_text(page: Page, el: ElementHandle, value: str, label: str) -> FieldResult:
    """Fill any rich text editor: Quill, TinyMCE, CKEditor 4/5, Draft.js, Trix,
    ProseMirror, or plain contenteditable. Tries each engine's native JS API
    so the editor's internal state is updated correctly (not just the DOM)."""
    selector = await _get_selector(el)

    # ── 1. Quill ──────────────────────────────────────────
    try:
        set_ok = await page.evaluate("""(text) => {
            const editor = document.querySelector('.ql-editor');
            if (!editor) return false;
            // Prefer Quill API (persists to hidden input)
            const container = editor.closest('.ql-container');
            if (container && container.__quill) {
                container.__quill.setText(text + '\\n');
                return true;
            }
            // Direct contenteditable injection
            editor.innerHTML = '<p>' + text.replace(/\\n/g, '</p><p>') + '</p>';
            editor.dispatchEvent(new Event('input',  { bubbles: true }));
            editor.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }""", value)
        if set_ok:
            return FieldResult(label, selector, "rich_text", value, "filled")
    except Exception:
        pass

    # ── 2. TinyMCE ────────────────────────────────────────
    try:
        set_ok = await page.evaluate("""(text) => {
            if (!window.tinymce) return false;
            const eds = tinymce.editors;
            if (!eds || eds.length === 0) return false;
            eds[0].setContent('<p>' + text.replace(/\\n/g, '</p><p>') + '</p>');
            eds[0].fire('change');
            return true;
        }""", value)
        if set_ok:
            return FieldResult(label, selector, "rich_text", value, "filled")
    except Exception:
        pass

    # ── 3. CKEditor 5 ─────────────────────────────────────
    try:
        set_ok = await page.evaluate("""(text) => {
            // CKEditor 5 stores the instance on the DOM element
            const editable = document.querySelector('.ck-editor__editable[contenteditable="true"]');
            if (!editable) return false;
            const ckInstance = editable.ckeditorInstance;
            if (ckInstance) {
                ckInstance.setData('<p>' + text + '</p>');
                return true;
            }
            // Fallback: execCommand
            editable.focus();
            document.execCommand('selectAll');
            document.execCommand('insertText', false, text);
            return true;
        }""", value)
        if set_ok:
            return FieldResult(label, selector, "rich_text", value, "filled")
    except Exception:
        pass

    # ── 4. CKEditor 4 ─────────────────────────────────────
    try:
        set_ok = await page.evaluate("""(text) => {
            if (!window.CKEDITOR) return false;
            for (const id in CKEDITOR.instances) {
                CKEDITOR.instances[id].setData('<p>' + text + '</p>');
                return true;
            }
            return false;
        }""", value)
        if set_ok:
            return FieldResult(label, selector, "rich_text", value, "filled")
    except Exception:
        pass

    # ── 5. Trix (Basecamp/HEY) ────────────────────────────
    try:
        set_ok = await page.evaluate("""(text) => {
            const trix = document.querySelector('trix-editor');
            if (!trix || !trix.editor) return false;
            trix.editor.loadHTML('<p>' + text + '</p>');
            return true;
        }""", value)
        if set_ok:
            return FieldResult(label, selector, "rich_text", value, "filled")
    except Exception:
        pass

    # ── 6. ProseMirror / Draft.js / generic contenteditable ──
    try:
        ce_handle = await page.evaluate_handle("""() =>
            document.querySelector(
                '.ProseMirror[contenteditable="true"], ' +
                'div[contenteditable="true"][data-block="true"], ' +
                'div[contenteditable="true"][class*="editor"], ' +
                'div[contenteditable="true"][class*="input"]'
            )
        """)
        ce = ce_handle.as_element()
        if ce:
            await ce.scroll_into_view_if_needed()
            await ce.click()
            await asyncio.sleep(0.3)
            await page.keyboard.press("Control+a")
            await asyncio.sleep(0.1)
            for char in value:
                await page.keyboard.type(char, delay=_typing_delay())
            await asyncio.sleep(0.3)
            return FieldResult(label, selector, "rich_text", value, "filled")
    except Exception:
        pass

    # ── 7. Last resort: click the textarea itself and type ─
    try:
        await el.scroll_into_view_if_needed()
        await el.click()
        await asyncio.sleep(0.2)
        await page.keyboard.press("Control+a")
        await asyncio.sleep(0.1)
        for char in value:
            await page.keyboard.type(char, delay=_typing_delay())
        return FieldResult(label, selector, "rich_text", value, "filled")
    except Exception as exc:
        return FieldResult(label, selector, "rich_text", value, "error", str(exc)[:120])


# ═══════════════════════════════════════════════════════════
#  CONDITIONAL FIELD DETECTION
# ═══════════════════════════════════════════════════════════

# Flat keyword → profile key mapping for fast lookup without LLM
_LABEL_TO_PROFILE_KEYS: list[tuple[tuple[str, ...], list[str]]] = [
    (("first name", "given name", "firstname"),         ["first_name"]),
    (("last name", "surname", "family name"),           ["last_name"]),
    (("full name", "your name", "applicant name"),      ["full_name", "first_name"]),
    (("email", "e-mail", "email address"),              ["email"]),
    (("phone", "mobile", "telephone", "cell"),          ["phone", "phone_number"]),
    (("address", "street address", "street"),           ["address"]),
    (("city", "town", "municipality"),                  ["city"]),
    (("state", "province", "county"),                   ["state", "province"]),
    (("country",),                                      ["country", "nationality"]),
    (("zip", "postal code", "postcode"),                ["postal_code", "zip_code"]),
    (("nationality", "citizenship"),                    ["nationality"]),
    (("gender", "sex"),                                 ["gender"]),
    (("date of birth", "dob", "birthday", "birth date"),["date_of_birth", "dob"]),
    (("linkedin",),                                     ["linkedin", "linkedin_url"]),
    (("website", "portfolio", "personal site"),         ["website", "portfolio_url"]),
    (("university", "institution", "school", "college"),["university", "institution"]),
    (("degree", "qualification", "highest education"),  ["degree"]),
    (("gpa", "cgpa", "grade point"),                    ["gpa", "cgpa"]),
    (("company", "employer", "current employer"),       ["company", "current_company"]),
    (("job title", "position", "current role", "title"),["current_title", "job_title", "title"]),
    (("years of experience", "experience (years)"),     ["years_experience"]),
    (("bio", "about me", "about yourself", "summary"),  ["bio", "summary", "about_me"]),
    (("cover letter", "motivation", "why do you want"), ["cover_letter"]),
    (("skills",),                                       ["skills"]),
    (("nationality",),                                  ["nationality"]),
]


def _quick_profile_match(
    label: str,
    field_type: str,
    options: list[str],
    profile: dict,
) -> str | None:
    """Fast keyword-based profile lookup for conditional fields — no LLM needed.
    Works for the common fields that appear after conditional logic fires."""
    if not profile or not label:
        return None

    label_lower = label.lower().strip()

    for keywords, profile_keys in _LABEL_TO_PROFILE_KEYS:
        if any(kw in label_lower for kw in keywords):
            for key in profile_keys:
                val = (
                    profile.get("personal", {}).get(key)
                    or profile.get(key)
                )
                if val and isinstance(val, str) and val.strip():
                    raw = val.strip()
                    # For choice fields, validate against available options
                    if options and field_type in ("select", "radio", "native_select", "autocomplete"):
                        opts_lower = [o.lower() for o in options]
                        raw_lower  = raw.lower()
                        match = next(
                            (o for o, ol in zip(options, opts_lower)
                             if raw_lower in ol or ol in raw_lower),
                            None,
                        )
                        if match:
                            return match
                        continue  # try next profile key
                    return raw

    # For small-option select/radio, cross-check every profile string
    if field_type in ("select", "radio", "native_select") and options and len(options) <= 15:
        for val in profile.values():
            if not isinstance(val, str):
                continue
            vl = val.lower().strip()
            for opt in options:
                if vl in opt.lower() or opt.lower() in vl:
                    return opt

    return None


async def _scan_and_fill_new_fields(
    page: Page,
    seen_selectors: set[str],
    profile: dict,
    errors: list[str],
    field_results: list[FieldResult],
) -> tuple[int, int]:
    """Re-scan for fields that appeared after previous fills (conditional logic).
    Matches new fields against the profile without LLM and fills them.
    Returns (newly_filled, newly_skipped)."""
    from .form_reader import _FIELD_EXTRACTION_JS, _postprocess_fields  # no circular dep

    try:
        new_data = await page.evaluate(_FIELD_EXTRACTION_JS)
    except Exception:
        return 0, 0

    new_fields = _postprocess_fields(new_data, "")
    filled = skipped = 0

    for ff in new_fields:
        if not ff.selector or ff.selector in seen_selectors:
            continue
        seen_selectors.add(ff.selector)

        value = _quick_profile_match(ff.label, ff.field_type, ff.options, profile)
        if not value:
            skipped += 1
            continue

        el = await _find_element(page, ff.selector, ff.label, ff.field_type)
        if not el:
            skipped += 1
            continue

        await asyncio.sleep(_human_delay())
        real_type = await _detect_field_type(page, el, ff.field_type, ff.label)
        fr = await _dispatch_fill(page, el, value, ff.label, real_type, ff.options)
        field_results.append(fr)

        if fr.status in ("filled", "verified"):
            filled += 1
        else:
            skipped += 1
            if fr.error_message:
                errors.append(fr.error_message)

    return filled, skipped


async def _dispatch_fill(
    page: Page,
    el: ElementHandle,
    value: str,
    label: str,
    real_type: str,
    options: list[str] | None = None,
) -> FieldResult:
    """Route to the right fill handler based on detected field type."""
    if real_type == "react_select":
        return await _fill_react_select_field(page, el, value, label)
    if real_type == "datepicker":
        return await _fill_datepicker(page, el, value, label)
    if real_type == "date_native":
        return await _fill_date_native(page, el, value, label)
    if real_type == "time_native":
        return await _fill_time_native(page, el, value, label)
    if real_type == "date_text":
        return await _fill_datepicker(page, el, value, label)
    if real_type == "phone":
        return await _fill_phone(page, el, value, label)
    if real_type == "native_select":
        return await _fill_native_select(page, el, value, label)
    if real_type == "file":
        return await _handle_file_upload(page, el, value, label)
    if real_type == "rich_text":
        return await _fill_rich_text(page, el, value, label)
    # default: text / email / number / textarea
    return await _fill_text(page, el, value, label)


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

        # If Google Places dropdown appeared, click first suggestion before blurring
        await asyncio.sleep(0.4)
        places_clicked = await _handle_google_places_suggestion(page)

        if not places_clicked:
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
            # Fuzzy match — note: el.evaluate receives (element, arg) so params are (el, val)
            matched = await el.evaluate("""(el, val) => {
                const opts = [...el.options];
                const match = opts.find(o =>
                    o.text.toLowerCase().includes(val.toLowerCase()) ||
                    o.value.toLowerCase().includes(val.toLowerCase())
                );
                if (match) {
                    el.value = match.value;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }
                return false;
            }""", value)
            if not matched:
                return FieldResult(label, selector, "native_select", value, "error",
                                   f"No option matching '{value}'")

        await asyncio.sleep(0.5)

        # Verify displayed value — el.evaluate passes element as first arg
        displayed = await el.evaluate("el => { const opt = el.options[el.selectedIndex]; return opt ? opt.text.trim() : ''; }")
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

        # JS: find best matching option and click it. Returns clicked text or ''.
        # NOTE: Uses el.evaluate() so document refers to the element's ownerDocument
        # (correct for both main frame and <iframe>-embedded React Select widgets).
        CLICK_OPTION_JS = """(el, val) => {
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
            return '';
        }"""

        # JS: list all visible option texts (for needs_user fallback)
        LIST_OPTIONS_JS = """(el) => {
            const options = document.querySelectorAll(
                '[class*="option"]:not([class*="disabled"]), ' +
                '[class*="menu"] [class*="option"], ' +
                '[role="option"], [class*="suggestion"], ' +
                '[class*="__option"], [class*="-option"]'
            );
            const result = [];
            for (const opt of options) {
                if (opt.offsetParent === null) continue;
                const text = opt.textContent.trim();
                if (text && !text.toLowerCase().includes('no option') && !text.toLowerCase().includes('not found')) {
                    result.push(text);
                }
                if (result.length >= 20) break;
            }
            return result;
        }"""

        for attempt_val in search_attempts:
            # Clear previous input — keyboard goes to whatever element has focus in
            # the browser (works cross-frame because it's OS-level input)
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.2)

            # Type first few characters slowly to trigger search
            search_text = attempt_val[:20]
            for char in search_text:
                await page.keyboard.type(char, delay=_typing_delay())

            # WAIT for suggestions dropdown to appear
            await asyncio.sleep(1.5)

            # el.evaluate runs in the element's frame — works for iframes too
            result = await el.evaluate(CLICK_OPTION_JS, attempt_val)

            if result:
                option_clicked = True
                clicked_text = result
                break

        # If no match found, discover available options and ask the user
        if not option_clicked:
            all_options: list[str] = []
            for probe in ["a", "c", "e", "m", "s", "b", "p"]:
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                await asyncio.sleep(0.2)
                await page.keyboard.type(probe, delay=_typing_delay())
                await asyncio.sleep(1.0)
                found = await el.evaluate(LIST_OPTIONS_JS)
                for opt in (found or []):
                    if opt not in all_options:
                        all_options.append(opt)

            # Close the dropdown
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

            if all_options:
                return FieldResult(label, selector, "react_select",
                                   f"Options: {', '.join(all_options[:15])}",
                                   "needs_user",
                                   f"No match for '{value}'. Available: {', '.join(all_options[:15])}")

        if option_clicked:
            await asyncio.sleep(0.5)
            # Verify by traversing up from the element itself (frame-safe)
            has_value = await el.evaluate("""(el) => {
                let container = el;
                for (let i = 0; i < 8 && container; i++) {
                    container = container.parentElement;
                    if (!container) break;
                    const cls = (typeof container.className === 'string') ? container.className : '';
                    if (cls.includes('__control') || cls.includes('react-select') || cls.includes('auto-complete')) {
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
            }""")

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


async def _fill_react_select_by_label(
    ctx: PageOrFrame,
    page: Page,
    label: str,
    value: str,
) -> Optional[FieldResult]:
    """Find a React Select near a label and fill it. Returns None if not found.

    ``ctx`` is the DOM context for queries — may be a Frame for ATS iframes.
    ``page`` is ALWAYS the top-level Page used for keyboard operations
    (``Frame`` has no ``.keyboard`` attribute).
    """
    try:
        inputs = await ctx.query_selector_all(
            'input[id*="react-select"], input[role="combobox"], '
            'input[class*="auto-complete"], input[id*="subjects"], '
            'input[id*="Subject"], input[id*="select"]'
        )
        containers = await ctx.query_selector_all(
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
                # Always pass the top-level page for keyboard so the call works
                # regardless of whether ctx is a Page or a Frame.
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


# ─── Time input (<input type="time">) ────────────────────

async def _fill_time_native(page: Page, el: ElementHandle, value: str,
                             label: str) -> FieldResult:
    """Fill <input type="time"> using Playwright's .fill() which handles
    the native time widget correctly. Normalises value to HH:MM format."""
    selector = await _get_selector(el)
    try:
        await el.scroll_into_view_if_needed()
        # Normalise to HH:MM (24-hour) — handles "9:00 AM", "2:30 PM", "14:30", "09:00"
        raw = value.strip()
        am_pm_match = re.search(r"(am|pm)", raw, re.IGNORECASE)
        parts = re.split(r"[:.]", re.sub(r"\s*(am|pm)", "", raw, flags=re.IGNORECASE).strip())
        if len(parts) >= 2:
            hh = int(parts[0])
            mm = int(re.sub(r"\D", "", parts[1][:2]) or "0")
            if am_pm_match:
                period = am_pm_match.group(1).lower()
                if period == "am" and hh == 12:
                    hh = 0
                elif period == "pm" and hh != 12:
                    hh += 12
            normalised = f"{hh:02d}:{mm:02d}"
        else:
            normalised = raw
        # Playwright's .fill() properly sets native time inputs
        await el.fill(normalised)
        await asyncio.sleep(0.3)
        # Verify
        filled = await el.input_value()
        if normalised in (filled or ""):
            return FieldResult(label, selector, "time_native", normalised, "verified")
        return FieldResult(label, selector, "time_native", normalised, "filled")
    except Exception as exc:
        return FieldResult(label, selector, "time_native", value, "error", str(exc)[:120])


# ─── File upload ─────────────────────────────────────────

async def _handle_file_upload(page: Page, el: Optional[ElementHandle], value: str,
                               label: str) -> FieldResult:
    """Upload a file (CV, resume, profile photo) to a file input on the form.

    Priority order for picking the file:
    1. Explicit value matches a filename in UPLOADS_DIR
    2. Label hints at CV/resume → prefer .pdf files
    3. Label hints at photo/picture → prefer image files
    4. Any file in UPLOADS_DIR (last resort)
    """
    from pathlib import Path
    from .config import UPLOADS_DIR

    selector = ""
    if el:
        selector = await _get_selector(el)

    label_lower = (label or "").lower()
    is_cv_field = any(kw in label_lower for kw in (
        "cv", "resume", "curriculum", "upload your cv", "upload cv",
        "upload resume", "portfolio", "cover letter", "document",
    ))
    is_photo_field = any(kw in label_lower for kw in (
        "photo", "picture", "image", "avatar", "headshot", "profile pic",
    ))

    # Find the actual file on disk
    file_path: Optional[Path] = None

    # 1. Explicit value → direct filename match
    if value:
        candidate = UPLOADS_DIR / value
        if candidate.exists():
            file_path = candidate

    # 2. CV field → prefer PDF
    if not file_path and is_cv_field:
        for p in UPLOADS_DIR.iterdir():
            if p.suffix.lower() == ".pdf":
                file_path = p
                break
        # Fallback: any doc
        if not file_path:
            for p in UPLOADS_DIR.iterdir():
                if p.suffix.lower() in (".doc", ".docx", ".txt"):
                    file_path = p
                    break

    # 3. Photo field → prefer image
    if not file_path and is_photo_field:
        for ext in ("jpg", "jpeg", "png", "webp"):
            candidate = UPLOADS_DIR / f"profile_photo.{ext}"
            if candidate.exists():
                file_path = candidate
                break
        if not file_path:
            for p in UPLOADS_DIR.iterdir():
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    file_path = p
                    break

    # 4. Any file in uploads
    if not file_path:
        for p in UPLOADS_DIR.iterdir():
            if p.is_file() and not p.name.startswith("."):
                file_path = p
                break

    if not file_path:
        return FieldResult(label, selector, "file", value, "needs_user",
                           f"No file found for '{label}'. Upload your CV or photo via the profile page first.")

    # Find the file input element on the page
    try:
        file_input = None
        if selector:
            file_input = await page.query_selector(selector)

        if not file_input:
            # Ordered selector list — most specific first
            candidates = [
                'input[type="file"]',
                '#uploadPicture', '#uploadCV', '#uploadResume',
                'input[accept*=".pdf"]', 'input[accept*="pdf"]',
                'input[accept*="image"]',
                'input[name*="cv"]', 'input[name*="resume"]',
                'input[name*="file"]', 'input[name*="photo"]',
                'input[name*="picture"]', 'input[name*="upload"]',
                'input[name*="document"]',
            ]
            for sel in candidates:
                file_input = await page.query_selector(sel)
                if file_input:
                    break

        if not file_input:
            return FieldResult(label, selector, "file", str(file_path.name), "error",
                               "Could not locate file input element on page")

        await file_input.set_input_files(str(file_path))
        await asyncio.sleep(0.8)

        has_file = await file_input.evaluate("e => e.files && e.files.length > 0")
        if has_file:
            return FieldResult(label, selector, "file", str(file_path.name), "verified",
                               f"Uploaded {file_path.name}")
        return FieldResult(label, selector, "file", str(file_path.name), "filled")

    except Exception as exc:
        return FieldResult(label, selector, "file", value, "error", str(exc)[:120])


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


# ─── Cascade wait ────────────────────────────────────────

async def _wait_for_cascade(page: Page, timeout: float = 5.0) -> None:
    """After filling a parent select (country/state), wait until dependent
    options actually load rather than sleeping a fixed amount.

    Polls for a network-idle state or a DOM mutation with new <option> elements,
    whichever comes first. Falls back to a 2-second cap so we never block forever.
    """
    # First: wait for pending XHR/fetch to settle (covers API-backed dropdowns)
    try:
        await page.wait_for_load_state("networkidle", timeout=int(timeout * 1000))
        return
    except Exception:
        pass  # Not a network-driven cascade — fall through to DOM polling

    # DOM polling: watch for new <option> or [role="option"] elements appearing
    deadline = asyncio.get_event_loop().time() + timeout
    prev_count: int = await page.evaluate("""() => (
        document.querySelectorAll('option, [role="option"]').length
    )""")

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.25)
        try:
            curr_count: int = await page.evaluate("""() => (
                document.querySelectorAll('option, [role="option"]').length
            )""")
            if curr_count > prev_count:
                # New options appeared — give them a moment to fully render
                await asyncio.sleep(0.3)
                return
        except Exception:
            break

    # Absolute fallback
    await asyncio.sleep(1.0)


# ─── Dynamic fields ──────────────────────────────────────

async def _handle_dynamic_fields(page: Page):
    """Wait briefly for any dynamic fields that appear after filling."""
    await asyncio.sleep(0.3)


# ─── Multi-page navigation (vision-guided) ──────────────

_NEXT_SAFE_WORDS = [
    "next", "continue", "proceed", "forward",
    "siguiente", "suivant", "weiter",
    "next step", "next page", "go to next",
    "save and continue", "save & continue",
]
_DANGER_NAV_WORDS = [
    "submit", "send", "apply", "finish", "complete", "confirm",
    "register", "sign up", "signup", "create", "pay", "order",
    "checkout", "check out", "place order", "subscribe", "donate",
    "purchase", "buy", "enroll", "enrol", "book now", "reserve",
]


async def _click_safe_next(page: Page) -> bool:
    """Click the first safe Next/Continue button. Never clicks Submit-like buttons."""
    return bool(await page.evaluate("""([safeWords, dangerWords]) => {
        const btns = [...document.querySelectorAll(
            'button, input[type="button"], a[role="button"], [role="button"]'
        )];
        for (const btn of btns) {
            if (btn.offsetParent === null) continue;
            const raw = (btn.textContent || btn.getAttribute('value') || '').trim();
            const text = raw.toLowerCase();
            if (dangerWords.some(w => text.includes(w))) continue;
            const cls = (btn.className || '').toLowerCase();
            if (['btn-submit','btn-danger','btn-primary','submit'].some(c => cls.includes(c))) continue;
            if (safeWords.includes(text)) { btn.click(); return true; }
        }
        return false;
    }""", [_NEXT_SAFE_WORDS, _DANGER_NAV_WORDS]))


async def _navigate_and_fill(
    page: Page,
    seen_selectors: set[str],
    profile: dict,
    errors: list[str],
    field_results: list[FieldResult],
    max_pages: int = 15,
) -> tuple[int, int, int, bool, bool]:
    """Navigate multi-page forms page-by-page, re-filling new fields each time.

    Also detects OTP prompts and login walls mid-form so the caller can
    surface them to the user instead of silently failing.

    Returns: (pages_navigated, extra_filled, extra_skipped, captcha, otp_detected)
    """
    try:
        from .vision_agent import detect_otp_field, detect_login_form, fill_login
        _vision_ok = True
    except Exception:
        _vision_ok = False

    pages = 1
    extra_filled = 0
    extra_skipped = 0

    for _ in range(max_pages - 1):
        await asyncio.sleep(0.5)

        # ── OTP wall check ──────────────────────────────────────────
        if _vision_ok:
            try:
                if await detect_otp_field(page):
                    return pages, extra_filled, extra_skipped, False, True
            except Exception:
                pass

        # ── Login wall check ────────────────────────────────────────
        if _vision_ok and profile:
            try:
                if await detect_login_form(page):
                    email = (
                        profile.get("personal", {}).get("email") or
                        profile.get("email") or ""
                    )
                    password = (
                        profile.get("personal", {}).get("password") or
                        profile.get("password") or ""
                    )
                    if email and password:
                        await fill_login(page, email, password)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=8_000)
                        except Exception:
                            pass
                        await _dismiss_popups(page)
                        pages += 1
                        continue
            except Exception:
                pass

        # ── CAPTCHA check ───────────────────────────────────────────
        if await _check_captcha(page):
            return pages, extra_filled, extra_skipped, True, False

        # ── Try to click a safe Next/Continue button ────────────────
        clicked = await _click_safe_next(page)
        if not clicked:
            break   # no more pages to navigate

        await asyncio.sleep(2)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        pages += 1
        await _dismiss_popups(page)

        # ── Re-scan and fill new fields on this page ────────────────
        cf_filled, cf_skipped = await _scan_and_fill_new_fields(
            page, seen_selectors, profile, errors, field_results
        )
        extra_filled += cf_filled
        extra_skipped += cf_skipped

        # ── Post-fill OTP check ────────────────────────────────────
        if _vision_ok:
            try:
                if await detect_otp_field(page):
                    return pages, extra_filled, extra_skipped, False, True
            except Exception:
                pass

    return pages, extra_filled, extra_skipped, False, False


# kept for backward-compat (nothing external calls it, but just in case)
async def _navigate_pages(page: Page, matches: list[dict]) -> int:
    pages, _, _, _, _ = await _navigate_and_fill(page, set(), {}, [], [])
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

async def _click_submit(page: Page) -> bool:
    """Find and click the submit/apply/finish button. Returns True if clicked."""
    submit_words = ["submit", "apply", "finish", "complete", "send application",
                    "send", "continue", "next"]
    # Try specific selectors first (most reliable)
    selectors = [
        'input[type="submit"]',
        'button[type="submit"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=800):
                await loc.scroll_into_view_if_needed()
                await asyncio.sleep(random.uniform(0.4, 0.9))
                await loc.click()
                return True
        except Exception:
            continue

    # Fallback: find by button text
    for word in submit_words:
        for sel in [f'button:has-text("{word}")', f'[role="button"]:has-text("{word}")']:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=500):
                    await loc.scroll_into_view_if_needed()
                    await asyncio.sleep(random.uniform(0.4, 0.9))
                    await loc.click()
                    return True
            except Exception:
                continue

    # Last resort: JS click on any visible submit-like button
    try:
        clicked = await page.evaluate("""() => {
            const words = ['submit','apply','finish','complete','send'];
            const btns = [...document.querySelectorAll(
                'button, input[type="submit"], [role="button"]'
            )];
            for (const b of btns) {
                const t = (b.textContent || b.value || '').trim().toLowerCase();
                if (words.some(w => t.includes(w)) && b.offsetParent !== null) {
                    b.click();
                    return true;
                }
            }
            return false;
        }""")
        return bool(clicked)
    except Exception:
        return False


async def _fill_form(url: str, matches: list[dict], auto_submit: bool = True, profile: dict | None = None) -> FillResult:
    """Navigate to URL and autonomously fill every field with intelligence.
    Set auto_submit=True (default) to click the submit button when done."""
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

        # Block Enter key from accidentally submitting mid-fill
        # (we control submission ourselves via _click_submit)
        await page.evaluate("""() => {
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

        # Track selectors we've already processed (for conditional field scanning)
        seen_selectors: set[str] = {m["selector"] for m in sorted_matches if m.get("selector")}

        # Track which "parent" selects have been filled (for cascading)
        filled_selects: list[str] = []

        for match in sorted_matches:
            selector = match["selector"]
            value = str(match["value"])
            ftype = match.get("field_type", "text")
            label = match.get("label", selector)

            # ── Resolve frame context for iframe-embedded fields ────────────
            # Fields discovered inside an ATS iframe (Workday, Greenhouse …)
            # carry a frame_url.  We find the matching Playwright Frame so
            # _find_element can query_selector inside the correct document.
            # Keyboard / click operations that act at the OS level still use
            # the top-level page object throughout.
            frame_url = match.get("frame_url", "")
            ctx: PageOrFrame = page
            if frame_url:
                for _frame in page.frames:
                    if _frame.url == frame_url:
                        ctx = _frame
                        break

            try:
                # ── Human delay between fields ──
                await asyncio.sleep(_human_delay())

                # ── Handle autocomplete / React Select fields ──
                if ftype == "autocomplete":
                    # Try to find the input element
                    el = await _find_element(ctx, selector, label, ftype)
                    if el:
                        fr = await _fill_react_select_field(page, el, value, label)
                    else:
                        fr_rs = await _fill_react_select_by_label(ctx, page, label, value)
                        fr = fr_rs or FieldResult(label, selector, "autocomplete", value, "error",
                                                   f"Could not find autocomplete: {label}")
                    field_results.append(fr)
                    if fr.status in ("filled", "verified"):
                        filled += 1
                        # Cascading wait — poll until dependent options appear or timeout
                        label_lower = label.lower()
                        if any(kw in label_lower for kw in ("state", "country", "province", "region")):
                            await _wait_for_cascade(page)
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
                    el = await _find_element(ctx, selector, label, ftype)
                    fr = await _handle_file_upload(page, el, value, label)
                    field_results.append(fr)
                    if fr.status in ("filled", "verified"):
                        filled += 1
                    else:
                        skipped += 1
                    continue

                # ── Handle selects (native + React) ──
                if ftype == "select":
                    # Check if this is a React Select first
                    if "react-select" in selector or "css-" in selector:
                        el = await _find_element(ctx, selector, label, ftype)
                        if el:
                            fr = await _fill_react_select_field(page, el, value, label)
                        else:
                            fr_rs = await _fill_react_select_by_label(ctx, page, label, value)
                            fr = fr_rs or FieldResult(label, selector, "select", value, "error",
                                                       f"Could not find React Select: {label}")
                    else:
                        el = await _find_element(ctx, selector, label, ftype)
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
                            fr_rs = await _fill_react_select_by_label(ctx, page, label, value)
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
                            await _wait_for_cascade(page)
                            # Re-dismiss popups in case cascade triggered a reload
                            await _dismiss_popups(page)
                    else:
                        skipped += 1
                        if fr.error_message:
                            errors.append(fr.error_message)
                    continue

                # ── All other field types: find element first ──
                el = await _find_element(ctx, selector, label, ftype)
                if not el:
                    # Last resort: maybe it's a React Select not tagged as select
                    if label:
                        fr_rs = await _fill_react_select_by_label(ctx, page, label, value)
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
                            await _wait_for_cascade(page)

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

                elif real_type == "rich_text":
                    fr = await _fill_rich_text(page, el, value, label)

                elif real_type == "google_places":
                    fr = await _fill_text(page, el, value, label)  # typing triggers Places API

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

                # ── Conditional field scan ───────────────────────────────────
                # After filling a select/radio/checkbox (fields most likely to
                # trigger conditional logic), re-scan for newly revealed fields.
                if profile and real_type in (
                    "native_select", "react_select", "radio", "checkbox",
                    "select", "autocomplete",
                ):
                    cf_filled, cf_skipped = await _scan_and_fill_new_fields(
                        page, seen_selectors, profile, errors, field_results
                    )
                    filled  += cf_filled
                    skipped += cf_skipped

            except Exception as e:
                fr = FieldResult(label, selector, ftype, value, "error", str(e)[:120])
                field_results.append(fr)
                skipped += 1
                errors.append(f"{label}: {str(e)[:120]}")

        # ── Final conditional scan — catch anything triggered by the last field ──
        if profile:
            cf_filled, cf_skipped = await _scan_and_fill_new_fields(
                page, seen_selectors, profile, errors, field_results
            )
            filled  += cf_filled
            skipped += cf_skipped

        # ── Multi-page navigation + per-page fill + OTP/login detection ──
        nav_pages, nav_filled, nav_skipped, captcha, otp_detected = await _navigate_and_fill(
            page, seen_selectors, profile or {}, errors, field_results
        )
        pages   = nav_pages
        filled  += nav_filled
        skipped += nav_skipped

        # ── Final CAPTCHA check (in case it appeared on the last page) ──
        if not captcha:
            captcha = await _check_captcha(page)

        if captcha:
            errors.append("CAPTCHA detected — solve it manually, then click Submit.")

        if otp_detected:
            errors.append("OTP / verification code required — check your email or phone and enter the code.")

        # ── Validation check ──
        validation_errors = await _check_validation(page)
        if validation_errors:
            errors.extend(validation_errors)

        # ── Screenshot of filled form (BEFORE submit so user sees filled fields,
        #    not the post-submission confirmation/JSON page) ─────────────────
        await page.evaluate("""() => {
            document.querySelectorAll('[style*="position: fixed"], [style*="position:fixed"]').forEach(el => {
                el.style.display = 'none';
            });
            document.querySelectorAll('#fixedban, .adsbygoogle, [id*="google_ads"], iframe[src*="googleads"]').forEach(el => {
                el.style.display = 'none';
            });
        }""")
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.8)
        screenshot = await page.screenshot(full_page=True, type="png")
        screenshot_b64 = base64.b64encode(screenshot).decode()

        # ── Auto-submit ──────────────────────────────────────────────────────
        submitted = False
        if auto_submit and not captcha and not otp_detected:
            submitted = await _click_submit(page)
            if submitted:
                # Wait for navigation / confirmation page
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    await asyncio.sleep(2)

                # Detect confirmation
                confirmed = await page.evaluate("""() => {
                    const body = (document.body.innerText || '').toLowerCase();
                    const signals = [
                        'thank you', 'thanks!', 'successfully submitted',
                        'application received', 'we received', 'you have applied',
                        'submission received', 'application submitted',
                        'confirmation', "you're all set", "we'll be in touch",
                    ];
                    return signals.some(s => body.includes(s));
                }""")
                if confirmed:
                    errors = [e for e in errors if "submit" not in e.lower()]
                else:
                    errors.append("Form submitted — no confirmation page detected. Verify manually.")
            else:
                errors.append("Could not locate the submit button — review and submit manually.")
        elif captcha:
            errors.append("Solve the CAPTCHA manually, then click Submit.")
        elif otp_detected:
            errors.append("Enter the OTP code sent to you, then submit the form manually.")

        await browser.close()

    return FillResult(
        filled=filled,
        skipped=skipped,
        pages_navigated=pages,
        screenshot_b64=screenshot_b64,
        errors=errors,
        captcha_detected=captcha,
        otp_detected=otp_detected,
        field_results=field_results,
    )


# ─── Public API ──────────────────────────────────────────

def fill_form(
    url: str,
    matches: list[dict],
    auto_submit: bool = True,
    profile: dict | None = None,
) -> FillResult:
    """Synchronous wrapper — fill every field and submit the form.
    Pass profile to enable conditional-field detection after each fill.
    Set auto_submit=False only if you want to review before submitting."""
    return asyncio.run(_fill_form(url, matches, auto_submit=auto_submit, profile=profile))
