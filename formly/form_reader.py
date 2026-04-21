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
    # Non-empty when the field lives inside an <iframe> (its frame URL).
    # form_filler uses this to route interactions to the correct Frame object.
    frame_url: str = ""


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


# Known ATS iframe domains — when an iframe's URL contains one of these we
# know we're inside an applicant-tracking system and must scan that frame.
_ATS_DOMAINS = (
    "myworkdayjobs.com", "workday.com",
    "greenhouse.io", "boards.greenhouse.io",
    "lever.co",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "smartrecruiters.com",
    "jobvite.com",
    "recruitee.com",
    "ashbyhq.com",
)


def _postprocess_fields(
    all_field_data: list[dict],
    page_title: str,
    frame_url: str = "",
) -> list[FormField]:
    """Convert raw JS field dicts into typed FormField objects.

    ``frame_url`` is set when the data came from an <iframe> so the filler
    can route interactions to the correct Playwright Frame.
    """
    fields: list[FormField] = []
    dropdown_counter = 0

    for data in all_field_data:
        label = data.get("label", "")
        el_id = data.get("id", "")
        name = data.get("name", "")
        placeholder = data.get("placeholder", "")
        field_type = data.get("field_type", "text")

        if label and label.lower() == page_title.lower():
            label = ""

        react_select_id = bool(re.match(r"^react-select-\d+-input$", el_id or ""))

        if not label or len(label) <= 1:
            if react_select_id:
                if placeholder and len(placeholder) < 80:
                    label = _clean_label(placeholder)
                else:
                    dropdown_counter += 1
                    label = f"Dropdown #{dropdown_counter}"
            else:
                label = (
                    _humanize_id(el_id)
                    or _humanize_id(name)
                    or (placeholder if placeholder and len(placeholder) < 80 else "")
                    or "Unknown Field"
                )

        selector = data.get("selector", "")
        if not selector:
            if el_id:
                selector = f"#{el_id}"
            elif name:
                selector = f'input[name="{name}"]'

        if not selector:
            continue

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
            frame_url=frame_url,
        ))

    # Deduplicate labels — append context suffix when the same label appears twice
    label_counts: dict[str, int] = {}
    for f in fields:
        label_counts[f.label] = label_counts.get(f.label, 0) + 1

    label_seen: dict[str, int] = {}
    for f in fields:
        if label_counts[f.label] > 1:
            label_seen[f.label] = label_seen.get(f.label, 0) + 1
            el_id = f.selector[1:] if f.selector.startswith("#") else ""
            suffix = _humanize_id(el_id) if el_id else f.placeholder
            if suffix and suffix.lower() != f.label.lower():
                f.label = f"{f.label} ({suffix})"
            else:
                f.label = f"{f.label} ({label_seen[f.label]})"

    return fields


# --------------------------------------------------------------------------
# The JS blob that extracts every form field from a document / frame.
# Defined once at module level so we can run it on both the main frame and
# any ATS <iframe> frames without duplication.
# --------------------------------------------------------------------------
_FIELD_EXTRACTION_JS = """() => {
    const results = [];
    const seen = new Set();

    function findLabel(el) {
        const id = el.id || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        const placeholder = el.placeholder || '';

        if (id) {
            const lbl = document.querySelector('label[for="' + id + '"]');
            if (lbl) {
                const text = lbl.textContent.trim().replace(/\\*/g, '');
                if (text && text.length < 100) return text;
            }
        }

        const parentLabel = el.closest('label');
        if (parentLabel) {
            const clone = parentLabel.cloneNode(true);
            clone.querySelectorAll('input, select, textarea').forEach(c => c.remove());
            const text = clone.textContent.trim().replace(/\\*/g, '');
            if (text && text.length < 100) return text;
        }

        const container = el.closest('.form-group, .form-field, [class*="col"], .mb-3, .mb-4, .mt-3');
        if (container) {
            const lbl = container.querySelector('label, .label, legend');
            if (lbl && !lbl.contains(el)) {
                const text = lbl.textContent.trim().replace(/\\*/g, '');
                if (text && text.length < 100) return text;
            }
        }

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

        if (ariaLabel) return ariaLabel;
        if (placeholder) return placeholder;
        return '';
    }

    function isAutocomplete(el) {
        const parent = el.closest(
            '[class*="__control"], [class*="__value-container"], ' +
            '[class*="auto-complete"], [class*="autocomplete"], ' +
            '[class*="react-select"], [class*="react-tags"], ' +
            '[class*="tagsinput"], [class*="token-input"], ' +
            '[class*="multiselect"], [class*="choices"]'
        );
        if (parent) return true;
        if (el.getAttribute('role') === 'combobox') return true;
        if (el.getAttribute('aria-autocomplete')) return true;
        return false;
    }

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
        const lbl = (label || '').toLowerCase();
        if (el.type === 'text' && /\\b(date|birth|dob|d\\.o\\.b|born|expir|deadline)\\b/i.test(lbl)) return true;
        return false;
    }

    document.querySelectorAll('input, textarea, select').forEach(el => {
        const type = el.type || (el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select' : 'text');
        if (['hidden', 'submit', 'button', 'reset'].includes(type)) return;
        if (type === 'radio' || type === 'checkbox') return;
        const id = el.id || '';
        if (id && id.includes('react-select')) return;

        const key = id || el.name || '';
        if (seen.has(key) && key) return;
        if (key) seen.add(key);

        const label = findLabel(el);
        const tag = el.tagName.toLowerCase();
        let selector = '';
        if (id) selector = '#' + id;
        else if (el.name) selector = tag + '[name="' + el.name + '"]';

        const options = [];
        if (tag === 'select') {
            el.querySelectorAll('option').forEach(o => {
                const t = o.textContent.trim();
                if (t) options.push(t);
            });
        }

        let fieldType = type;
        if (type === 'file') {
            fieldType = 'file';
        } else if (isAutocomplete(el)) {
            fieldType = 'autocomplete';
        } else if (type === 'date' || isDatePicker(el, label)) {
            fieldType = 'datepicker';
        } else if (type === 'tel') {
            fieldType = 'tel';
        }

        results.push({
            selector, field_type: fieldType, label, id, name: el.name || '',
            placeholder: el.placeholder || el.getAttribute('data-placeholder') || '',
            required: el.required || el.getAttribute('aria-required') === 'true',
            options, max_length: el.maxLength > 0 ? el.maxLength : null, depends_on: ''
        });
    });

    const radioGroups = {};
    document.querySelectorAll('input[type="radio"]').forEach(el => {
        const name = el.name;
        if (!name) return;
        if (!radioGroups[name]) {
            radioGroups[name] = { type: 'radio', options: [], label: '',
                required: el.required || el.getAttribute('aria-required') === 'true' };
            const fieldset = el.closest('fieldset');
            if (fieldset) {
                const legend = fieldset.querySelector('legend');
                if (legend) radioGroups[name].label = legend.textContent.trim();
            }
            if (!radioGroups[name].label) {
                const parent = el.closest('.form-group, .col, .mb-3, .custom-control, [class*="col"]');
                if (parent) {
                    const lbl = parent.parentElement?.querySelector('label, .label, [class*="label"]');
                    if (lbl && !lbl.contains(el))
                        radioGroups[name].label = lbl.textContent.trim().replace(/\\*/g, '');
                }
            }
        }
        const lbl = el.labels?.[0]?.textContent?.trim() || el.value;
        if (lbl) radioGroups[name].options.push(lbl);
    });
    for (const [name, info] of Object.entries(radioGroups)) {
        results.push({
            selector: 'input[name="' + name + '"]', field_type: info.type,
            label: info.label || '', id: '', name, placeholder: '',
            required: info.required, options: info.options, max_length: null, depends_on: ''
        });
    }

    const checkboxGroups = {};
    let cbGroupCounter = 0;
    document.querySelectorAll('input[type="checkbox"]').forEach(el => {
        let groupKey = el.name;
        if (!groupKey) {
            const eid = el.id || '';
            const idBase = eid.replace(/[-_]?\\d+$/, '');
            if (idBase) {
                groupKey = '__id__' + idBase;
            } else {
                const wrapper = el.closest('.form-group, .col, .mb-3, fieldset, [class*="col"], [class*="wrapper"], [class*="group"]');
                if (wrapper) {
                    if (!wrapper._cbGroupKey) { wrapper._cbGroupKey = '__auto__' + (++cbGroupCounter); }
                    groupKey = wrapper._cbGroupKey;
                } else {
                    groupKey = '__ungrouped__' + (++cbGroupCounter);
                }
            }
        }
        if (!checkboxGroups[groupKey]) {
            checkboxGroups[groupKey] = { groupLabel: '', items: [],
                required: el.required || el.getAttribute('aria-required') === 'true',
                hasName: !!el.name, name: el.name || '' };
            const fieldset = el.closest('fieldset');
            if (fieldset) {
                const legend = fieldset.querySelector('legend');
                if (legend) checkboxGroups[groupKey].groupLabel = legend.textContent.trim().replace(/\\*/g, '');
            }
            if (!checkboxGroups[groupKey].groupLabel) {
                const parent = el.closest('.form-group, .col, .mb-3, .custom-control, [class*="col"], [class*="wrapper"]');
                if (parent) {
                    const lbl = parent.parentElement?.querySelector('label, .label, [class*="label"]');
                    if (lbl && !lbl.contains(el))
                        checkboxGroups[groupKey].groupLabel = lbl.textContent.trim().replace(/\\*/g, '');
                }
            }
            if (!checkboxGroups[groupKey].groupLabel && el.id) {
                const idLabel = el.id.replace(/[-_]?\\d+$/, '').replace(/[-_]/g, ' ').trim();
                if (idLabel && idLabel.length > 1)
                    checkboxGroups[groupKey].groupLabel = idLabel.charAt(0).toUpperCase() + idLabel.slice(1);
            }
        }
        let optionLabel = '';
        if (el.labels && el.labels.length > 0) {
            const clone = el.labels[0].cloneNode(true);
            clone.querySelectorAll('input').forEach(c => c.remove());
            optionLabel = clone.textContent.trim();
        }
        if (!optionLabel) optionLabel = el.value || '';
        checkboxGroups[groupKey].items.push({ label: optionLabel, id: el.id || '', value: el.value || '' });
    });
    for (const [groupKey, info] of Object.entries(checkboxGroups)) {
        const allOptions = info.items.map(i => i.label).filter(Boolean);
        let cbSelector;
        if (info.hasName && info.name) {
            cbSelector = 'input[name="' + info.name + '"]';
        } else {
            const ids = info.items.map(i => i.id).filter(Boolean);
            cbSelector = ids.length > 0 ? ids.map(id => '#' + CSS.escape(id)).join(', ') : 'input[type="checkbox"]';
        }
        results.push({
            selector: cbSelector, field_type: 'checkbox', label: info.groupLabel || '',
            id: '', name: info.name || groupKey, placeholder: '', required: info.required,
            options: allOptions, max_length: null, depends_on: ''
        });
    }

    const reactContainers = document.querySelectorAll(
        '[class*="react-select"], [id*="react-select"], [class*="__control"], [class*="__value-container"]'
    );
    const reactSeen = new Set();
    reactContainers.forEach(container => {
        const wrapper = container.closest('[class*="wrapper"], [class*="container"], .form-group, .col, .mb-3, [class*="col"]') || container;
        const wrapperKey = wrapper.getAttribute('id') || wrapper.className.slice(0, 50);
        if (reactSeen.has(wrapperKey) && wrapperKey) return;
        if (wrapperKey) reactSeen.add(wrapperKey);
        const input = container.querySelector('input') || container.querySelector('[role="combobox"]');
        if (!input) return;
        const inputKey = input.id || input.name || '';
        if (inputKey && seen.has(inputKey)) return;
        if (inputKey) seen.add(inputKey);
        let label = '';
        let searchNode = container;
        for (let i = 0; i < 6; i++) {
            searchNode = searchNode.parentElement;
            if (!searchNode) break;
            const lbl = searchNode.querySelector(':scope > label, :scope > .label');
            if (lbl) { label = lbl.textContent.trim().replace(/\\*/g, ''); break; }
            const prev = searchNode.previousElementSibling;
            if (prev && ['LABEL', 'P', 'SPAN', 'DIV'].includes(prev.tagName)) {
                const text = prev.textContent.trim().replace(/\\*/g, '');
                if (text && text.length < 80 && text.length > 1) { label = text; break; }
            }
            const children = [...searchNode.children];
            const containerIdx = children.indexOf(container.closest('[class*="css"]') || container);
            for (let j = containerIdx - 1; j >= 0; j--) {
                const text = children[j].textContent?.trim();
                if (text && text.length < 80 && text.length > 1) { label = text; break; }
            }
            if (label) break;
        }
        if (!label) label = input.getAttribute('aria-label') || '';
        const phEl = container.querySelector('[class*="placeholder"]') ||
                     (container.closest('[class*="__control"]') || container).querySelector('[class*="placeholder"]');
        const placeholder = input.placeholder || (phEl ? phEl.textContent.trim() : '');
        if (!label && placeholder) label = placeholder;
        if (!label) {
            const idWrapper = container.closest('[id]:not([id^="react-select"])');
            if (idWrapper && idWrapper.id && idWrapper.id.length < 40) label = idWrapper.id;
        }
        const options = [];
        const menu = container.querySelector('[class*="menu"]');
        if (menu) menu.querySelectorAll('[class*="option"]').forEach(opt => options.push(opt.textContent.trim()));
        const inputId = input.id || '';
        results.push({
            selector: inputId ? '#' + inputId : 'input[role="combobox"]',
            field_type: 'autocomplete', label, id: inputId, name: input.name || '',
            placeholder, required: input.required || input.getAttribute('aria-required') === 'true',
            options, max_length: null, depends_on: ''
        });
    });

    const customDropdowns = document.querySelectorAll(
        '[role="listbox"], div[class*="dropdown"]:not(nav *):not(header *), ' +
        'div[class*="select-menu"], div[class*="custom-select"]:not(select)'
    );
    const customSeen = new Set();
    customDropdowns.forEach(el => {
        if (el.closest('[class*="react-select"]')) return;
        const cls = (el.className || '').toString();
        if (/nav|menu-item|header/i.test(cls)) return;
        const elKey = el.id || cls.slice(0, 60);
        if (customSeen.has(elKey) && elKey) return;
        if (elKey) customSeen.add(elKey);
        const hiddenSelect = el.querySelector('select');
        if (hiddenSelect) {
            const hKey = hiddenSelect.id || hiddenSelect.name || '';
            if (hKey && seen.has(hKey)) return;
        }
        let label = '';
        const container = el.closest('.form-group, .form-field, .mb-3, .mb-4, [class*="col"]');
        if (container) {
            const lbl = container.querySelector('label, .label, legend');
            if (lbl && !el.contains(lbl)) label = lbl.textContent.trim().replace(/\\*/g, '');
        }
        if (!label) {
            const prev = el.previousElementSibling;
            if (prev && ['LABEL', 'SPAN', 'P'].includes(prev.tagName))
                label = prev.textContent.trim().replace(/\\*/g, '');
        }
        if (!label) label = el.getAttribute('aria-label') || '';
        const options = [];
        el.querySelectorAll('[role="option"], li, .option, [class*="option"]').forEach(opt => {
            const t = opt.textContent.trim();
            if (t && t.length < 100) options.push(t);
        });
        const selector = el.id ? '#' + el.id : '';
        if (!selector && !label) return;
        results.push({
            selector: selector || '[role="listbox"]', field_type: 'custom_select', label,
            id: el.id || '', name: '', placeholder: el.getAttribute('data-placeholder') || '',
            required: el.getAttribute('aria-required') === 'true', options, max_length: null, depends_on: ''
        });
    });

    document.querySelectorAll('[role="combobox"]').forEach(el => {
        const id = el.id || '';
        const name = el.getAttribute('name') || '';
        const key = id || name || '';
        if (key && seen.has(key)) return;
        if (key) seen.add(key);
        const label = findLabel(el);
        results.push({
            selector: id ? '#' + id : '[role="combobox"]', field_type: 'autocomplete', label,
            id, name, placeholder: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '',
            required: el.getAttribute('aria-required') === 'true', options: [], max_length: null, depends_on: ''
        });
    });

    const cascadePairs = [
        ['country', 'state'], ['country', 'province'], ['country', 'region'],
        ['state', 'city'], ['province', 'city'], ['region', 'city'],
        ['state', 'district'], ['district', 'sub_district'],
        ['category', 'subcategory'], ['category', 'sub_category'],
        ['make', 'model'], ['brand', 'model']
    ];
    for (const item of results) {
        const itemName = (item.name || item.id || item.label || '').toLowerCase().replace(/[-_ ]/g, '');
        for (const [parent, child] of cascadePairs) {
            const childNorm = child.replace(/[-_ ]/g, '');
            const parentNorm = parent.replace(/[-_ ]/g, '');
            if (itemName.includes(childNorm)) {
                const parentField = results.find(r => {
                    const rName = (r.name || r.id || r.label || '').toLowerCase().replace(/[-_ ]/g, '');
                    return rName.includes(parentNorm);
                });
                if (parentField)
                    item.depends_on = parentField.label || parentField.name || parentField.id || '';
                break;
            }
        }
    }

    return results;
}"""


async def _read_fields(url: str) -> tuple[list[FormField], str]:
    """Navigate to URL and extract all form fields + page context.

    After scanning the main document we also scan any <iframe> frames that
    belong to known ATS platforms (Workday, Greenhouse, Lever, iCIMS …) or
    that contain form inputs, so that job-application forms embedded inside
    iframes are discovered correctly.
    """
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

        # ── Main frame: extract all fields ──────────────────────────────────
        all_field_data = await page.evaluate(_FIELD_EXTRACTION_JS)
        page_title = title.strip()
        fields = _postprocess_fields(all_field_data, page_title)

        # ── iframe frames: scan for ATS-embedded forms ───────────────────────
        # Workday / Greenhouse / Lever / iCIMS load their application forms
        # inside <iframe> elements. We detect known ATS domains and run the
        # same extraction on those frames so the filler can fill them too.
        seen_frame_urls: set[str] = set()
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            furl = frame.url or ""
            if not furl or furl in ("about:blank", "") or furl in seen_frame_urls:
                continue
            # Only process ATS iframes (or iframes with visible form input)
            is_ats = any(domain in furl for domain in _ATS_DOMAINS)
            if not is_ats:
                # Check if the frame has any form inputs before bothering
                try:
                    has_inputs = await frame.evaluate(
                        "() => document.querySelectorAll('input, select, textarea').length > 0"
                    )
                    if not has_inputs:
                        continue
                except Exception:
                    continue
            seen_frame_urls.add(furl)
            try:
                frame_data = await frame.evaluate(_FIELD_EXTRACTION_JS)
                frame_fields = _postprocess_fields(frame_data, page_title, frame_url=furl)
                # Merge — deduplicate by (label, field_type) against main-frame fields
                main_labels = {(f.label.lower(), f.field_type) for f in fields}
                for ff in frame_fields:
                    if (ff.label.lower(), ff.field_type) not in main_labels:
                        fields.append(ff)
            except Exception:
                pass  # Frame navigated away or cross-origin block — skip silently

        if False:  # pragma: no cover — dead block kept only to preserve triple-quote balance
         _x = """DEAD_PLACEHOLDER {
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
            let cbGroupCounter = 0;
            document.querySelectorAll('input[type="checkbox"]').forEach(el => {
                // Use name, or fall back to id prefix, or a shared parent key
                let groupKey = el.name;
                if (!groupKey) {
                    // Try to derive group from id (e.g. "hobbies-checkbox-1" → "hobbies-checkbox")
                    const eid = el.id || '';
                    const idBase = eid.replace(/[-_]?\\d+$/, '');
                    if (idBase) {
                        groupKey = '__id__' + idBase;
                    } else {
                        // Group by closest wrapper element
                        const wrapper = el.closest('.form-group, .col, .mb-3, fieldset, [class*="col"], [class*="wrapper"], [class*="group"]');
                        if (wrapper) {
                            if (!wrapper._cbGroupKey) { wrapper._cbGroupKey = '__auto__' + (++cbGroupCounter); }
                            groupKey = wrapper._cbGroupKey;
                        } else {
                            groupKey = '__ungrouped__' + (++cbGroupCounter);
                        }
                    }
                }
                if (!checkboxGroups[groupKey]) {
                    checkboxGroups[groupKey] = {
                        groupLabel: '',
                        items: [],
                        required: el.required || el.getAttribute('aria-required') === 'true',
                        hasName: !!el.name,
                        name: el.name || ''
                    };
                    const fieldset = el.closest('fieldset');
                    if (fieldset) {
                        const legend = fieldset.querySelector('legend');
                        if (legend) checkboxGroups[groupKey].groupLabel = legend.textContent.trim().replace(/\\*/g, '');
                    }
                    if (!checkboxGroups[groupKey].groupLabel) {
                        const parent = el.closest('.form-group, .col, .mb-3, .custom-control, [class*="col"], [class*="wrapper"]');
                        if (parent) {
                            const lbl = parent.parentElement?.querySelector('label, .label, [class*="label"]');
                            if (lbl && !lbl.contains(el)) {
                                checkboxGroups[groupKey].groupLabel = lbl.textContent.trim().replace(/\\*/g, '');
                            }
                        }
                    }
                    // Also try looking at the id for a label hint
                    if (!checkboxGroups[groupKey].groupLabel && el.id) {
                        const idLabel = el.id.replace(/[-_]?\\d+$/, '').replace(/[-_]/g, ' ').trim();
                        if (idLabel && idLabel.length > 1) {
                            checkboxGroups[groupKey].groupLabel = idLabel.charAt(0).toUpperCase() + idLabel.slice(1);
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
                checkboxGroups[groupKey].items.push({
                    label: optionLabel,
                    id: optionId,
                    value: el.value || ''
                });
            });

            for (const [groupKey, info] of Object.entries(checkboxGroups)) {
                const allOptions = info.items.map(i => i.label).filter(Boolean);
                // Build selector: use name if available, else use individual IDs
                let cbSelector;
                if (info.hasName && info.name) {
                    cbSelector = 'input[name="' + info.name + '"]';
                } else {
                    const ids = info.items.map(i => i.id).filter(Boolean);
                    if (ids.length > 0) {
                        cbSelector = ids.map(id => '#' + CSS.escape(id)).join(', ');
                    } else {
                        cbSelector = 'input[type="checkbox"]';
                    }
                }
                results.push({
                    selector: cbSelector,
                    field_type: 'checkbox',
                    label: info.groupLabel || '',
                    id: '',
                    name: info.name || groupKey,
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
                const phEl = container.querySelector('[class*="placeholder"]') ||
                             (container.closest('[class*="__control"]') || container).querySelector('[class*="placeholder"]');
                const placeholder = input.placeholder || (phEl ? phEl.textContent.trim() : '');
                if (!label && placeholder) label = placeholder;

                // Last resort: use the wrapper id as a label hint.
                // demoqa has <div id="state"><div class="react-select..."/></div>.
                if (!label) {
                    const idWrapper = container.closest('[id]:not([id^="react-select"])');
                    if (idWrapper && idWrapper.id && idWrapper.id.length < 40) {
                        label = idWrapper.id;
                    }
                }

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
        }"""  # end of _dead — this string is intentionally unused

        await browser.close()

    return fields, page_context


def read_form(url: str) -> tuple[list[FormField], str]:
    """Synchronous wrapper for form reading."""
    return asyncio.run(_read_fields(url))
