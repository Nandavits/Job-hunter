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
    (r"xing", "xing"),
    (r"website|portfolio|personal (site|url)", "github"),
    (r"salutation|anrede|^\s*title\s*\*?\s*$", "salutation"),
    (r"date of birth|birth ?date|birthday|geburtsdatum", "date_of_birth"),
    (r"nationality|citizenship|staatsangehörigkeit|staatsangehoerigkeit", "nationality"),
    (r"postal|zip|postleitzahl|\bplz\b", "postal_code"),
    (r"street|straße|strasse|address line|^\s*(home |postal |mailing )?address|anschrift|adresse", "street"),
    (r"\bcountry\b|\bland\b", "country"),
    (r"driv(er.?s|ing) licen[cs]e|führerschein|fuehrerschein", "driving_licence"),
    (r"job ?title|position title|current (title|position|role)|aktuelle position|jobtitel", "current_title"),
    (r"current (company|employer)|aktueller arbeitgeber", "current_company"),
    (r"salary|gehalt|compensation expectation", "salary_expectation"),
    (r"start date|notice period|kündigungsfrist|eintritt|earliest|availability|verfügbar", "earliest_start_date"),
    (r"residence permit|residence title|aufenthalt|visa (status|type)|immigration status|permit type", "residence_permit"),
    (r"sponsor|visa", "requires_sponsorship"),
    (r"authori[sz]ed|work permit|right to work|eligible to work|arbeitserlaubnis|arbeitsgenehmigung", "work_authorization"),
    (r"how did you (hear|find)|wie sind sie|wie hast du|where did you|referral source|\bsource\b", "how_did_you_hear"),
    (r"applied .*before|previously applied|bereits beworben|worked (here|for us)|previously (employed|worked)", "applied_before"),
    (r"\breferr|know (anyone|someone)|empfohlen|empfehlung", "referred"),
    (r"non-?compete|wettbewerbsverbot|restrictive covenant", "non_compete"),
    (r"relocat|umzug|umziehen", "relocation_answer"),
    (r"remote|hybrid|on-?site|work(ing)? model|arbeitsmodell|home ?office", "work_model"),
    (r"full[- ]?time|part[- ]?time|vollzeit|teilzeit|employment type|working hours|wochenstunden", "employment_type"),
    (r"contract type|vertragsart|befrist|permanent|fixed[- ]term|freelance", "contract_type"),
    (r"travel|reise", "travel"),
    (r"graduat|abschlussdatum|completion date|expected (completion|degree)", "graduation_date"),
    (r"\bgpa\b|cgpa|\bgrade\b|abschlussnote|durchschnittsnote|\bnote\b(?!s)", "degree_grade"),
    (r"highest (degree|level of education|education)|höchster (bildungs)?abschluss|education level|degree", "highest_degree"),
    (r"university|universität|hochschule|school|institution", "university"),
    (r"german|deutsch", "german_level"),
    (r"english|englisch", "english_level"),
    (r"\bcity\b|\blocation\b|where are you (based|located)|wohnort|\bort\b", "city"),
    (r"cover letter|anschreiben|motivation", "cover_letter_text"),
]]

# "How many years of experience do you have with SQL?" -> answers.years_sql
TOOL_YEARS = ["power bi", "postgres", "airflow", "tableau", "python", "docker", "kubernetes", "kafka",
              "google cloud", "gcp", "sql", "dbt"]
YEARS_Q = re.compile(r"\byears?\b|\bjahre", re.I)
EXPERIENCE_Q = re.compile(r"experience|erfahrung|berufserfahrung", re.I)


# Open questions answered from the tailored cover letter (or by Claude, if an API key is set).
QUESTION_RULES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), k) for p, k in [
    (r"why (are )?you (a |the )?(good|right|best|ideal|great|strong)|why should we|what makes you|good fit|right fit|suitable|qualif|strength|stand out|"
     r"relevant experience|experience (with|in)|tell us about a (project|time)|describe .*(project|experience)",
     "why_you"),
    (r"why .*(join|us\b|company|apply|this (role|position|job)|interested)|motivat|warum|"
     r"interest(ed)? in (this|the|our|joining)|what (excites|attracts) you", "why_us"),
    (r"about (yourself|you)\b|introduce yourself|über (dich|sie|mich)|short summary|who are you", "about_you"),
]]

SENSITIVE = re.compile(r"gender|geschlecht|pronoun|race|ethnic|disab|behinder|veteran|sexual|orientation|"
                       r"religio|marital|age\b|date of birth|geburtsdatum|nationality|citizenship|staatsangeh", re.I)
PREFER_NOT = re.compile(r"prefer not|decline|don.t wish|do not wish|not to (say|answer|disclose)|keine angabe|"
                        r"möchte (ich )?nicht", re.I)
YES = re.compile(r"^\s*(yes|ja|y)\b", re.I)
NO = re.compile(r"^\s*(no|nein|n)\b", re.I)
PLACEHOLDER = re.compile(r"^\s*(select|choose|please|bitte|--|—|-|\.\.\.)|^\s*$", re.I)


def choose_option(label: str, options: list[str], values: dict[str, str]) -> str:
    """Pick the option to select for a dropdown / radio question, or '' to leave it to the person."""
    real = [o for o in options if not PLACEHOLDER.match(o)]
    if not real:
        return ""
    key, value = match_field(label, values)
    shared = key in ("nationality", "date_of_birth", "salutation") and value  # the person chose to share these
    if SENSITIVE.search(label) and not shared:
        return next((o for o in real if PREFER_NOT.search(o)), "")
    if not value:
        if re.search(r"country|land\b|where .*(based|live|located)", label, re.I):
            value = values.get("country", "")
        elif re.search(r"how did you (hear|find)|wie .*erfahren|source", label, re.I):
            value = values.get("how_did_you_hear", "")
    if not value:
        return ""
    if YES.match(value) or NO.match(value):
        pattern = YES if YES.match(value) else NO
        return next((o for o in real if pattern.match(o)), "")
    low = value.lower()
    exact = next((o for o in real if o.strip().lower() == low), "")
    if exact:
        return exact
    contains = [o for o in real if o.strip().lower() in low or low in o.strip().lower()]
    if contains:
        return max(contains, key=len)
    words = set(re.findall(r"[a-zäöü]{4,}", low))
    scored = sorted(((len(words & set(re.findall(r"[a-zäöü]{4,}", o.lower()))), o) for o in real), reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    if key == "how_did_you_hear":
        return next((o for o in real if re.search(r"website|career|karriere|company|other|andere", o, re.I)), "")
    return ""


def claude_choose(question: str, options: list[str], job: dict, profile: dict) -> str:
    """Optional: let Claude pick an option for a question we have no saved answer for."""
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    real = [o for o in options if not PLACEHOLDER.match(o)]
    if not key or not real or SENSITIVE.search(question):
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        facts = {k: profile.get(k) for k in ("default_summary", "experience", "skills", "education",
                                             "languages", "relocation", "answers")}
        msg = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            max_tokens=50,
            system=("Pick the option that truthfully answers a job-application question for the candidate, "
                    "using ONLY the candidate facts. Reply with the exact option text, or NONE if the facts "
                    "do not clearly answer it."),
            messages=[{"role": "user", "content":
                       f"Question: {question}\nOptions:\n" + "\n".join(f"- {o}" for o in real) +
                       f"\n\nJob: {job.get('title')} at {job.get('company')}\n"
                       f"Candidate facts: {json.dumps(facts, ensure_ascii=False)[:6000]}"}],
        )
        answer = "".join(getattr(b, "text", "") for b in msg.content).strip().strip('"').lstrip("- ")
        return next((o for o in real if o.strip() == answer), "")
    except Exception as exc:
        log.warning("Claude choice failed: %s", exc)
        return ""


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
        "country": me.get("country", "Germany"),
        "xing": me.get("xing", ""),
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
    if YEARS_Q.search(label) and (EXPERIENCE_Q.search(label) or re.search(r"how many|wie viele", label, re.I)):
        low = label.lower()
        for tool in TOOL_YEARS:
            if re.search(rf"(?<![\w]){re.escape(tool)}", low):
                key = "years_" + tool.replace(" ", "_")
                return key, values.get(key, "")
        return "years_experience", values.get("years_experience", "")
    for pattern, key in FIELD_RULES:
        if pattern.search(label):
            return key, values.get(key, "")
    return "", ""


def fit_to_input(value: str, input_type: str) -> str:
    """Adapt a saved answer to the input type: numbers for number fields, YYYY-MM-DD for date fields."""
    if input_type == "number":
        m = re.search(r"\d{1,3}(?:[.,]\d{3})+(?!\d)|\d+(?:[.,]\d+)?", value)
        if not m:
            return ""
        text = m.group(0)
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", text):   # 55,000 or 55.000 -> 55000
            text = re.sub(r"[.,]", "", text)
        number = float(text.replace(",", "."))
        return str(int(round(number))) if number >= 1 else "1"
    if input_type == "date":
        m = re.match(r"\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})\s*$", value)
        if m:
            return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        return value if re.match(r"\d{4}-\d{2}-\d{2}$", value) else ""
    return value


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
            value = fit_to_input(value, info["type"]) if value else value
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

        # Dropdowns: saved answers first, then Claude (if a key is set); sensitive ones -> "prefer not to say".
        selects = page.locator("select")
        for i in range(selects.count()):
            el = selects.nth(i)
            info = el.evaluate(LABEL_JS)
            if not info["visible"]:
                continue
            options = el.evaluate("e => [...e.options].map(o => o.text.trim())")
            current = el.evaluate("e => e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : ''")
            if current and not PLACEHOLDER.match(current):
                continue
            choice = choose_option(info["label"], options, values) or claude_choose(info["label"], options, job, profile)
            if choice:
                el.select_option(label=choice)
                report.filled.append(f"{info['label']} = {choice}")
            elif info["required"]:
                report.needs_you.append(f"{info['label']} (choose yourself)")

        # Radio-button questions (Yes/No etc.), grouped by name.
        groups = page.evaluate("""() => {
          const out = {};
          for (const r of document.querySelectorAll('input[type=radio]')) {
            if (!r.name || !(r.offsetWidth || r.offsetHeight || r.getClientRects().length)) continue;
            const g = out[r.name] || (out[r.name] = {question: '', options: [], ids: [], checked: false, required: false});
            const lab = (r.id && document.querySelector(`label[for="${CSS.escape(r.id)}"]`)) || r.closest('label');
            g.options.push(((lab && lab.innerText) || r.value || '').trim());
            g.ids.push(r.id || '');
            g.checked = g.checked || r.checked;
            g.required = g.required || r.required;
            if (!g.question) {
              const box = r.closest('fieldset, [role=radiogroup], .application-question, [class*=question], [class*=field]');
              const legend = box && box.querySelector('legend, label:not(:has(input)), [class*=label], h3, h4, p');
              g.question = ((legend && legend.innerText) || (box && box.innerText.split('\\n')[0]) || r.name).trim();
            }
          }
          return out;
        }""")
        for name, g in groups.items():
            if g["checked"]:
                continue
            choice = choose_option(g["question"], g["options"], values) or \
                claude_choose(g["question"], g["options"], job, profile)
            if choice:
                idx = g["options"].index(choice)
                radio = page.locator(f'input[type=radio][name="{name}"]').nth(idx)
                radio.check()
                report.filled.append(f"{g['question'][:60]} = {choice}")
            else:
                report.needs_you.append(f"{g['question'][:60]} (choose yourself)")

        # Required checkboxes are usually privacy / terms consent: that is your decision, so they stay for you.
        boxes = page.locator("input[type=checkbox][required]")
        for i in range(boxes.count()):
            info = boxes.nth(i).evaluate(LABEL_JS)
            if info["visible"]:
                report.needs_you.append(f"{info['label'][:60]} (tick yourself)")
        combos = page.locator("[role=combobox]")
        for i in range(combos.count()):
            info = combos.nth(i).evaluate(LABEL_JS)
            if info["visible"] and info["required"] and not info["value"]:
                report.needs_you.append(f"{info['label'][:60]} (choose yourself)")

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
