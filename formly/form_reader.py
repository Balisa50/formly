"""Read form fields from a web page using Playwright."""
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

        # Find all form inputs
        inputs = await page.query_selector_all(
            "input:not([type='hidden']):not([type='submit']):not([type='button']), "
            "textarea, select"
        )

        for el in inputs:
            try:
                field = await _extract_field(page, el)
                if field:
                    fields.append(field)
            except Exception:
                continue

        # Handle radio/checkbox groups — group by name
        radio_groups = await _extract_radio_groups(page)
        fields.extend(radio_groups)

        await browser.close()

    return fields, page_context


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

    # Skip radio/checkbox — handled separately as groups
    if el_type in ("radio", "checkbox"):
        return None

    # Find label
    label = ""
    if el_id:
        label_el = await page.query_selector(f'label[for="{el_id}"]')
        if label_el:
            label = (await label_el.text_content() or "").strip()

    if not label:
        # Try parent label
        label = await el.evaluate("""e => {
            const parent = e.closest('label');
            if (parent) return parent.textContent.trim();
            // Try previous sibling or nearby text
            const prev = e.previousElementSibling;
            if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN' || prev.tagName === 'P'))
                return prev.textContent.trim();
            return '';
        }""")

    if not label:
        label = aria_label or placeholder or name

    # Build selector
    if el_id:
        selector = f"#{el_id}"
    elif name:
        selector = f'{tag}[name="{name}"]'
    else:
        selector = await el.evaluate("e => { const idx = [...e.parentElement.children].indexOf(e); return e.parentElement.tagName.toLowerCase() + ' > ' + e.tagName.toLowerCase() + ':nth-child(' + (idx+1) + ')'; }")

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
        label=label[:200],
        placeholder=placeholder,
        required=required,
        options=options,
        max_length=int(maxlength) if maxlength else None,
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
                groups[name] = { type: el.type, options: [], label: '' };
                // Find group label
                const fieldset = el.closest('fieldset');
                if (fieldset) {
                    const legend = fieldset.querySelector('legend');
                    if (legend) groups[name].label = legend.textContent.trim();
                }
                if (!groups[name].label) {
                    const parent = el.closest('div, fieldset, section');
                    if (parent) {
                        const heading = parent.querySelector('label, span, p, h3, h4');
                        if (heading) groups[name].label = heading.textContent.trim();
                    }
                }
            }
            const lbl = el.labels?.[0]?.textContent?.trim() || el.value;
            groups[name].options.push(lbl);
        });
        return groups;
    }""")

    for name, info in group_data.items():
        groups.append(FormField(
            selector=f'input[name="{name}"]',
            field_type=info["type"],
            label=info.get("label", name),
            options=info.get("options", []),
        ))

    return groups


def read_form(url: str) -> tuple[list[FormField], str]:
    """Synchronous wrapper for form reading."""
    return asyncio.run(_read_fields(url))
