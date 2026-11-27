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
    job = evaluate(_job(desc=GOOD + " Fluent German is required."), {**config["search"], "german_ads": "skip"})
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


def test_every_30_minutes():
    from datetime import datetime
    from jobhunter.__main__ import due_interval
    now = datetime(2026, 11, 18, 10, 0)
    assert due_interval(now, 30, "")                              # never ran
    assert due_interval(now, 30, "2026-11-18")                    # old date-only marker
    assert not due_interval(now, 30, "2026-11-18T09:45")          # 15 min ago
    assert due_interval(now, 30, "2026-11-18T09:30")              # 30 min ago
    assert due_interval(now, 30, "2026-11-17T22:10")              # laptop was off overnight


def test_match_score_ranks_like_a_recruiter(config):
    cfg = config["search"]
    ideal = _job("Junior Data Engineer", "Our working language is English. You build ETL pipelines in Python and SQL, "
                 "model data with dbt on PostgreSQL, orchestrate with Airflow and run everything in Docker. "
                 "0-2 years of experience.")
    partial = _job("Data Engineer", "Ingestion from Postgres to BigQuery. Must have: Airflow, Dagster, or similar; "
                   "Kubernetes; PostgreSQL; dbt. Nice to have: Kafka, Google Cloud Platform. 2-4 years experience.")
    cloud = _job("Data Engineer", "Build pipelines with Spark, Kafka and Databricks on AWS with Terraform. "
                 "Python and SQL required. 3 years experience. English speaking team.")
    scores = [evaluate(j, cfg).score for j in (ideal, partial, cloud)]
    assert scores[0] >= 90
    assert scores[0] > scores[1] > scores[2]


def test_alternatives_and_nice_to_haves_are_not_full_gaps(config):
    cfg = config["search"]
    job = evaluate(_job("Data Engineer", "You orchestrate with Airflow, Dagster or similar. SQL and dbt. "
                        "Kafka is a plus."), cfg)
    assert "dagster" not in job.extra["missing_skills"]          # alternative to Airflow, which you have
    assert "kafka" in job.extra["missing_skills"]                # optional, but still listed as a gap
    assert job.score >= 80


def test_european_cities_outside_germany_are_dropped(config):
    cfg = dict(config["search"], anywhere_in_germany=True)
    for loc in ("Bordeaux", "Drachten, Friesland, Nederland", "Graz", "Kraków"):
        assert evaluate(_job("Data Engineer", "SQL Python", location=loc), cfg).excluded_reason, loc
    for loc in ("Cologne", "Berlin", "Freiburg im Breisgau", "Germany"):
        assert not evaluate(_job("Data Engineer", "SQL Python", location=loc), cfg).excluded_reason, loc


RITTAL = """Completed studies in (business) informatics, statistics, data science or quantitatively oriented economics or comparable qualification
Professional experience in Business Intelligence, Data Analytics or Data Science, ideally in B2B sales, marketing or e-commerce
Good SQL and Python skills; experience with automated ETL processes, confident use of SSMS, experience with Windows VMs
Solid experience with Power BI in complex data models and KPI dashboards; SAP BW, SAP Datasphere/SAC, GA4 and Contentsquare are desirable.
Excellent German and English skills, both spoken and written.
Strong analytical skills, good understanding of complex relationships
Communication and teamwork skills as well as entrepreneurial thinking"""


def test_rittal_ad_shows_what_is_missing_and_scores_honestly():
    from jobhunter.filters import evaluate
    from jobhunter.models import Job
    cfg = {"title_keywords": ["data analyst", "business intelligence"], "locations": ["germany"],
           "german_level": "A2",
           "skills": {"sql": 3, "python": 2, "power bi": 1, "etl": 2, "dbt": 3, "airflow": 3}}
    job = evaluate(Job(source="email", external_id="r", company="Rittal", title="Data Analyst Business Intelligence (m/w/d)",
                       url="u", location="Herborn, Germany", description=RITTAL), cfg)
    missing = job.extra["missing_skills"]
    for tool in ("ssms", "windows vms", "sap bw", "datasphere", "ga4", "contentsquare"):
        assert tool in missing, tool
    assert "ssms" in job.extra["missing_must"]                  # "confident use of SSMS"
    assert "ga4" not in job.extra["missing_must"]               # "are desirable"
    assert job.extra.get("german", "").startswith("C1")         # "Excellent German"
    assert "sap" not in missing                                 # counted once, as "sap bw"
    assert job.score <= 60


def test_years_written_as_words():
    from jobhunter.filters import min_years_required as years
    assert years("Mindestens drei Jahre Erfahrung im Data Engineering") == 3
    assert years("zwei bis drei Jahre Berufserfahrung") == 2
    assert years("two to three years of experience") == 2
    assert years("Erfahrung mit mindestens einer Cloud-Plattform (AWS, Azure oder GCP)") is None


def test_cloud_alternatives_you_have_are_not_missing():
    from jobhunter.filters import evaluate
    from jobhunter.models import Job
    ad = "Erfahrung mit mindestens einer Cloud-Plattform (AWS, Azure oder GCP). Snowflake und Datavault Builder."
    job = evaluate(Job(source="email", external_id="s", company="sonotact", title="Data Engineer", url="u",
                       location="Germany", description=ad),
                   {"title_keywords": ["data engineer"], "locations": ["germany"], "skills": {"google cloud": 2}})
    assert "aws" not in job.extra["missing_skills"] and "azure" not in job.extra["missing_skills"]
    assert {"snowflake", "datavault builder"} <= set(job.extra["missing_skills"])


def test_us_locations_are_abroad():
    from jobhunter.filters import location_matches
    from jobhunter.models import Job
    cfg = {"locations": ["germany", "remote"], "anywhere_in_germany": True}
    for loc in ("US", "Remote - US", "Remote (US)"):
        assert location_matches(Job(source="x", external_id="1", company="c", title="t", url="u", location=loc), cfg)
    assert not location_matches(Job(source="x", external_id="1", company="c", title="t", url="u", location="Berlin"), cfg)
