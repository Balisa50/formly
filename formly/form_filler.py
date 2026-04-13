"""Autonomous form-filling agent using Playwright.

Opens any URL in a headless browser and fills every field using the
user's stored profile. Handles dropdowns, React Select, radios,
checkboxes, date pickers, multi-page forms, cookie popups, and
dynamic fields. Uses human-like delays and stealth to avoid detection."""
from __future__ import annotations

import asyncio
import base64
import random
from dataclasses import dataclass, field

from playwright.async_api import async_playwright, Page, BrowserContext


@dataclass
class FillResult:
    filled: int
    skipped: int
    pages_navigated: int
    screenshot_b64: str
    errors: list[str] = field(default_factory=list)
    captcha_detected: bool = False


# ─── Human-like timing ────────────────────────────────

def _human_delay() -> float:
    """Random delay between actions (0.4–2.0s) like a real person."""
    return random.uniform(0.4, 2.0)


def _typing_delay() -> int:
    """Random per-keystroke delay in ms (40–120ms) like real typing."""
    return random.randint(40, 120)


# ─── Main fill engine ─────────────────────────────────

async def _fill_form(url: str, matches: list[dict]) -> FillResult:
    """Navigate to URL and autonomously fill every field."""
    filled = 0
    skipped = 0
    pages = 1
    errors: list[str] = []
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="Europe/London",
        )

        # Remove webdriver flag to avoid detection
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Dismiss cookie popups and modals
        await _dismiss_popups(page)
        await asyncio.sleep(_human_delay())

        # Sort: text first, then selects, then radios/checkboxes
        priority = {"text": 0, "email": 0, "tel": 0, "number": 0, "textarea": 1,
                     "date": 2, "select": 3, "radio": 4, "checkbox": 4}
        sorted_matches = sorted(
            [m for m in matches if m.get("value") and m.get("match_type") != "skipped" and m.get("field_type") != "file"],
            key=lambda m: priority.get(m.get("field_type", "text"), 5),
        )

        for match in sorted_matches:
            selector = match["selector"]
            value = str(match["value"])
            ftype = match.get("field_type", "text")
            label = match.get("label", selector)

            try:
                await asyncio.sleep(_human_delay())

                # Radios and checkboxes use group selectors — don't use _find_element
                if ftype == "radio":
                    ok = await _fill_radio(page, selector, label, value)
                    if ok:
                        filled += 1
                    else:
                        skipped += 1
                        errors.append(f"Could not select radio: {label} = {value}")
                    continue

                if ftype == "checkbox":
                    await _fill_checkbox(page, selector, label, value)
                    filled += 1
                    continue

                # Select/React Select — use dedicated handler
                if ftype == "select":
                    ok = await _fill_select(page, selector, label, value)
                    if ok:
                        filled += 1
                    else:
                        skipped += 1
                        errors.append(f"Could not select: {label} = {value}")
                    continue

                # Text, email, textarea, etc. — find element then type
                el = await _find_element(page, selector, label, ftype)
                if not el:
                    # Last resort: maybe it's a React Select that wasn't tagged as "select"
                    if label:
                        ok = await _fill_react_select_by_label(page, label, value)
                        if ok:
                            filled += 1
                            continue
                    skipped += 1
                    errors.append(f"Could not find: {label}")
                    continue

                # Check if this element is inside a React Select container
                is_react_select = await el.evaluate("""e => {
                    const parent = e.closest('[class*="react-select"], [class*="css-"]');
                    if (parent && parent.querySelector('[class*="indicatorContainer"], [class*="ValueContainer"]')) return true;
                    if (e.getAttribute('role') === 'combobox') return true;
                    if (e.id && e.id.includes('react-select')) return true;
                    return false;
                }""")

                if is_react_select:
                    el_id = await el.get_attribute("id")
                    sel = f"#{el_id}" if el_id else selector
                    ok = await _fill_react_select(page, sel, value)
                    if ok:
                        filled += 1
                    else:
                        skipped += 1
                        errors.append(f"Could not fill React Select: {label}")
                    continue

                if ftype == "date":
                    await _fill_date_element(page, el, value)
                    filled += 1
                else:
                    await _type_into_element(page, el, value)
                    filled += 1

                await _handle_dynamic_fields(page)

            except Exception as e:
                skipped += 1
                errors.append(f"{label}: {str(e)[:120]}")

        # Handle multi-page forms
        pages = await _navigate_pages(page, sorted_matches)

        # Check for CAPTCHA (after filling, before submit)
        captcha = await _check_captcha(page)
        if captcha:
            errors.append("CAPTCHA detected on the form — you'll need to solve it manually when you open the form")

        # Validate — check for required field errors
        validation_errors = await _check_validation(page)
        if validation_errors:
            errors.extend(validation_errors)

        # Hide fixed overlays/ads that block the screenshot
        await page.evaluate("""() => {
            // Remove fixed position elements that cover content (ads, banners)
            document.querySelectorAll('[style*="position: fixed"], [style*="position:fixed"]').forEach(el => {
                el.style.display = 'none';
            });
            // Also hide common ad containers
            document.querySelectorAll('#fixedban, .adsbygoogle, [id*="google_ads"], iframe[src*="googleads"]').forEach(el => {
                el.style.display = 'none';
            });
        }""")

        # Scroll to top and take screenshot
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
    )


# ─── Cookie / popup dismissal ─────────────────────────

async def _dismiss_popups(page: Page):
    """Dismiss cookie consent banners, modals, and overlays."""
    dismiss_selectors = [
        # Cookie consent buttons
        'button:has-text("Accept")', 'button:has-text("Accept All")',
        'button:has-text("I Agree")', 'button:has-text("OK")',
        'button:has-text("Got it")', 'button:has-text("Agree")',
        '[id*="cookie"] button', '[class*="cookie"] button',
        '[id*="consent"] button', '[class*="consent"] button',
        # Modal close buttons
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


# ─── CAPTCHA detection ─────────────────────────────────

async def _check_captcha(page: Page) -> bool:
    """Detect REAL form CAPTCHAs — not just ads or scripts with 'recaptcha' in them."""
    return await page.evaluate("""() => {
        // Only detect visible CAPTCHA challenges, not hidden ad scripts
        const captchaFrame = document.querySelector(
            'iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/api2/bframe"], ' +
            'iframe[src*="hcaptcha.com/captcha"], .g-recaptcha[data-sitekey], .h-captcha[data-sitekey]'
        );
        if (captchaFrame) {
            // Check if it's actually visible (not hidden in ads)
            const rect = captchaFrame.getBoundingClientRect();
            if (rect.width > 50 && rect.height > 50) return true;
        }
        // Check for Cloudflare challenge page
        if (document.title.includes('Just a moment') || document.querySelector('#challenge-running'))
            return true;
        return false;
    }""")


# ─── Smart element finding ─────────────────────────────

async def _find_element(page: Page, selector: str, label: str, ftype: str):
    """Find an element using multiple strategies — CSS selector, label text, name, placeholder."""
    # Strategy 1: Direct CSS selector (try visible first, then any state)
    if selector:
        try:
            el = await page.wait_for_selector(selector, timeout=2000, state="visible")
            if el:
                return el
        except Exception:
            pass
        # Maybe element exists but needs scroll — try without visible requirement
        try:
            el = await page.wait_for_selector(selector, timeout=2000, state="attached")
            if el:
                try:
                    await el.scroll_into_view_if_needed()
                    return el
                except Exception:
                    return el
        except Exception:
            pass

    # Strategy 2: Find by label text using for attribute
    if label:
        try:
            el = await page.evaluate_handle("""(label) => {
                const labels = [...document.querySelectorAll('label')];
                const match = labels.find(l => l.textContent.trim().toLowerCase().includes(label.toLowerCase()));
                if (match && match.htmlFor) {
                    return document.getElementById(match.htmlFor);
                }
                if (match) {
                    return match.querySelector('input, textarea, select');
                }
                return null;
            }""", label)
            el_value = await el.json_value() if el else None
            if el_value is not None:
                return el.as_element()
        except Exception:
            pass

    # Strategy 3: Find by placeholder text
    if label:
        try:
            el = await page.query_selector(f'input[placeholder*="{label}" i], textarea[placeholder*="{label}" i]')
            if el:
                return el
        except Exception:
            pass

    # Strategy 3b: Find textarea/input near a label text on the page
    if label:
        try:
            el_handle = await page.evaluate_handle("""(label) => {
                const allLabels = [...document.querySelectorAll('label, .label, span, p, td, h4, h5, h6')];
                const match = allLabels.find(l => {
                    const text = l.textContent.trim().toLowerCase();
                    return text.includes(label.toLowerCase()) && text.length < 100;
                });
                if (match) {
                    // Look for input/textarea in the same row/container
                    const container = match.closest('tr, .form-group, .form-field, [class*="col"], .row, .mb-3, .mb-4') || match.parentElement;
                    if (container) {
                        const input = container.querySelector('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"]), textarea, select');
                        if (input) return input;
                    }
                    // Walk UP further to find a bigger container
                    const bigContainer = match.closest('tr, .form-group, .form-field, [class*="col"], .row, .mb-3') || match.parentElement?.parentElement;
                    if (bigContainer) {
                        const input = bigContainer.querySelector('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"]), textarea, select');
                        if (input) return input;
                    }
                    // Try next sibling
                    const next = match.nextElementSibling;
                    if (next && ['INPUT', 'TEXTAREA', 'SELECT'].includes(next.tagName)) return next;
                    if (next) {
                        const inp = next.querySelector('input, textarea, select');
                        if (inp) return inp;
                    }
                    // Try parent's next
                    const parentNext = match.parentElement?.nextElementSibling;
                    if (parentNext) {
                        const inp = parentNext.querySelector('input, textarea, select');
                        if (inp) return inp;
                    }
                }
                return null;
            }""", label)
            if el_handle:
                element = el_handle.as_element()
                if element:
                    return element
        except Exception:
            pass

    # Strategy 4: Find by name attribute
    if label:
        name_guess = label.lower().replace(" ", "").replace("_", "")
        try:
            for tag in ["input", "textarea", "select"]:
                el = await page.query_selector(f'{tag}[name*="{name_guess}" i]')
                if el:
                    return el
        except Exception:
            pass

    # Strategy 5: Find by aria-label
    if label:
        try:
            el = await page.query_selector(f'[aria-label*="{label}" i]')
            if el:
                return el
        except Exception:
            pass

    # Strategy 6: Find by ID containing label words
    if label:
        try:
            id_guess = label.lower().replace(" ", "")
            el = await page.query_selector(f'[id*="{id_guess}" i]')
            if el:
                tag = await el.evaluate("e => e.tagName.toLowerCase()")
                if tag in ["input", "textarea", "select"]:
                    return el
        except Exception:
            pass

    return None


# ─── Text typing (human-like, works with element directly) ──

async def _type_into_element(page: Page, el, value: str):
    """Type into an already-found element with human-like speed."""
    await el.scroll_into_view_if_needed()
    await asyncio.sleep(random.uniform(0.1, 0.3))

    # Click to focus
    await el.click()
    await asyncio.sleep(random.uniform(0.1, 0.3))

    # Clear existing content
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Backspace")
    await asyncio.sleep(random.uniform(0.1, 0.2))

    # Type character by character
    for char in value:
        await page.keyboard.type(char, delay=_typing_delay())

    # Tab out to trigger validation
    await page.keyboard.press("Tab")


async def _type_text(page: Page, selector: str, value: str):
    """Legacy: find element by selector and type."""
    el = await _find_element(page, selector, "", "text")
    if not el:
        raise Exception(f"Not found: {selector}")
    await _type_into_element(page, el, value)


# ─── Date fields ──────────────────────────────────────

async def _fill_date_element(page: Page, el, value: str):
    """Fill date input using element directly."""
    await el.scroll_into_view_if_needed()
    input_type = await el.get_attribute("type")

    if input_type == "date":
        await el.fill(value)
    else:
        await el.click()
        await asyncio.sleep(0.2)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        for char in value:
            await page.keyboard.type(char, delay=_typing_delay())
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        await page.keyboard.press("Tab")


async def _fill_date(page: Page, selector: str, value: str):
    """Legacy wrapper."""
    el = await _find_element(page, selector, "", "date")
    if not el:
        raise Exception(f"Not found: {selector}")
    await _fill_date_element(page, el, value)


# ─── Select / Dropdown ────────────────────────────────

async def _fill_select(page: Page, selector: str, label: str, value: str) -> bool:
    """Fill select dropdowns including React Select and custom dropdowns."""
    # Check if React Select
    if "react-select" in selector or "css-" in selector:
        return await _fill_react_select(page, selector, value)

    # Try to find the element
    el = None
    if selector:
        try:
            el = await page.wait_for_selector(selector, timeout=3000, state="visible")
        except Exception:
            pass

    # Fallback: find by label
    if not el and label:
        el = await _find_element(page, "", label, "select")

    # Check if this is actually a React Select (class-based detection)
    if not el and label:
        # Look for React Select containers near this label
        react_input = await page.evaluate("""(label) => {
            const labels = [...document.querySelectorAll('label')];
            const match = labels.find(l => l.textContent.trim().toLowerCase().includes(label.toLowerCase()));
            if (match) {
                const container = match.closest('.form-group, .form-field, [class*="col"], .mb-3') || match.parentElement;
                if (container) {
                    const input = container.querySelector('input[id*="react-select"], input[role="combobox"]');
                    if (input) return input.id || null;
                }
            }
            return null;
        }""", label)
        if react_input:
            return await _fill_react_select(page, f"#{react_input}", value)

    if not el:
        # Last resort: try React Select approach with any visible combobox
        return await _fill_react_select_by_label(page, label, value)

    await el.scroll_into_view_if_needed()
    tag = await el.evaluate("e => e.tagName.toLowerCase()")

    if tag == "select":
        try:
            await el.select_option(label=value)
            return True
        except Exception:
            pass
        try:
            matched = await page.evaluate("""(args) => {
                const [sel, val] = args;
                const select = document.querySelector(sel);
                if (!select) return false;
                const opts = [...select.options];
                const match = opts.find(o => o.text.toLowerCase().includes(val.toLowerCase()));
                if (match) { select.value = match.value; select.dispatchEvent(new Event('change', {bubbles: true})); return true; }
                return false;
            }""", [selector, value])
            return matched
        except Exception:
            return False
    else:
        return await _fill_react_select(page, selector, value)


async def _fill_react_select_by_label(page: Page, label: str, value: str) -> bool:
    """Find a React Select near a label and fill it."""
    try:
        # Find all React Select input fields
        inputs = await page.query_selector_all('input[id*="react-select"], input[role="combobox"]')
        for inp in inputs:
            # Check if this input is near our label
            is_near = await inp.evaluate("""(e, label) => {
                const container = e.closest('.form-group, .form-field, [class*="col"], .mb-3, .mb-4, div') || e.parentElement?.parentElement;
                if (!container) return false;
                const text = container.textContent.toLowerCase();
                return text.includes(label.toLowerCase());
            }""", label)
            if is_near:
                inp_id = await inp.get_attribute("id")
                sel = f"#{inp_id}" if inp_id else 'input[role="combobox"]'
                return await _fill_react_select(page, sel, value)
        return False
    except Exception:
        return False


async def _fill_react_select(page: Page, selector: str, value: str) -> bool:
    """Fill React Select: click to open, type to search, select matching option."""
    try:
        el = await page.wait_for_selector(selector, timeout=5000)
        if not el:
            return False

        await el.scroll_into_view_if_needed()
        await asyncio.sleep(random.uniform(0.2, 0.5))

        # Click to open dropdown
        await el.click()
        await asyncio.sleep(0.5)

        # Type the value to filter options
        for char in value[:20]:  # Type enough chars to filter
            await page.keyboard.type(char, delay=_typing_delay())
        await asyncio.sleep(0.6)

        # Try to find and click matching option
        option = await page.query_selector('[class*="option"]:not([class*="disabled"])')
        if option:
            text = await option.text_content()
            await option.click()
            await asyncio.sleep(0.3)
            return True

        # Fallback: press Enter (selects first match)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.3)
        return True

    except Exception:
        return False


# ─── Radio buttons ─────────────────────────────────────

async def _fill_radio(page: Page, selector: str, label: str, value: str) -> bool:
    """Select the radio button matching the value. Uses multiple strategies.
    Handles hidden radios (opacity:0) with custom label styling (like demoqa)."""
    val_lower = value.lower().strip()

    # Strategy 1: Click a label whose text matches the value
    # This works even when radio inputs are hidden (custom-styled radios)
    try:
        clicked = await page.evaluate("""(value) => {
            const val = value.toLowerCase().trim();
            const labels = [...document.querySelectorAll('label')];
            // Exact match first
            let match = labels.find(l => l.textContent.trim().toLowerCase() === val);
            // Then substring match
            if (!match) match = labels.find(l => {
                const t = l.textContent.trim().toLowerCase();
                return t.includes(val) || val.includes(t);
            });
            if (match) {
                match.scrollIntoView({block: 'center'});
                match.click();
                return true;
            }
            return false;
        }""", value)
        if clicked:
            await asyncio.sleep(0.3)
            return True
    except Exception:
        pass

    # Strategy 2: Find all radios (including hidden ones) and click their labels
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

        if label_text and (val_lower in label_text.lower() or label_text.lower() in val_lower):
            try:
                # Click the associated label (works even when radio is hidden)
                await radio.evaluate("""e => {
                    const label = e.labels?.[0] || document.querySelector('label[for="' + e.id + '"]');
                    if (label) { label.scrollIntoView({block: 'center'}); label.click(); return; }
                    // If no label, force-click the radio via JS
                    e.checked = true;
                    e.dispatchEvent(new Event('change', {bubbles: true}));
                }""")
                await asyncio.sleep(0.3)
                return True
            except Exception:
                pass

    # Strategy 3: Match by value attribute
    for radio in radios:
        radio_val = await radio.get_attribute("value")
        if radio_val and val_lower in radio_val.lower():
            try:
                await radio.evaluate("""e => {
                    const label = e.labels?.[0] || document.querySelector('label[for="' + e.id + '"]');
                    if (label) { label.scrollIntoView({block: 'center'}); label.click(); return; }
                    e.checked = true;
                    e.dispatchEvent(new Event('change', {bubbles: true}));
                }""")
                await asyncio.sleep(0.3)
                return True
            except Exception:
                pass

    # Strategy 4: Use Playwright's label locator
    try:
        label_el = page.get_by_text(value, exact=True)
        if await label_el.count() > 0:
            await label_el.first.scroll_into_view_if_needed()
            await label_el.first.click()
            return True
    except Exception:
        pass

    return False


# ─── Checkboxes ────────────────────────────────────────

async def _fill_checkbox(page: Page, selector: str, label: str, value: str):
    """Check boxes matching comma-separated values."""
    values = [v.strip().lower() for v in value.split(",")]
    checkboxes = await page.query_selector_all(selector) if selector else []
    if not checkboxes:
        checkboxes = await page.query_selector_all('input[type="checkbox"]')

    for cb in checkboxes:
        label_text = await cb.evaluate("""e => {
            if (e.labels && e.labels[0]) return e.labels[0].textContent.trim();
            const next = e.nextElementSibling || e.parentElement;
            return next?.textContent?.trim() || e.value || '';
        }""")

        if label_text and any(v in label_text.lower() for v in values):
            is_checked = await cb.is_checked()
            if not is_checked:
                await cb.scroll_into_view_if_needed()
                await asyncio.sleep(random.uniform(0.2, 0.5))
                try:
                    label_el = await cb.evaluate_handle("""e => e.labels?.[0] || e.parentElement""")
                    await label_el.as_element().click()
                except Exception:
                    await cb.click()


# ─── Dynamic fields ───────────────────────────────────

async def _handle_dynamic_fields(page: Page):
    """Wait briefly for any dynamic fields that appear after filling."""
    await asyncio.sleep(0.3)
    # Check if new fields appeared (some forms show fields conditionally)
    # This is handled by the main loop — new fields from the original match
    # list will be filled when we get to them


# ─── Multi-page navigation ────────────────────────────

async def _navigate_pages(page: Page, matches: list[dict]) -> int:
    """Detect and handle multi-page forms."""
    pages = 1
    max_pages = 10

    while pages < max_pages:
        # Look for Next / Continue buttons
        next_clicked = await page.evaluate("""() => {
            const btns = [...document.querySelectorAll('button, input[type="submit"], a.btn, [role="button"], a[class*="btn"]')];
            const nextWords = ['next', 'continue', 'proceed', 'forward', 'siguiente', 'suivant', 'weiter'];
            for (const btn of btns) {
                const text = (btn.textContent || btn.value || '').trim().toLowerCase();
                const isSubmit = text.includes('submit') || text.includes('finish') || text.includes('complete');
                if (isSubmit) return 'submit_found';
                if (nextWords.some(w => text.includes(w)) && btn.offsetParent !== null) {
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
            # Dismiss any new popups on the new page
            await _dismiss_popups(page)
        else:
            break

    return pages


# ─── Validation checking ──────────────────────────────

async def _check_validation(page: Page) -> list[str]:
    """Check for validation error messages on the form."""
    errors = await page.evaluate("""() => {
        const errs = [];
        // Common error selectors
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


# ─── Public API ────────────────────────────────────────

def fill_form(url: str, matches: list[dict]) -> FillResult:
    """Synchronous wrapper — the main entry point."""
    return asyncio.run(_fill_form(url, matches))
