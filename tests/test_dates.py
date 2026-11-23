from datetime import date

from jobhunter.dates import age_days, age_text, date_in_ad, normalize_date
from jobhunter.filters import evaluate
from jobhunter.models import Job

TODAY = date(2026, 11, 23)


def test_normalize_many_formats():
    assert normalize_date("2026-11-19T08:00:00Z") == "2026-11-19"
    assert normalize_date("October  2, 2026") == "2026-10-02"
    assert normalize_date("27 October 2026") == "2026-10-27"
    assert normalize_date("23.11.2026") == "2026-11-23"
    assert normalize_date("Mon, 23 Nov 2026 07:12:03 +0100") == "2026-11-23"
    assert normalize_date(1795000000000) == "2026-11-18"
    assert normalize_date("") == "" and normalize_date("soon") == ""


def test_date_at_top_of_ad():
    ad = "F Flink Data Engineer (m/f/d) Data Engineer Berlin, BE, Germany · 27 October 2026 €6,200,000 ..."
    assert date_in_ad(ad, TODAY) == "2026-10-27"
    assert date_in_ad("Start date: 1 January 2027 in Berlin", TODAY) == ""      # future = not a posting date


def test_age():
    assert age_days("2026-11-16", TODAY) == 7
    assert age_text("2026-11-23", TODAY) == "today" and age_text("2026-11-22", TODAY) == "yesterday"


CFG = {"title_keywords": ["data"], "locations": ["berlin"], "german_level": "A2", "skills": {"sql": 3},
       "max_job_age_days": 7}
TEXT = "We are an English speaking team in Berlin and you write SQL every day for our reports. " * 5


def _job(posted="", seen=""):
    return Job(source="t", external_id="1", title="Data Analyst", company="A", location="Berlin",
               url="https://x/1", description=TEXT, posted_at=posted, extra={"collected_at": seen})


def test_old_jobs_are_dropped():
    today = date.today().isoformat()
    assert evaluate(_job("2020-01-01"), CFG).excluded_reason.startswith("posted ")
    assert not evaluate(_job(today), CFG).excluded_reason
    assert evaluate(_job("", "2020-01-01T10:00:00"), CFG).excluded_reason.startswith("first seen")
    assert not evaluate(_job("2020-01-01"), {**CFG, "max_job_age_days": 0}).excluded_reason
