import json
from pathlib import Path

from docx import Document

from jobhunter.pipeline import run
from jobhunter.storage import Store


def test_full_run_and_dedup(settings, session):
    result = run(settings, send_digest=False, session=session)

    store = Store(settings.database_url)
    titles = {j["title"] for j in store.list(limit=100)}
    # kept: good English jobs from every source
    assert {"Data Engineer", "Analytics Engineer", "Business Intelligence & Automations Engineer",
            "Junior Data Analyst", "Junior Data Engineer (m/w/d)"} <= titles
    assert "Data Engineer" in titles  # Munich (Personio) is kept: anywhere in Germany is allowed
    # dropped: senior, German-required, US-based, German-language posting
    skipped = {j["title"]: j["excluded_reason"] for j in store.list(status="skipped", limit=100)}
    assert "Senior Data Engineer" in skipped
    assert skipped["Data Analyst (m/w/d)"].startswith("needs German")
    assert "Datenanalyst (m/w/d)" in skipped  # written in German
    assert "Koch (m/w/d)" not in titles | set(skipped)  # never fetched in detail

    assert "Data Engineer (m/f/d)" in titles  # from Arbeitnow
    assert result["new"] == 7
    assert result["prepared"] == 5  # top 5 get documents
    for job in store.list(status="prepared"):
        assert Path(job["cv_path"]).exists() and Path(job["letter_path"]).exists()
        folder = Path(job["cv_path"]).parent
        assert json.loads((folder / "tailoring.json").read_text())["engine"] == "fallback"

    assert Path(result["tracker"]).read_text().count("\n") >= 6
    assert "new matching jobs" in Path(result["digest"]).read_text()

    # second run: nothing new
    again = run(settings, send_digest=False, session=session)
    assert again["new"] == 0 and again["prepared"] == 0


def test_cv_contains_only_profile_facts(settings, session):
    settings.config["tailoring"]["top_n"] = 10
    run(settings, send_digest=False, session=session)
    store = Store(settings.database_url)
    job = next(j for j in store.list(status="prepared") if j["company"] == "Gamma")
    text = "\n".join(p.text for p in Document(job["cv_path"].replace(".pdf", ".docx")).paragraphs)
    assert "n8n" in text
    # tools from the job ad that are not in the profile must not appear in the CV
    for tool in ("BigQuery", "Fivetran"):
        assert tool not in text
    letter = "\n".join(p.text for p in Document(job["letter_path"].replace(".pdf", ".docx")).paragraphs)
    assert "Dear Gamma Hiring Team" in letter
    assert "BigQuery" in letter and "would be new to me" in letter  # honest gap, not a claim


def test_ad_text_is_copied_from_the_same_job_on_another_site(tmp_path):
    from jobhunter.models import Job
    from jobhunter.pipeline import copy_descriptions_from_twins
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    glassdoor = Job("email", "glassdoor:1", "Statista", "Analytics Engineer (m/f/d)", "https://www.glassdoor.de/job-listing/j?jl=1")
    databerlin = Job("databerlin", "analytics-engineer-at-statista", "Statista", "Analytics Engineer",
                     "https://databerlin.net/jobs/analytics-engineer-at-statista",
                     description="We build dbt models on BigQuery. SQL and Python. " * 5,
                     apply_url="https://statista.jobs.personio.de/job/1")
    store.save([glassdoor, databerlin])
    assert copy_descriptions_from_twins(store) == 1
    row = store.get(glassdoor.uid)
    assert "dbt models" in row["description"] and row["apply_url"].startswith("https://statista")


def test_jobs_that_still_need_the_employer_link():
    from jobhunter.pipeline import needs_employer_link
    assert needs_employer_link({"url": "https://databerlin.net/jobs/x-at-y", "apply_url": ""})
    assert needs_employer_link({"url": "https://www.linkedin.com/jobs/view/1/", "apply_url": "https://www.linkedin.com/jobs/view/1/"})
    assert not needs_employer_link({"url": "https://databerlin.net/jobs/x-at-y", "apply_url": "https://y.jobs.personio.de/job/1"})
    assert not needs_employer_link({"url": "https://jobs.ashbyhq.com/moss/1", "apply_url": ""})
