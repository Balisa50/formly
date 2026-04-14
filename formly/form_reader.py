"""Read form fields from a web page using Playwright.

Handles standard HTML forms, React Select, custom dropdowns, ARIA
components, date pickers, autocomplete/tag inputs, file uploads,
and other modern form libraries."""
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
    depends_on: str = ""


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


def _classify_file_field(label: str) -> str:
    """Return a specific file field_type based on label keywords."""
    lower = label.lower()
    if any(kw in lower for kw in ("photo", "picture", "avatar", "image", "headshot", "selfie")):
        return "file_photo"
    if any(kw in lower for kw in ("cv", "resume", "curriculum")):
        return "file_cv"
    return "file"


async def _scroll_entire_page(page: Page) -> None:
    """Scroll the page top-to-bottom in small increments to trigger lazy-loaded fields."""
    await page.evaluate("""async () => {
        await new Promise(resolve => {
            let totalHeight = 0;
            const distance = 300;
            const timer = setInterval(() => {
                const scrollHeight = document.body.scrollHeight;
                window.scrollBy(0, distance);
                totalHeight += distance;
                if (totalHeight >= scrollHeight) {
                    clearInterval(timer);
                    // Scroll back to top
                    window.scrollTo(0, 0);
                    resolve();
                }
            }, 100);
        });
    }""")
    # Brief wait for any lazy content to finish rendering
    await page.wait_for_timeout(500)


async def _read_fields(url: str) -> tuple[list[FormField], str]:
    """Navigate to URL and extract all form fields + page context."""
    fields: list[FormField] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # ── Scroll the entire page first to trigger lazy-loaded fields ──
        await _scroll_entire_page(page)

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

                // 7. Don't return name/id directly -- let Python humanize it
                return '';
            }

            // Helper: check if an element is inside an autocomplete/tag container
            function isAutocomplete(el) {
                // React Select containers
                const parent = el.closest(
                    '[class*="__control"], [class*="__value-container"], ' +
                    '[class*="auto-complete"], [class*="autocomplete"], ' +
                    '[class*="react-select"], [class*="react-tags"], ' +
                    '[class*="tagsinput"], [class*="token-input"], ' +
                    '[class*="multiselect"], [class*="choices"]'
                );
                if (parent) return true;
                // role="combobox" on the input itself
                if (el.getAttribute('role') === 'combobox') return true;
                // aria-autocomplete attribute
                if (el.getAttribute('aria-autocomplete')) return true;
                return false;
            }

            // Helper: check if an input is inside a date picker
            function isDatePicker(el, label) {
                const parent = el.closest(
                    '[class*="react-datepicker"], [class*="date-picker"], ' +
                    '[class*="datepicker"], [class*="calendar"], ' +
                    '[class*="flatpickr"], [class*="pikaday"], ' +
                    '[class*="date-range"], [class*="daterange"]'
                );
                if (parent) return true;
                if (el.getAttribute('data-datepicker') !== null) return true;
                if (el.getAttribute('data-provide') === 'datepicker') return true;
                // Check label text for date-related keywords
                const lbl = (label || '').toLowerCase();
                if (el.type === 'text' && /\\b(date|birth|dob|d\\.o\\.b|born|expir|deadline)\\b/i.test(lbl)) return true;
                return false;
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

                // Determine field type with enhanced detection
                let fieldType = type;

                // File upload sub-classification handled in Python
                if (type === 'file') {
                    fieldType = 'file';
                }
                // Autocomplete / tag input detection
                else if (isAutocomplete(el)) {
                    fieldType = 'autocomplete';
                }
                // Date picker detection
                else if (type === 'date' || isDatePicker(el, label)) {
                    fieldType = 'datepicker';
                }
                // Phone fields -- keep type but always grab placeholder
                else if (type === 'tel') {
                    fieldType = 'tel';
                }

                results.push({
                    selector: selector,
                    field_type: fieldType,
                    label: label,
                    id: id,
                    name: el.name || '',
                    placeholder: el.placeholder || el.getAttribute('data-placeholder') || '',
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    options: options,
                    max_length: el.maxLength > 0 ? el.maxLength : null,
                    depends_on: ''
                });
            });

            // ── Radio groups ────────────────────────────
            const radioGroups = {};
            document.querySelectorAll('input[type="radio"]').forEach(el => {
                const name = el.name;
                if (!name) return;
                if (!radioGroups[name]) {
                    radioGroups[name] = {
                        type: 'radio',
                        options: [],
                        label: '',
                        required: el.required || el.getAttribute('aria-required') === 'true'
                    };
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
                    max_length: null,
                    depends_on: ''
                });
            }

            // ── Checkbox groups (each option listed individually) ────
            const checkboxGroups = {};
            document.querySelectorAll('input[type="checkbox"]').forEach(el => {
                const name = el.name;
                if (!name) return;
                if (!checkboxGroups[name]) {
                    checkboxGroups[name] = {
                        groupLabel: '',
                        items: [],
                        required: el.required || el.getAttribute('aria-required') === 'true'
                    };
                    const fieldset = el.closest('fieldset');
                    if (fieldset) {
                        const legend = fieldset.querySelector('legend');
                        if (legend) checkboxGroups[name].groupLabel = legend.textContent.trim().replace(/\\*/g, '');
                    }
                    if (!checkboxGroups[name].groupLabel) {
                        const parent = el.closest('.form-group, .col, .mb-3, .custom-control, [class*="col"]');
                        if (parent) {
                            const lbl = parent.parentElement?.querySelector('label, .label, [class*="label"]');
                            if (lbl && !lbl.contains(el)) {
                                checkboxGroups[name].groupLabel = lbl.textContent.trim().replace(/\\*/g, '');
                            }
                        }
                    }
                }
                // Get the individual label for this checkbox option
                let optionLabel = '';
                if (el.labels && el.labels.length > 0) {
                    const clone = el.labels[0].cloneNode(true);
                    clone.querySelectorAll('input').forEach(c => c.remove());
                    optionLabel = clone.textContent.trim();
                }
                if (!optionLabel) optionLabel = el.value || '';
                const optionId = el.id || '';
                checkboxGroups[name].items.push({
                    label: optionLabel,
                    id: optionId,
                    value: el.value || ''
                });
            });

            for (const [name, info] of Object.entries(checkboxGroups)) {
                const allOptions = info.items.map(i => i.label).filter(Boolean);
                results.push({
                    selector: 'input[name="' + name + '"]',
                    field_type: 'checkbox',
                    label: info.groupLabel || '',
                    id: '',
                    name: name,
                    placeholder: '',
                    required: info.required,
                    options: allOptions,
                    max_length: null,
                    depends_on: ''
                });
            }

            // ── React Select / autocomplete dropdowns ──────────
            const reactContainers = document.querySelectorAll(
                '[class*="react-select"], [id*="react-select"], ' +
                '[class*="__control"], [class*="__value-container"]'
            );
            const reactSeen = new Set();

            reactContainers.forEach(container => {
                const wrapper = container.closest('[class*="wrapper"], [class*="container"], .form-group, .col, .mb-3, [class*="col"]') || container;
                const wrapperKey = wrapper.getAttribute('id') || wrapper.className.slice(0, 50);
                if (reactSeen.has(wrapperKey) && wrapperKey) return;
                if (wrapperKey) reactSeen.add(wrapperKey);

                const input = container.querySelector('input') || container.querySelector('[role="combobox"]');
                if (!input) return;
                // Skip if this input was already captured
                const inputKey = input.id || input.name || '';
                if (inputKey && seen.has(inputKey)) return;
                if (inputKey) seen.add(inputKey);

                let label = '';
                let searchNode = container;
                for (let i = 0; i < 6; i++) {
                    searchNode = searchNode.parentElement;
                    if (!searchNode) break;
                    const lbl = searchNode.querySelector(':scope > label, :scope > .label');
                    if (lbl) {
                        label = lbl.textContent.trim().replace(/\\*/g, '');
                        break;
                    }
                    const prev = searchNode.previousElementSibling;
                    if (prev && ['LABEL', 'P', 'SPAN', 'DIV'].includes(prev.tagName)) {
                        const text = prev.textContent.trim().replace(/\\*/g, '');
                        if (text && text.length < 80 && text.length > 1) {
                            label = text;
                            break;
                        }
                    }
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
                if (!label) label = input.getAttribute('aria-label') || '';
                const phEl = container.querySelector('[class*="placeholder"]');
                const placeholder = input.placeholder || (phEl ? phEl.textContent.trim() : '');
                if (!label && placeholder) label = placeholder;

                const options = [];
                const menu = container.querySelector('[class*="menu"]');
                if (menu) {
                    menu.querySelectorAll('[class*="option"]').forEach(opt => {
                        options.push(opt.textContent.trim());
                    });
                }

                const inputId = input.id || '';
                // Determine if this is an autocomplete (multi/tag) or plain select
                const isMulti = container.closest('[class*="multi"]') !== null
                    || container.querySelector('[class*="multi-value"]') !== null;
                const fieldType = isMulti ? 'autocomplete' : 'autocomplete';

                results.push({
                    selector: inputId ? '#' + inputId : 'input[role="combobox"]',
                    field_type: 'autocomplete',
                    label: label,
                    id: inputId,
                    name: input.name || '',
                    placeholder: placeholder,
                    required: input.required || input.getAttribute('aria-required') === 'true',
                    options: options,
                    max_length: null,
                    depends_on: ''
                });
            });

            // ── Custom dropdowns (non-native selects) ──────────
            const customDropdowns = document.querySelectorAll(
                '[role="listbox"], ' +
                'div[class*="dropdown"]:not(nav *):not(header *), ' +
                'div[class*="select-menu"], ' +
                'div[class*="custom-select"]:not(select)'
            );
            const customSeen = new Set();

            customDropdowns.forEach(el => {
                // Skip if it's a React Select container (already handled)
                if (el.closest('[class*="react-select"]')) return;
                // Skip nav/header dropdowns that aren't form fields
                const cls = (el.className || '').toString();
                if (/nav|menu-item|header/i.test(cls)) return;

                const elKey = el.id || cls.slice(0, 60);
                if (customSeen.has(elKey) && elKey) return;
                if (elKey) customSeen.add(elKey);

                // Check if this contains a hidden <select> we already captured
                const hiddenSelect = el.querySelector('select');
                if (hiddenSelect) {
                    const hKey = hiddenSelect.id || hiddenSelect.name || '';
                    if (hKey && seen.has(hKey)) return;
                }

                let label = '';
                // Look for a label in the same container or nearby
                const container = el.closest('.form-group, .form-field, .mb-3, .mb-4, [class*="col"]');
                if (container) {
                    const lbl = container.querySelector('label, .label, legend');
                    if (lbl && !el.contains(lbl)) {
                        label = lbl.textContent.trim().replace(/\\*/g, '');
                    }
                }
                if (!label) {
                    const prev = el.previousElementSibling;
                    if (prev && ['LABEL', 'SPAN', 'P'].includes(prev.tagName)) {
                        label = prev.textContent.trim().replace(/\\*/g, '');
                    }
                }
                if (!label) label = el.getAttribute('aria-label') || '';

                // Gather visible options
                const options = [];
                el.querySelectorAll('[role="option"], li, .option, [class*="option"]').forEach(opt => {
                    const t = opt.textContent.trim();
                    if (t && t.length < 100) options.push(t);
                });

                const selector = el.id ? '#' + el.id : '';
                if (!selector && !label) return; // Can't target or identify

                results.push({
                    selector: selector || '[role="listbox"]',
                    field_type: 'custom_select',
                    label: label,
                    id: el.id || '',
                    name: '',
                    placeholder: el.getAttribute('data-placeholder') || '',
                    required: el.getAttribute('aria-required') === 'true',
                    options: options,
                    max_length: null,
                    depends_on: ''
                });
            });

            // ── Standalone combobox inputs not yet captured ──────
            document.querySelectorAll('[role="combobox"]').forEach(el => {
                const id = el.id || '';
                const name = el.getAttribute('name') || '';
                const key = id || name || '';
                if (key && seen.has(key)) return;
                if (key) seen.add(key);

                const label = findLabel(el);
                let selector = '';
                if (id) selector = '#' + id;
                else selector = '[role="combobox"]';

                results.push({
                    selector: selector,
                    field_type: 'autocomplete',
                    label: label,
                    id: id,
                    name: name,
                    placeholder: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '',
                    required: el.getAttribute('aria-required') === 'true',
                    options: [],
                    max_length: null,
                    depends_on: ''
                });
            });

            // ── Detect cascading/linked dropdown pairs ──────────
            // Look for select/custom-select pairs that share a container
            // and have parent-child naming (e.g. state->city, country->state)
            const cascadePairs = [
                ['country', 'state'], ['country', 'province'], ['country', 'region'],
                ['state', 'city'], ['province', 'city'], ['region', 'city'],
                ['state', 'district'], ['district', 'sub_district'],
                ['category', 'subcategory'], ['category', 'sub_category'],
                ['make', 'model'], ['brand', 'model']
            ];

            for (const item of results) {
                const itemName = (item.name || item.id || item.label || '').toLowerCase()
                    .replace(/[-_ ]/g, '');
                for (const [parent, child] of cascadePairs) {
                    const childNorm = child.replace(/[-_ ]/g, '');
                    const parentNorm = parent.replace(/[-_ ]/g, '');
                    if (itemName.includes(childNorm)) {
                        // Find the parent field
                        const parentField = results.find(r => {
                            const rName = (r.name || r.id || r.label || '').toLowerCase()
                                .replace(/[-_ ]/g, '');
                            return rName.includes(parentNorm);
                        });
                        if (parentField) {
                            item.depends_on = parentField.label || parentField.name || parentField.id || '';
                        }
                        break;
                    }
                }
            }

            return results;
        }""")

        # Post-process: ensure every field has a good label
        page_title = title.strip()
        for data in all_field_data:
            label = data.get("label", "")
            el_id = data.get("id", "")
            name = data.get("name", "")

            # If label matches page title, it's wrong -- picked up the heading
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

            # Classify file fields with more detail based on label
            field_type = data.get("field_type", "text")
            if field_type == "file":
                field_type = _classify_file_field(label)

            fields.append(FormField(
                selector=selector,
                field_type=field_type,
                label=label,
                placeholder=data.get("placeholder", ""),
                required=data.get("required", False),
                options=data.get("options", []),
                max_length=data.get("max_length"),
                depends_on=data.get("depends_on", ""),
            ))

        # Deduplicate labels -- if multiple fields have the same label, append context
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
