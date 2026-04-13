"""Read form fields from a web page using Playwright.

Handles standard HTML forms, React Select, custom dropdowns, ARIA
components, and other modern form libraries."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from playwright.async_api import async_playwright, Page


@dataclass
class FormField:
    selector: str
    field_type: str
    label: str
    placeholder: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)
    max_length: int | None = None
    context: str = ""


def _humanize_id(raw: str) -> str:
    """Convert camelCase/snake_case/kebab IDs to readable labels.
    e.g. 'firstName' -> 'First Name', 'date_of_birth' -> 'Date Of Birth'
    """
    if not raw:
        return ""
    # Remove common prefixes
    for prefix in ("txt", "input", "field", "frm", "sel", "cb", "chk", "user"):
        if raw.lower().startswith(prefix) and len(raw) > len(prefix):
            rest = raw[len(prefix):]
            if rest[0].isupper() or rest[0] == "_":
                raw = rest
                break
    # camelCase -> spaces
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', raw)
    # snake_case / kebab-case -> spaces
    s = s.replace("_", " ").replace("-", " ")
    # Title case
    s = s.strip().title()
    return s


def _clean_label(raw: str) -> str:
    """Clean up a raw label string."""
    if not raw:
        return ""
    cleaned = raw.replace("*", "").strip()
    cleaned = " ".join(cleaned.split())
    return cleaned[:200]


async def _read_fields(url: str) -> tuple[list[FormField], str]:
    """Navigate to URL and extract all form fields + page context."""
    fields: list[FormField] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        title = await page.title()
        headings = await page.eval_on_selector_all(
            "h1, h2, h3",
            "els => els.map(e => e.textContent.trim()).filter(Boolean).slice(0, 5)"
        )
        page_context = f"Page: {title}. Headings: {'; '.join(headings)}"

        # ── All fields via comprehensive JS extraction ────
        all_field_data = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();

            // Helper: find the closest label for an element
            function findLabel(el) {
                const id = el.id || '';
                const name = el.name || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                const placeholder = el.placeholder || '';

                // 1. <label for="id">
                if (id) {
                    const lbl = document.querySelector('label[for="' + id + '"]');
                    if (lbl) {
                        const text = lbl.textContent.trim().replace(/\\*/g, '');
                        if (text && text.length < 100) return text;
                    }
                }

                // 2. Wrapping <label>
                const parentLabel = el.closest('label');
                if (parentLabel) {
                    // Get label text excluding the input's own text
                    const clone = parentLabel.cloneNode(true);
                    clone.querySelectorAll('input, select, textarea').forEach(c => c.remove());
                    const text = clone.textContent.trim().replace(/\\*/g, '');
                    if (text && text.length < 100) return text;
                }

                // 3. Closest form-group container's label
                const container = el.closest('.form-group, .form-field, [class*="col"], .mb-3, .mb-4, .mt-3');
                if (container) {
                    const lbl = container.querySelector('label, .label, legend');
                    if (lbl && !lbl.contains(el)) {
                        const text = lbl.textContent.trim().replace(/\\*/g, '');
                        if (text && text.length < 100) return text;
                    }
                }

                // 4. Immediate previous sibling that's a label
                let prev = el.previousElementSibling;
                if (!prev && el.parentElement) prev = el.parentElement.previousElementSibling;
                if (prev) {
                    if (['LABEL', 'SPAN', 'P'].includes(prev.tagName)) {
                        const text = prev.textContent.trim().replace(/\\*/g, '');
                        if (text && text.length < 80 && text.length > 1) return text;
                    }
                    const lbl = prev.querySelector && prev.querySelector('label, span');
                    if (lbl) {
                        const text = lbl.textContent.trim().replace(/\\*/g, '');
                        if (text && text.length < 80) return text;
                    }
                }

                // 5. aria-label
                if (ariaLabel) return ariaLabel;

                // 6. placeholder
                if (placeholder) return placeholder;

                // 7. Don't return name/id directly — let Python humanize it
                return '';
            }

            // ── Standard inputs ──────────────────────────
            document.querySelectorAll('input, textarea, select').forEach(el => {
                const type = el.type || (el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select' : 'text');
                if (['hidden', 'submit', 'button', 'reset'].includes(type)) return;
                if (type === 'radio' || type === 'checkbox') return; // handled separately
                const id = el.id || '';
                if (id && id.includes('react-select')) return; // handled separately

                const key = id || el.name || '';
                if (seen.has(key) && key) return;
                if (key) seen.add(key);

                const label = findLabel(el);
                const tag = el.tagName.toLowerCase();
                let selector = '';
                if (id) selector = '#' + id;
                else if (el.name) selector = tag + '[name="' + el.name + '"]';
                else selector = '';

                const options = [];
                if (tag === 'select') {
                    el.querySelectorAll('option').forEach(o => {
                        const t = o.textContent.trim();
                        if (t) options.push(t);
                    });
                }

                results.push({
                    selector: selector,
                    field_type: type,
                    label: label,
                    id: id,
                    name: el.name || '',
                    placeholder: el.placeholder || '',
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    options: options,
                    max_length: el.maxLength > 0 ? el.maxLength : null
                });
            });

            // ── Radio/checkbox groups ────────────────────
            const radioGroups = {};
            document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(el => {
                const name = el.name;
                if (!name) return;
                if (!radioGroups[name]) {
                    radioGroups[name] = {
                        type: el.type,
                        options: [],
                        label: '',
                        required: el.required || el.getAttribute('aria-required') === 'true'
                    };
                    // Find group label
                    const fieldset = el.closest('fieldset');
                    if (fieldset) {
                        const legend = fieldset.querySelector('legend');
                        if (legend) radioGroups[name].label = legend.textContent.trim();
                    }
                    if (!radioGroups[name].label) {
                        const parent = el.closest('.form-group, .col, .mb-3, .custom-control, [class*="col"]');
                        if (parent) {
                            const lbl = parent.parentElement?.querySelector('label, .label, [class*="label"]');
                            if (lbl && !lbl.contains(el)) {
                                radioGroups[name].label = lbl.textContent.trim().replace(/\\*/g, '');
                            }
                        }
                    }
                }
                const lbl = el.labels?.[0]?.textContent?.trim() || el.value;
                if (lbl) radioGroups[name].options.push(lbl);
            });

            for (const [name, info] of Object.entries(radioGroups)) {
                results.push({
                    selector: 'input[name="' + name + '"]',
                    field_type: info.type,
                    label: info.label || '',
                    id: '',
                    name: name,
                    placeholder: '',
                    required: info.required,
                    options: info.options,
                    max_length: null
                });
            }

            // ── React Select / custom dropdowns ──────────
            const reactContainers = document.querySelectorAll('[class*="react-select"], [id*="react-select"]');
            const reactSeen = new Set();

            reactContainers.forEach(container => {
                // Find the wrapper (usually has a specific class pattern)
                const wrapper = container.closest('[class*="wrapper"], [class*="container"], .form-group, .col, .mb-3, [class*="col"]') || container;
                const wrapperKey = wrapper.getAttribute('id') || wrapper.className.slice(0, 50);
                if (reactSeen.has(wrapperKey) && wrapperKey) return;
                if (wrapperKey) reactSeen.add(wrapperKey);

                const input = container.querySelector('input') || container.querySelector('[role="combobox"]');
                if (!input) return;

                let label = '';

                // Look for label in parent containers
                let searchNode = container;
                for (let i = 0; i < 6; i++) {
                    searchNode = searchNode.parentElement;
                    if (!searchNode) break;

                    // Direct label child
                    const lbl = searchNode.querySelector(':scope > label, :scope > .label');
                    if (lbl) {
                        label = lbl.textContent.trim().replace(/\\*/g, '');
                        break;
                    }

                    // Previous sibling label
                    const prev = searchNode.previousElementSibling;
                    if (prev && ['LABEL', 'P', 'SPAN', 'DIV'].includes(prev.tagName)) {
                        const text = prev.textContent.trim().replace(/\\*/g, '');
                        if (text && text.length < 80 && text.length > 1) {
                            label = text;
                            break;
                        }
                    }

                    // Check if this container has a heading-like element before the react select
                    const children = [...searchNode.children];
                    const containerIdx = children.indexOf(container.closest('[class*="css"]') || container);
                    for (let j = containerIdx - 1; j >= 0; j--) {
                        const text = children[j].textContent?.trim();
                        if (text && text.length < 80 && text.length > 1) {
                            label = text;
                            break;
                        }
                    }
                    if (label) break;
                }

                // Try aria-label
                if (!label) label = input.getAttribute('aria-label') || '';

                // Try placeholder
                const phEl = container.querySelector('[class*="placeholder"]');
                const placeholder = input.placeholder || (phEl ? phEl.textContent.trim() : '');
                if (!label && placeholder) label = placeholder;

                // Get options if menu is visible
                const options = [];
                const menu = container.querySelector('[class*="menu"]');
                if (menu) {
                    menu.querySelectorAll('[class*="option"]').forEach(opt => {
                        options.push(opt.textContent.trim());
                    });
                }

                const inputId = input.id || '';
                results.push({
                    selector: inputId ? '#' + inputId : 'input[role="combobox"]',
                    field_type: 'select',
                    label: label,
                    id: inputId,
                    name: input.name || '',
                    placeholder: placeholder,
                    required: input.required || input.getAttribute('aria-required') === 'true',
                    options: options,
                    max_length: null
                });
            });

            return results;
        }""")

        # Post-process: ensure every field has a good label
        page_title = title.strip()
        for data in all_field_data:
            label = data.get("label", "")
            el_id = data.get("id", "")
            name = data.get("name", "")

            # If label matches page title, it's wrong — picked up the heading
            if label and label.lower() == page_title.lower():
                label = ""

            # If label is too short (1 char) or empty, try humanizing the id/name
            if not label or len(label) <= 1:
                label = _humanize_id(el_id) or _humanize_id(name) or "Unknown Field"

            # Build selector if missing
            selector = data.get("selector", "")
            if not selector:
                if el_id:
                    selector = f"#{el_id}"
                elif name:
                    selector = f'input[name="{name}"]'

            if not selector:
                continue  # Skip fields with no way to target them

            fields.append(FormField(
                selector=selector,
                field_type=data.get("field_type", "text"),
                label=label,
                placeholder=data.get("placeholder", ""),
                required=data.get("required", False),
                options=data.get("options", []),
                max_length=data.get("max_length"),
            ))

        # Deduplicate labels — if multiple fields have the same label, append context
        label_counts: dict[str, int] = {}
        for f in fields:
            label_counts[f.label] = label_counts.get(f.label, 0) + 1

        label_seen: dict[str, int] = {}
        for f in fields:
            if label_counts[f.label] > 1:
                label_seen[f.label] = label_seen.get(f.label, 0) + 1
                # Try to differentiate using id or placeholder
                el_id = ""
                if f.selector.startswith("#"):
                    el_id = f.selector[1:]
                suffix = _humanize_id(el_id) if el_id else f.placeholder
                if suffix and suffix.lower() != f.label.lower():
                    f.label = f"{f.label} ({suffix})"
                else:
                    f.label = f"{f.label} ({label_seen[f.label]})"

        await browser.close()

    return fields, page_context


def read_form(url: str) -> tuple[list[FormField], str]:
    """Synchronous wrapper for form reading."""
    return asyncio.run(_read_fields(url))
