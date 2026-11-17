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

    assert result["new"] == 6
    assert result["prepared"] == 5  # the Munich job's description is too short to tailor from
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
