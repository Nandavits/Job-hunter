import json
from types import SimpleNamespace

import pytest

from jobhunter.autofill import match_field, value_map
from jobhunter.tailor import fallback, tailor

JOB = {"company": "Acme", "title": "Analytics Engineer", "location": "Berlin",
       "description": "dbt, SQL, Metabase, Airflow, BigQuery. English working language."}


class FakeClient:
    def __init__(self, payload):
        self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
            content=[SimpleNamespace(text=payload)]))


def test_fallback_orders_relevant_first(profile):
    t = fallback(profile, JOB)
    assert t.title == "Analytics Engineer"
    assert t.skills_order[0] in ("Data Engineering", "BI & Visualisation", "Languages")
    assert t.bullet_order[0][0] == 0  # ownership bullet stays first
    assert "BigQuery" in t.gaps
    assert len(t.letter_paragraphs) == 4


def test_claude_output_is_validated(profile):
    payload = "```json\n" + json.dumps({
        "title": "Analytics Engineer", "summary": "S", "bullet_order": {"0": [3, 99, 3, 1]},
        "skills_order": ["Tools", "Made Up Category"], "cover_letter": ["a", "b", "c", "d"],
        "fit_notes": ["x"], "gaps": ["BigQuery"]}) + "\n```"
    t = tailor(profile, JOB, client=FakeClient(payload))
    assert t.engine == "claude"
    n0 = len(profile["experience"][0]["bullets"])
    assert t.bullet_order[0][:2] == [3, 1] and sorted(t.bullet_order[0]) == list(range(n0))
    assert t.skills_order[0] == "Tools" and "Made Up Category" not in t.skills_order
    assert len(t.skills_order) == len(profile["skills"])


def test_claude_failure_falls_back(profile):
    t = tailor(profile, JOB, client=FakeClient("not json"))
    assert t.engine == "fallback"


@pytest.mark.parametrize("label, key", [
    ("First Name *", "first_name"), ("Last name", "last_name"), ("Name", "full_name"),
    ("Email address", "email"), ("Phone", "phone"), ("LinkedIn Profile", "linkedin"),
    ("GitHub URL", "github"), ("Website", "github"), ("Salary expectations (EUR)", "salary_expectation"),
    ("Earliest start date", "earliest_start_date"), ("Will you require visa sponsorship?", "requires_sponsorship"),
    ("Are you authorized to work in Germany?", "work_authorization"),
    ("What is your level of German?", "german_level"), ("Current company", "current_company"),
    ("Cover Letter", "cover_letter_text"), ("Favourite colour", ""),
])
def test_match_field(label, key, profile):
    assert match_field(label, value_map(profile))[0] == key


def test_empty_answers_are_not_filled(profile):
    values = value_map(profile)
    assert match_field("Salary expectations", values) == ("salary_expectation", "")


def _chromium_available():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not installed")
@pytest.mark.parametrize("form", ["greenhouse_like", "lever_like"])
def test_autofill_fills_but_never_submits(form, profile, tmp_path):
    from conftest import FIX
    from jobhunter.autofill import fill

    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    seen = {}

    def inspect(page):
        seen["title"] = page.title()
        seen["email"] = page.locator("input[type=email]").first.input_value()
        seen["files"] = page.evaluate("() => [...document.querySelectorAll('input[type=file]')]"
                                      ".map(i => i.files.length)")
        seen["why"] = page.locator("textarea").first.input_value()
        seen["selects"] = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('select')]"
                                        ".map(s => [s.id, s.options[s.selectedIndex].text]))")
        seen["sponsor"] = page.evaluate("() => (document.querySelector('input[name=sponsor]:checked') || {}).value")
        seen["consent"] = page.evaluate("() => (document.querySelector('#consent') || {}).checked")
    job = {"company": "Acme", "title": "Data Engineer",
           "apply_url": (FIX / "forms" / f"{form}.html").as_uri()}
    profile = dict(profile, answers=dict(profile["answers"], work_authorization="Yes, I am allowed to work in Germany",
                                         requires_sponsorship="No", salary_expectation="55,000 EUR"))
    report = fill(job, profile, {"cv_pdf": str(cv)}, ["One.", "Two.", "Three.", "Four."],
                  headless=True, inspect=inspect)
    assert seen["title"] != "SUBMITTED"
    assert seen["email"] == profile["personal"]["email"]
    assert seen["files"][0] == 1
    assert report.filled
    if form == "greenhouse_like":  # "Why do you want to join us?" answered from the letter's first paragraph
        assert seen["why"] == "One."
        assert any("Why do you want to join us" in d for d in report.drafted)
        assert seen["selects"] == {"q4": "Yes", "country": "Germany", "gender": "I prefer not to say",
                                   "hear": "Company website", "ethn": "Select"}  # no 'prefer not' option -> left
        assert seen["sponsor"] == "0"            # "No" sponsorship needed
        assert seen["consent"] is False          # consent stays the person's decision
        assert any("privacy policy" in n for n in report.needs_you)
        assert any("Ethnicity" in n for n in report.needs_you) is False  # optional, so not listed


def test_letter_mentions_relocation(profile):
    t = fallback(dict(profile, relocation="Open to relocating anywhere in Germany"), JOB)
    assert "open to relocating anywhere in germany" in t.letter_paragraphs[-1].lower()
    t = fallback({k: v for k, v in profile.items() if k != "relocation"}, JOB)
    assert "relocat" not in t.letter_paragraphs[-1].lower()


@pytest.mark.parametrize("label, key", [
    ("Why do you want to join us? *", "why_us"),
    ("Why are you interested in this role?", "why_us"),
    ("What motivates you to apply?", "why_us"),
    ("Warum möchtest du bei uns arbeiten?", "why_us"),
    ("Why are you a good fit for this position?", "why_you"),
    ("Describe a project you are proud of", "why_you"),
    ("Tell us about yourself", "about_you"),
    ("Favourite colour", ""),
])
def test_match_question(label, key):
    from jobhunter.autofill import match_question
    assert match_question(label) == key


def test_drafts_come_from_letter_and_profile(profile):
    from jobhunter.autofill import draft_answers
    d = draft_answers(profile, ["Para about company.", "Para about evidence.", "x", "y"])
    assert d["why_us"] == "Para about company."
    assert d["why_you"] == "Para about evidence."
    assert d["about_you"].startswith("I am a data engineer with production experience")
    assert "I am strong in SQL" in d["about_you"]


@pytest.mark.parametrize("label, options, expected", [
    ("Are you authorized to work in Germany?", ["Select", "Yes", "No"], "Yes"),
    ("Do you require visa sponsorship?", ["Please select", "Yes", "No"], "No"),
    ("Gender", ["Male", "Female", "Prefer not to say"], "Prefer not to say"),
    ("Gender", ["Male", "Female"], ""),
    ("Disability status", ["Yes", "No", "I don't wish to answer"], "I don't wish to answer"),
    ("Country", ["--", "Austria", "Germany"], "Germany"),
    ("How did you hear about this job?", ["LinkedIn", "Karriereseite", "Other"], "Karriereseite"),
    ("Favourite colour", ["Red", "Blue"], ""),
])
def test_choose_option(label, options, expected, profile):
    from jobhunter.autofill import choose_option
    profile = dict(profile, answers=dict(profile["answers"], work_authorization="Yes, allowed",
                                         requires_sponsorship="No"))
    assert choose_option(label, options, value_map(profile)) == expected


FULL_ANSWERS = dict(salutation="Mr", street="Musterstraße 1", postal_code="10115", date_of_birth="01.02.2000",
                    nationality="Italian", driving_licence="No", years_experience="1.5", years_sql="2",
                    years_dbt="0.5", work_model="Open to remote, hybrid or on-site", employment_type="Full-time",
                    travel="Yes, willing to travel", residence_permit="Student residence permit",
                    requires_sponsorship="No", applied_before="No", referred="No", highest_degree="Master's degree",
                    earliest_start_date="Immediately")


@pytest.mark.parametrize("label, expected", [
    ("Email address *", "jane.doe@example.com"),
    ("Street address", "Musterstraße 1"),
    ("Postleitzahl", "10115"),
    ("Anrede", "Mr"),
    ("Geburtsdatum", "01.02.2000"),
    ("Führerschein vorhanden?", "No"),
    ("How many years of professional experience do you have?", "1.5"),
    ("Years of experience with SQL", "2"),
    ("Wie viele Jahre Berufserfahrung haben Sie?", "1.5"),
    ("Preferred working model (remote/hybrid/on-site)", "Open to remote, hybrid or on-site"),
    ("Were you referred by an employee?", "No"),
    ("Residence permit type", "Student residence permit"),
    ("Will you require visa sponsorship?", "No"),
    ("Kündigungsfrist", "Immediately"),
    ("Job Title at current company", "Working Student, AI Engineering"),
    ("Current company", "Example Company"),
])
def test_more_application_questions(label, expected, profile):
    values = value_map(dict(profile, answers=dict(profile["answers"], **FULL_ANSWERS)))
    assert match_field(label, values)[1] == expected


def test_number_and_date_fields():
    from jobhunter.autofill import fit_to_input
    assert fit_to_input("2", "number") == "2"
    assert fit_to_input("0.5", "number") == "1"
    assert fit_to_input("55,000 - 60,000 EUR", "number") == "55000"  # number field: lower end of the range
    assert fit_to_input("55.000 €", "number") == "55000"
    assert fit_to_input("1.5", "number") == "2"
    assert fit_to_input("01.02.2000", "date") == "2000-02-01"
    assert fit_to_input("December 2026", "date") == ""


def test_shared_nationality_dropdown_but_not_gender(profile):
    from jobhunter.autofill import choose_option
    values = value_map(dict(profile, answers=dict(profile["answers"], **FULL_ANSWERS)))
    assert choose_option("Nationality", ["German", "Italian"], values) == "Italian"
    assert choose_option("Gender", ["Male", "Female"], values) == ""
    assert choose_option("Highest degree", ["Bachelor's Degree", "Master's Degree"], values) == "Master's Degree"


def test_asked_for_skills_go_first_and_nothing_is_invented(profile):
    from jobhunter.tailor import fallback
    job = {"title": "Data Engineer", "company": "X",
           "description": "We use Docker, PostgreSQL and Kubernetes. Python is a must."}
    t = fallback(profile, job)
    assert "Docker" in " ".join(t.skill_priority)
    assert not any("kubernetes" in s.lower() for s in t.skill_priority)   # you don't have it: never added
    assert "Kubernetes" in t.gaps


def test_bare_or_homepage_links_are_handled():
    from jobhunter.autofill import normalize_url, is_homepage, target_url
    assert normalize_url("www.allianzdeutschland.de") == "https://www.allianzdeutschland.de"
    assert is_homepage("www.allianzdeutschland.de") and not is_homepage("https://x.de/jobs/123")
    job = {"apply_url": "www.allianzdeutschland.de", "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/1"}
    assert target_url(job).startswith("https://www.arbeitsagentur.de")
    assert target_url({"apply_url": "x.de/apply/7", "url": "https://a.de"}) == "https://x.de/apply/7"
