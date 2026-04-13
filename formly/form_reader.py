"""Read form fields from a web page using Playwright.

Handles standard HTML forms, React Select, custom dropdowns, ARIA
components, and other modern form libraries."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from playwright.async_api import async_playwright, Page


@dataclass
class FormField:
    selector: str
    field_type: str  # text, email, tel, textarea, select, radio, checkbox, file, number, date, url, password
    label: str
    placeholder: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)
    max_length: int | None = None
    context: str = ""  # nearby text for extra understanding


async def _read_fields(url: str) -> tuple[list[FormField], str]:
    """Navigate to URL and extract all form fields + page context."""
    fields: list[FormField] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Grab page context
        title = await page.title()
        headings = await page.eval_on_selector_all(
            "h1, h2, h3",
            "els => els.map(e => e.textContent.trim()).filter(Boolean).slice(0, 5)"
        )
        page_context = f"Page: {title}. Headings: {'; '.join(headings)}"

        # ── Standard HTML inputs ──────────────────────────
        inputs = await page.query_selector_all(
            "input:not([type='hidden']):not([type='submit']):not([type='button']), "
            "textarea, select"
        )

        for el in inputs:
            try:
                f = await _extract_field(page, el)
                if f:
                    fields.append(f)
            except Exception:
                continue

        # ── Radio/checkbox groups ─────────────────────────
        radio_groups = await _extract_radio_groups(page)
        fields.extend(radio_groups)

        # ── React Select / custom dropdowns ───────────────
        react_selects = await _extract_react_selects(page)
        fields.extend(react_selects)

        # ── ARIA comboboxes and listboxes ─────────────────
        aria_fields = await _extract_aria_fields(page)
        # Avoid duplicates
        existing_selectors = {f.selector for f in fields}
        for af in aria_fields:
            if af.selector not in existing_selectors:
                fields.append(af)

        await browser.close()

    return fields, page_context


def _clean_label(raw: str) -> str:
    """Clean up a raw label string — remove noise, extra whitespace."""
    if not raw:
        return ""
    # Remove asterisks (required markers), extra whitespace
    cleaned = raw.replace("*", "").strip()
    # Collapse whitespace
    cleaned = " ".join(cleaned.split())
    # Limit length
    return cleaned[:200]


async def _find_label(page: Page, el, el_id: str, aria_label: str, placeholder: str, name: str) -> str:
    """Try multiple strategies to find a human-readable label for a field."""
    label = ""

    # Strategy 1: <label for="id">
    if el_id:
        label_el = await page.query_selector(f'label[for="{el_id}"]')
        if label_el:
            label = _clean_label(await label_el.text_content() or "")

    # Strategy 2: Parent <label>
    if not label:
        label = _clean_label(await el.evaluate("""e => {
            const parent = e.closest('label');
            if (parent) return parent.textContent.trim();
            return '';
        }"""))

    # Strategy 3: Nearest preceding label/span/div with text
    if not label:
        label = _clean_label(await el.evaluate("""e => {
            // Walk up and look for label-like elements
            let node = e;
            for (let i = 0; i < 5; i++) {
                node = node.previousElementSibling || node.parentElement;
                if (!node) break;
                if (['LABEL', 'SPAN', 'P', 'DIV'].includes(node.tagName)) {
                    const text = node.textContent?.trim();
                    if (text && text.length < 100 && text.length > 1) return text;
                }
                // Check for a label child
                const lbl = node.querySelector('label, .form-label, [class*="label"]');
                if (lbl) {
                    const text = lbl.textContent?.trim();
                    if (text && text.length < 100) return text;
                }
            }
            return '';
        }"""))

    # Strategy 4: Parent container's first label-like element
    if not label:
        label = _clean_label(await el.evaluate("""e => {
            const container = e.closest('.form-group, .form-field, .field, [class*="form"], [class*="field"], .col, .mb-3, .mb-4');
            if (container) {
                const lbl = container.querySelector('label, .label, [class*="label"], legend, h4, h5');
                if (lbl && lbl !== e) return lbl.textContent.trim();
            }
            return '';
        }"""))

    # Strategy 5: aria-label, aria-labelledby
    if not label and aria_label:
        label = _clean_label(aria_label)

    if not label and el_id:
        labelledby = await el.get_attribute("aria-labelledby")
        if labelledby:
            lbl_el = await page.query_selector(f'#{labelledby}')
            if lbl_el:
                label = _clean_label(await lbl_el.text_content() or "")

    # Strategy 6: placeholder or name as last resort (but humanize it)
    if not label:
        if placeholder:
            label = placeholder
        elif name:
            # Convert camelCase/snake_case to words
            import re
            label = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
            label = label.replace("_", " ").replace("-", " ").title()

    return label


async def _extract_field(page: Page, el) -> FormField | None:
    """Extract metadata from a single form element."""
    tag = await el.evaluate("e => e.tagName.toLowerCase()")
    el_type = await el.get_attribute("type") or ("textarea" if tag == "textarea" else "select" if tag == "select" else "text")
    name = await el.get_attribute("name") or ""
    el_id = await el.get_attribute("id") or ""
    placeholder = await el.get_attribute("placeholder") or ""
    required = await el.get_attribute("required") is not None
    maxlength = await el.get_attribute("maxlength")
    aria_label = await el.get_attribute("aria-label") or ""
    aria_required = await el.get_attribute("aria-required")
    if aria_required == "true":
        required = True

    # Skip radio/checkbox — handled separately as groups
    if el_type in ("radio", "checkbox"):
        return None

    # Skip React Select internal inputs (handled in _extract_react_selects)
    if el_id and "react-select" in el_id:
        return None

    # Find label using all strategies
    label = await _find_label(page, el, el_id, aria_label, placeholder, name)

    # Get surrounding context text
    context = await el.evaluate("""e => {
        const container = e.closest('.form-group, .form-field, .field, [class*="form"], .col, .mb-3');
        if (container) {
            const helpText = container.querySelector('.help-text, .form-text, [class*="help"], [class*="hint"], small');
            if (helpText) return helpText.textContent.trim().slice(0, 200);
        }
        return '';
    }""")

    # Build selector
    if el_id:
        selector = f"#{el_id}"
    elif name:
        selector = f'{tag}[name="{name}"]'
    else:
        selector = await el.evaluate("""e => {
            const idx = [...e.parentElement.children].indexOf(e);
            return e.parentElement.tagName.toLowerCase() + ' > ' + e.tagName.toLowerCase() + ':nth-child(' + (idx+1) + ')';
        }""")

    # Get options for select
    options = []
    if tag == "select":
        options = await el.eval_on_selector_all(
            "option",
            "opts => opts.map(o => o.textContent.trim()).filter(Boolean)"
        )

    return FormField(
        selector=selector,
        field_type=el_type,
        label=label,
        placeholder=placeholder,
        required=required,
        options=options,
        max_length=int(maxlength) if maxlength else None,
        context=context,
    )


async def _extract_radio_groups(page: Page) -> list[FormField]:
    """Extract radio button and checkbox groups."""
    groups: list[FormField] = []

    group_data = await page.evaluate("""() => {
        const groups = {};
        document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(el => {
            const name = el.name;
            if (!name) return;
            if (!groups[name]) {
                groups[name] = { type: el.type, options: [], label: '', required: el.required || el.getAttribute('aria-required') === 'true' };
                // Find group label
                const fieldset = el.closest('fieldset');
                if (fieldset) {
                    const legend = fieldset.querySelector('legend');
                    if (legend) groups[name].label = legend.textContent.trim();
                }
                if (!groups[name].label) {
                    const parent = el.closest('.form-group, .form-field, div, fieldset, section');
                    if (parent) {
                        const heading = parent.querySelector('label, .label, [class*="label"], span, p, h3, h4');
                        if (heading && !heading.querySelector('input')) {
                            groups[name].label = heading.textContent.trim();
                        }
                    }
                }
                // Fallback: humanize the name
                if (!groups[name].label) {
                    groups[name].label = name.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, s => s.toUpperCase());
                }
            }
            const lbl = el.labels?.[0]?.textContent?.trim() || el.value;
            if (lbl) groups[name].options.push(lbl);
        });
        return groups;
    }""")

    for name, info in group_data.items():
        groups.append(FormField(
            selector=f'input[name="{name}"]',
            field_type=info["type"],
            label=_clean_label(info.get("label", name)),
            options=info.get("options", []),
            required=info.get("required", False),
        ))

    return groups


async def _extract_react_selects(page: Page) -> list[FormField]:
    """Detect and extract React Select dropdowns."""
    fields = []

    react_select_data = await page.evaluate("""() => {
        const results = [];
        // React Select containers have specific class patterns
        const containers = document.querySelectorAll(
            '[class*="react-select"], [class*="css-"][class*="container"], [id*="react-select"]'
        );

        const seen = new Set();
        containers.forEach(container => {
            // Find the hidden input or the visible input inside
            const input = container.querySelector('input[id*="react-select"]') ||
                          container.querySelector('input[role="combobox"]') ||
                          container.querySelector('input');
            if (!input) return;

            const inputId = input.id || '';
            if (seen.has(inputId)) return;
            seen.add(inputId);

            // Find label
            let label = '';

            // Check for associated label
            if (inputId) {
                const lbl = document.querySelector(`label[for="${inputId}"]`);
                if (lbl) label = lbl.textContent.trim();
            }

            // Check parent containers for label
            if (!label) {
                const wrapper = container.closest('.form-group, .form-field, [class*="field"], [class*="form"], .col, .mb-3, .mb-4');
                if (wrapper) {
                    const lbl = wrapper.querySelector('label, .label, [class*="label"], legend');
                    if (lbl) label = lbl.textContent.trim();
                }
            }

            // Check preceding siblings
            if (!label) {
                let prev = container.previousElementSibling;
                if (prev && ['LABEL', 'SPAN', 'P', 'H4', 'H5'].includes(prev.tagName)) {
                    label = prev.textContent.trim();
                }
            }

            // aria-label
            if (!label) label = input.getAttribute('aria-label') || '';

            // placeholder
            const placeholder = input.getAttribute('placeholder') || container.querySelector('[class*="placeholder"]')?.textContent?.trim() || '';
            if (!label) label = placeholder;

            // Get current options (if menu is in DOM)
            const options = [];
            const menu = container.querySelector('[class*="menu"]') || document.querySelector('[class*="react-select"][class*="menu"]');
            if (menu) {
                menu.querySelectorAll('[class*="option"]').forEach(opt => {
                    const text = opt.textContent.trim();
                    if (text) options.push(text);
                });
            }

            results.push({
                selector: inputId ? '#' + inputId : 'input[role="combobox"]',
                label: label || 'Selection field',
                placeholder: placeholder,
                options: options,
                required: input.required || input.getAttribute('aria-required') === 'true'
            });
        });
        return results;
    }""")

    for data in react_select_data:
        fields.append(FormField(
            selector=data["selector"],
            field_type="select",
            label=_clean_label(data["label"]),
            placeholder=data.get("placeholder", ""),
            options=data.get("options", []),
            required=data.get("required", False),
            context="React Select dropdown component",
        ))

    return fields


async def _extract_aria_fields(page: Page) -> list[FormField]:
    """Extract ARIA comboboxes, listboxes, and other custom form widgets."""
    fields = []

    aria_data = await page.evaluate("""() => {
        const results = [];
        const seen = new Set();

        // ARIA comboboxes
        document.querySelectorAll('[role="combobox"], [role="listbox"], [role="spinbutton"]').forEach(el => {
            const id = el.id || '';
            if (seen.has(id) && id) return;
            if (id) seen.add(id);

            // Skip if it's inside a React Select (already handled)
            if (el.closest('[class*="react-select"]')) return;

            let label = el.getAttribute('aria-label') || '';
            if (!label && el.getAttribute('aria-labelledby')) {
                const lbl = document.getElementById(el.getAttribute('aria-labelledby'));
                if (lbl) label = lbl.textContent.trim();
            }
            if (!label) {
                const parent = el.closest('.form-group, .form-field, [class*="field"]');
                if (parent) {
                    const lbl = parent.querySelector('label, .label');
                    if (lbl) label = lbl.textContent.trim();
                }
            }

            const options = [];
            // Check for associated listbox
            const listboxId = el.getAttribute('aria-owns') || el.getAttribute('aria-controls');
            if (listboxId) {
                const listbox = document.getElementById(listboxId);
                if (listbox) {
                    listbox.querySelectorAll('[role="option"]').forEach(opt => {
                        options.push(opt.textContent.trim());
                    });
                }
            }

            results.push({
                selector: id ? '#' + id : `[role="${el.getAttribute('role')}"]`,
                label: label || el.getAttribute('placeholder') || 'Custom field',
                type: el.getAttribute('role') === 'spinbutton' ? 'number' : 'select',
                options: options,
                required: el.getAttribute('aria-required') === 'true'
            });
        });
        return results;
    }""")

    for data in aria_data:
        fields.append(FormField(
            selector=data["selector"],
            field_type=data.get("type", "select"),
            label=_clean_label(data["label"]),
            options=data.get("options", []),
            required=data.get("required", False),
            context="ARIA custom form widget",
        ))

    return fields


def read_form(url: str) -> tuple[list[FormField], str]:
    """Synchronous wrapper for form reading."""
    return asyncio.run(_read_fields(url))
