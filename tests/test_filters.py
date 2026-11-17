import pytest

from jobhunter.filters import (evaluate, filter_jobs, min_years_required, required_german_level,
                               written_in_german)
from jobhunter.models import Job


@pytest.mark.parametrize("text, level", [
    ("Fluent German and English are required.", "C1"),
    ("You speak German at C1 level.", "C1"),
    ("Sehr gute Deutschkenntnisse in Wort und Schrift.", "C1"),
    ("Fließend Deutsch und gutes Englisch.", "C1"),
    ("Good command of German is required.", "B2"),
    ("German (B2) required.", "B2"),
    ("German is a plus.", ""),
    ("Fluent German is nice to have.", ""),
    ("Deutschkenntnisse sind von Vorteil.", ""),
    ("Our working language is English.", ""),
    ("You will work with our partners in Germany.", ""),
])
def test_required_german_level(text, level):
    assert required_german_level(text)[0] == level


def test_written_in_german():
    de = ("Wir suchen dich für unser Team. Du arbeitest mit SQL und bist die Schnittstelle zu den Fachbereichen "
          "und hast Freude an der Arbeit im Team und mit uns. ") * 4
    en = ("We are looking for you to join our team and work with SQL as the interface to our business teams "
          "and you enjoy working in a team with us. ") * 4
    assert written_in_german(de)
    assert not written_in_german(en)


@pytest.mark.parametrize("text, years", [
    ("2-4 years of experience in data engineering", 2),
    ("At least 5+ years experience with Python", 5),
    ("Mindestens 3 Jahre Berufserfahrung", 3),
    ("Founded 10 years ago, we serve customers.", None),
])
def test_min_years(text, years):
    assert min_years_required(text) == years


def _job(title="Data Engineer", desc="", location="Berlin", **kw):
    return Job(source="t", external_id=title, company="X", title=title, url="u", location=location,
               description=desc, **kw)


GOOD = ("Our working language is English. Build dbt models, SQL, Airflow, Docker, PostgreSQL pipelines, "
        "data quality checks and ETL. 1-2 years of experience. ") * 3


def test_evaluate_drops_senior_title(config):
    assert "senior" in evaluate(_job("Senior Data Engineer", GOOD), config["search"]).excluded_reason


def test_evaluate_drops_german_required(config):
    job = evaluate(_job(desc=GOOD + " Fluent German is required."), config["search"])
    assert job.german_required and job.excluded_reason.startswith("needs German C1")


def test_evaluate_keeps_and_scores(config):
    job = evaluate(_job("Junior Data Engineer", GOOD), config["search"])
    assert job.excluded_reason == ""
    assert job.score >= 70
    assert {"dbt", "airflow", "sql"} <= set(job.matched_skills)


def test_evaluate_location_berlin_only(config):
    cfg = dict(config["search"], anywhere_in_germany=False)
    assert evaluate(_job(desc=GOOD, location="New York, United States"), cfg).excluded_reason
    assert evaluate(_job(desc=GOOD, location="Munich"), cfg).excluded_reason
    assert not evaluate(_job(desc=GOOD, location="Remote - Germany"), cfg).excluded_reason
    assert not evaluate(_job(desc=GOOD, location="Munich", remote=True), cfg).excluded_reason


def test_evaluate_location_anywhere_in_germany(config):
    cfg = dict(config["search"], anywhere_in_germany=True)
    for city in ("Munich", "München", "Hamburg", "Oldenburg", "Bochum", "Frankfurt am Main", "Remote - Germany",
                 "Berlin, Vienna", "Bundesweit"):
        assert not evaluate(_job(desc=GOOD, location=city), cfg).excluded_reason, city
    for city in ("New York, United States", "London, UK", "Vienna", "Wien, Österreich", "Amsterdam",
                 "Zürich, Schweiz", "Remote - Poland"):
        assert evaluate(_job(desc=GOOD, location=city), cfg).excluded_reason, city


def test_evaluate_years(config):
    job = evaluate(_job(desc=GOOD.replace("1-2 years", "6+ years")), config["search"])
    assert "6+ years" in job.excluded_reason


def test_email_jobs_without_description_are_kept(config):
    kept, dropped = filter_jobs([_job("Analytics Engineer", "")], config["search"])
    assert len(kept) == 1 and not dropped


def test_schedule_catches_up_after_laptop_was_off():
    from datetime import datetime
    from jobhunter.__main__ import due_today
    assert not due_today(datetime(2026, 11, 18, 7, 59), "08:00", "2026-11-17")   # too early
    assert due_today(datetime(2026, 11, 18, 8, 0), "08:00", "2026-11-17")        # on time
    assert due_today(datetime(2026, 11, 18, 11, 30), "08:00", "2026-11-17")      # laptop opened late
    assert not due_today(datetime(2026, 11, 18, 15, 0), "08:00", "2026-11-18")   # already ran today


def test_app_password_spaces_and_quotes_removed(monkeypatch):
    from jobhunter.config import Settings
    monkeypatch.setenv("GMAIL_APP_PASSWORD", '"abcd efgh ijkl mnop"\r')
    monkeypatch.setenv("GMAIL_ADDRESS", " me@example.com ")
    s = Settings(config={}, profile={})
    assert s.env("GMAIL_APP_PASSWORD") == "abcdefghijklmnop"
    assert s.env("GMAIL_ADDRESS") == "me@example.com"


def test_documents_made_in_docker_are_found_locally(tmp_path, monkeypatch):
    from jobhunter import __main__ as cli
    monkeypatch.setattr(cli, "OUTPUT_DIR", tmp_path)
    (tmp_path / "2026-11-17_Acme_Data_Engineer").mkdir()
    cv = tmp_path / "2026-11-17_Acme_Data_Engineer" / "CV.pdf"
    cv.write_bytes(b"%PDF")
    assert cli.local_path("/app/output/2026-11-17_Acme_Data_Engineer/CV.pdf") == cv
    assert cli.local_path(str(cv)) == cv
    assert not cli.local_path("/app/output/missing/CV.pdf").exists()
