"""Open a job's application form in a real browser window and fill in what we know.

SAFETY: this module never clicks Submit / Apply / Send. It fills fields, uploads your CV and
cover letter, highlights the submit button, lists anything it could not answer, and then waits
for YOU to review and submit (or close the window). Run it on your own computer, not on a server.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# (label regex, key in the value map). First match wins, so order matters.
FIELD_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), k) for p, k in [
    (r"\bfirst\s*name|vorname|given name", "first_name"),
    (r"\blast\s*name|nachname|surname|family name", "last_name"),
    (r"^\s*(full\s*)?name\s*\*?\s*$|^\s*your name|vollständiger name", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|telefon|mobile|handy", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"website|portfolio|personal (site|url)", "github"),
    (r"current (company|employer)|aktueller arbeitgeber", "current_company"),
    (r"current (title|position|role)", "current_title"),
    (r"salary|gehalt|compensation expectation", "salary_expectation"),
    (r"start date|notice period|kündigungsfrist|eintritt|earliest", "earliest_start_date"),
    (r"sponsor|visa", "requires_sponsorship"),
    (r"authori[sz]ed|work permit|right to work|eligible to work|arbeitserlaubnis", "work_authorization"),
    (r"how did you (hear|find)|wie sind sie|where did you", "how_did_you_hear"),
    (r"german|deutsch", "german_level"),
    (r"english|englisch", "english_level"),
    (r"\bcity\b|\blocation\b|where are you (based|located)|wohnort|\bort\b", "city"),
    (r"cover letter|anschreiben|motivation", "cover_letter_text"),
]]

SUBMIT_TEXT = re.compile(r"submit|apply|send|bewerb|absenden|senden", re.I)


def value_map(profile: dict, letter_text: str = "") -> dict[str, str]:
    me = profile.get("personal", {})
    answers = profile.get("answers", {}) or {}
    exp0 = (profile.get("experience") or [{}])[0]
    values = {
        "first_name": me.get("first_name", ""),
        "last_name": me.get("last_name", ""),
        "full_name": f"{me.get('first_name', '')} {me.get('last_name', '')}".strip(),
        "email": me.get("email", ""),
        "phone": me.get("phone", ""),
        "linkedin": me.get("linkedin", ""),
        "github": me.get("github", ""),
        "city": me.get("city", ""),
        "current_company": exp0.get("company", ""),
        "current_title": exp0.get("title", ""),
        "cover_letter_text": letter_text,
    }
    values.update({k: str(v) for k, v in answers.items() if v})
    return values


def match_field(label: str, values: dict[str, str]) -> tuple[str, str]:
    """Return (key, value) for a form label, or ('', '') if we should not touch it."""
    for pattern, key in FIELD_RULES:
        if pattern.search(label):
            return key, values.get(key, "")
    return "", ""


def letter_plain_text(job: dict, profile: dict, paragraphs: list[str]) -> str:
    name = f"{profile['personal']['first_name']} {profile['personal']['last_name']}"
    company = job.get("company") or "Hiring Team"
    return f"Dear {company} Hiring Team,\n\n" + "\n\n".join(paragraphs) + f"\n\nKind regards,\n{name}"


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    needs_you: list[str] = field(default_factory=list)


LABEL_JS = """el => {
  const byFor = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
  const wrap = el.closest('label, .field, .application-question, fieldset, [class*="field"], [class*="question"]');
  const text = (byFor && byFor.innerText) || (el.labels && el.labels[0] && el.labels[0].innerText)
             || el.getAttribute('aria-label') || el.placeholder
             || (wrap && wrap.innerText.split('\\n')[0]) || el.name || el.id || '';
  return {label: text.trim().slice(0, 160), type: (el.type || el.tagName).toLowerCase(),
          required: el.required || el.getAttribute('aria-required') === 'true',
          value: el.value || '', visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)};
}"""

BANNER_JS = """() => {
  const b = document.createElement('div');
  b.textContent = 'Job Hunter filled this form. Review every field, fix anything wrong, then click Submit yourself.';
  b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#1f6feb;color:#fff;' +
                    'font:600 15px sans-serif;padding:10px 16px;text-align:center';
  document.body.appendChild(b);
}"""


def fill(job: dict, profile: dict, docs: dict, letter_paragraphs: list[str], headless: bool = False,
         inspect=None) -> FillReport:
    from playwright.sync_api import sync_playwright

    url = job.get("apply_url") or job.get("url")
    values = value_map(profile, letter_plain_text(job, profile, letter_paragraphs))
    cv_file = docs.get("cv_pdf") or docs.get("cv_docx")
    letter_file = docs.get("letter_pdf") or docs.get("letter_docx")
    report = FillReport()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2500)

        # Some boards show the form only after clicking "Apply" (this opens the form; it does not submit).
        if not page.locator("input[type=file], input[type=email]").count():
            opener = page.get_by_role("link", name=re.compile(r"^\s*apply", re.I)).or_(
                page.get_by_role("button", name=re.compile(r"^\s*apply( for this job| now)?\s*$", re.I)))
            if opener.count():
                opener.first.click()
                page.wait_for_timeout(2500)

        # File uploads
        file_inputs = page.locator("input[type=file]")
        for i in range(file_inputs.count()):
            el = file_inputs.nth(i)
            info = el.evaluate(LABEL_JS)
            label = info["label"].lower()
            target = letter_file if re.search(r"cover|anschreiben|motivation", label) else cv_file
            if re.search(r"cover|anschreiben|motivation", label) or i == 0 or re.search(r"resume|cv|lebenslauf", label):
                if target and Path(target).exists():
                    el.set_input_files(target)
                    report.uploaded.append(f"{info['label'] or 'file'} ← {Path(target).name}")

        # Text fields
        fields = page.locator("input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]):"
                              "not([type=submit]):not([type=button]), textarea")
        for i in range(fields.count()):
            el = fields.nth(i)
            info = el.evaluate(LABEL_JS)
            if not info["visible"] or info["value"]:
                continue
            key, value = match_field(info["label"], values)
            if value:
                el.fill(value)
                report.filled.append(f"{info['label']} = {value[:60]}{'…' if len(value) > 60 else ''}")
            elif info["required"]:
                report.needs_you.append(info["label"] or "(unlabelled required field)")

        # Dropdowns, checkboxes and radio questions are left to you on purpose.
        for sel in ("select", "input[type=checkbox][required]", "[role=radiogroup]", "[role=combobox]"):
            loc = page.locator(sel)
            for i in range(loc.count()):
                info = loc.nth(i).evaluate(LABEL_JS)
                if info["visible"] and (info["required"] or sel != "select"):
                    report.needs_you.append(f"{info['label'] or sel} (choose yourself)")

        # Mark the submit button, never click it.
        page.evaluate(BANNER_JS)
        buttons = page.locator("button, input[type=submit]")
        for i in range(buttons.count()):
            btn = buttons.nth(i)
            text = (btn.inner_text() if btn.evaluate("e => e.tagName") == "BUTTON" else btn.get_attribute("value")) or ""
            if SUBMIT_TEXT.search(text):
                btn.evaluate("e => { e.style.outline = '4px solid #d1242f'; e.scrollIntoView({block:'center'}); }")

        if inspect:  # test hook: look at the page before it closes
            inspect(page)
        if not headless:
            print("\nThe form is open in the browser. Review it, submit it yourself, then close the window.")
            page.wait_for_event("close", timeout=0)
        browser.close()
    return report
