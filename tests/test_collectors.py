import json

from conftest import FIX

from jobhunter.collectors import arbeitsagentur, ats, email_alerts


def test_greenhouse_parse_unescapes_html():
    jobs = ats.parse_greenhouse(json.loads((FIX / "greenhouse.json").read_text()), "Acme")
    assert len(jobs) == 3
    job = jobs[0]
    assert job.title == "Data Engineer"
    assert job.location == "Berlin, Germany"
    assert job.posted_at == "2026-11-13"
    assert "<p>" not in job.description and "dbt models" in job.description


def test_lever_parse_merges_lists_and_remote():
    jobs = ats.parse_lever(json.loads((FIX / "lever.json").read_text()), "Beta")
    assert jobs[0].remote is True
    assert jobs[0].apply_url.endswith("/apply")
    assert "0-2 years" in jobs[0].description
    assert jobs[0].posted_at.startswith("2025-") or jobs[0].posted_at.startswith("2026-")


def test_ashby_skips_unlisted():
    jobs = ats.parse_ashby(json.loads((FIX / "ashby.json").read_text()), "Gamma")
    assert [j.title for j in jobs] == ["Business Intelligence & Automations Engineer"]
    assert jobs[0].location == "Berlin, Vienna"


def test_personio_parse():
    jobs = ats.parse_personio((FIX / "personio.xml").read_text(), "Zeta", "zeta")
    assert jobs[0].company == "Zeta Energy GmbH"
    assert jobs[0].location == "Berlin, Hamburg"
    assert jobs[0].url == "https://zeta.jobs.personio.de/job/555?language=en"
    assert "Power BI" in jobs[0].description
    assert jobs[0].extra["seniority"] == "entry-level"


def test_arbeitsagentur_fetch_only_details_for_kept(session):
    keep = lambda j: "Koch" not in j.title  # noqa: E731
    jobs = arbeitsagentur.fetch(session, {"queries": ["Data Engineer"], "where": "Berlin"}, keep=keep)
    assert {j.company for j in jobs} == {"Delta GmbH", "Epsilon AG"}
    detail_calls = [u for u, _ in session.calls if "jobdetails" in u]
    assert len(detail_calls) == 2
    delta = next(j for j in jobs if j.company == "Delta GmbH")
    assert delta.apply_url == "https://delta.example/jobs/42"
    assert "dbt" in delta.description


def test_email_alert_parsing():
    jobs = email_alerts.parse_alert_html((FIX / "linkedin_alert.html").read_text())
    by_site = {j.extra["site"]: j for j in jobs}
    assert set(by_site) == {"linkedin", "stepstone", "indeed"}
    li = by_site["linkedin"]
    assert li.title == "Analytics Engineer"
    assert li.company == "Omega Labs"
    assert li.url == "https://www.linkedin.com/jobs/view/4012345678/"
    assert by_site["stepstone"].company == "Sigma GmbH"
    assert by_site["indeed"].url == "https://de.indeed.com/viewjob?jk=a1b2c3d4e5f6"


def test_detect_ats():
    html = """<iframe src="https://job-boards.eu.greenhouse.io/embed/job_board?for=talonone"></iframe>
              <a href="https://jobs.lever.co/pitch/123">x</a>
              <a href="https://jobs.ashbyhq.com/flinn">y</a>
              <a href="https://acme.jobs.personio.de/job/1">z</a>"""
    found = ats.detect_ats(html)
    assert {"ats": "greenhouse", "slug": "talonone", "region": "eu"} in found
    assert {"ats": "lever", "slug": "pitch"} in found
    assert {"ats": "ashby", "slug": "flinn"} in found
    assert {"ats": "personio", "slug": "acme"} in found


def test_fetch_company_unknown_board_returns_empty(session):
    assert ats.fetch_company(session, {"name": "Nope", "ats": "greenhouse", "slug": "nope"}) == []
    assert ats.fetch_company(session, {"name": "Nope", "ats": "workday", "slug": "x"}) == []
