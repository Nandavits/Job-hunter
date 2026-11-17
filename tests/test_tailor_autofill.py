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
    job = {"company": "Acme", "title": "Data Engineer",
           "apply_url": (FIX / "forms" / f"{form}.html").as_uri()}
    report = fill(job, profile, {"cv_pdf": str(cv)}, ["One.", "Two.", "Three.", "Four."],
                  headless=True, inspect=inspect)
    assert seen["title"] != "SUBMITTED"
    assert seen["email"] == profile["personal"]["email"]
    assert seen["files"][0] == 1
    assert report.filled
