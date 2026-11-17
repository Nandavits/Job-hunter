"""Open a job's application form in a real browser window and fill in what we know.

SAFETY: this module never clicks Submit / Apply / Send. It fills fields, uploads your CV and
cover letter, highlights the submit button, lists anything it could not answer, and then waits
for YOU to review and submit (or close the window). Run it on your own computer, not on a server.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("jobhunter")

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

# Open questions answered from the tailored cover letter (or by Claude, if an API key is set).
QUESTION_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), k) for p, k in [
    (r"why (are )?you (a |the )?(good|right|best|ideal|great|strong)|why should we|what makes you|good fit|right fit|suitable|qualif|strength|stand out|"
     r"relevant experience|experience (with|in)|tell us about a (project|time)|describe .*(project|experience)",
     "why_you"),
    (r"why .*(join|us\b|company|apply|this (role|position|job)|interested)|motivat|warum|"
     r"interest(ed)? in (this|the|our|joining)|what (excites|attracts) you", "why_us"),
    (r"about (yourself|you)\b|introduce yourself|über (dich|sie|mich)|short summary|who are you", "about_you"),
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


def first_person_summary(profile: dict) -> str:
    text = (profile.get("default_summary") or "").strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = [f"I am {'an' if sentences[0][:1].lower() in 'aeiou' else 'a'} {sentences[0][:1].lower()}{sentences[0][1:]}"]
    for sentence in sentences[1:]:
        if re.match(r"(Strong|Experienced|Skilled|Focused|Completing|Seeking)\b", sentence):
            sentence = "I am " + sentence[:1].lower() + sentence[1:]
        out.append(sentence)
    return " ".join(out).replace(" and seeking ", " and I am looking for ")


def draft_answers(profile: dict, letter_paragraphs: list[str]) -> dict[str, str]:
    """Answers to common open questions, built from the tailored letter (facts from the profile only)."""
    paras = [p.strip() for p in letter_paragraphs if p.strip()]
    return {
        "why_us": paras[0] if paras else "",
        "why_you": paras[1] if len(paras) > 1 else "",
        "about_you": first_person_summary(profile),
    }


def match_question(label: str) -> str:
    for pattern, key in QUESTION_RULES:
        if pattern.search(label):
            return key
    return ""


def claude_answer(question: str, job: dict, profile: dict, letter_paragraphs: list[str]) -> str:
    """Optional: a fresh answer to the exact question, written by Claude from the same facts."""
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        facts = {k: profile.get(k) for k in ("default_summary", "experience", "skills", "projects",
                                             "education", "languages", "relocation")}
        msg = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            max_tokens=400,
            system=("Answer one job-application question for the candidate, in the first person, in English, "
                    "60-120 words, plain text. Use ONLY facts from the candidate profile and cover letter; "
                    "never invent experience, numbers or tools. Be specific to the company and role."),
            messages=[{"role": "user", "content":
                       f"Question: {question}\n\nCompany: {job.get('company')}\nRole: {job.get('title')}\n"
                       f"Job ad (excerpt): {(job.get('description') or '')[:3000]}\n\n"
                       f"Cover letter: {' '.join(letter_paragraphs)}\n\n"
                       f"Candidate profile: {json.dumps(facts, ensure_ascii=False)[:8000]}"}],
        )
        return "".join(getattr(b, "text", "") for b in msg.content).strip()
    except Exception as exc:
        log.warning("Claude answer failed (%s); using the cover-letter draft", exc)
        return ""


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
    drafted: list[str] = field(default_factory=list)
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
    drafts = draft_answers(profile, letter_paragraphs)
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
            question = "" if key else match_question(info["label"])
            if value:
                el.fill(value)
                report.filled.append(f"{info['label']} = {value[:60]}{'…' if len(value) > 60 else ''}")
            elif question and (drafts.get(question) or os.environ.get("ANTHROPIC_API_KEY")):
                answer = claude_answer(info["label"], job, profile, letter_paragraphs) or drafts.get(question, "")
                if answer:
                    el.fill(answer)
                    report.drafted.append(f"{info['label']} = {answer[:70]}…")
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
