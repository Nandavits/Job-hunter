"""The browser dashboard: pages, filters, status changes, prepare/apply buttons and its safety checks."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobhunter import dashboard
from jobhunter.dashboard import charts_data, country_of, create_app, web_link
from jobhunter.models import Job
from jobhunter.storage import Store

AD = "English speaking team. SQL, Python, dbt and Airflow. Proven hands-on experience with Databricks is required. " * 3


class S:                                      # just what the dashboard needs from Settings
    search = {"skills": {"sql": 3, "python": 2, "dbt": 3}, "also_countries": ["netherlands", "india"]}
    database_url = ""


@pytest.fixture
def app(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'jobs.db'}")
    jobs = [Job(source="greenhouse", external_id="1", company="Adyen", title="Data Engineer", url="https://x/1",
                location="Amsterdam", description=AD, posted_at="2026-11-20"),
            Job(source="arbeitnow", external_id="2", company="Acme <b>bold</b>", title="Data Analyst", url="https://x/2",
                location="Berlin", description="SQL and Python. English speaking team. " * 4),
            Job(source="email", external_id="3", company="Moss", title="Applied AI Engineer", url="https://x/3",
                location="Berlin")]
    store.save(jobs)
    for j, score in zip(jobs, (55, 88, 70)):
        store.update(j.uid, score=score)
    store.update(jobs[2].uid, status="applied", applied_at="2026-11-17")
    launched = []
    client = TestClient(create_app(S(), store, launcher=launched.append, checker=lambda job: None))
    token = re.search(r'name="token" value="([^"]+)"', client.get(f"/job/{jobs[0].uid}").text).group(1)
    return {"client": client, "store": store, "jobs": jobs, "launched": launched, "token": token}


def test_pages_and_filters(app):
    c, jobs = app["client"], app["jobs"]
    page = c.get("/").text
    assert "Adyen" in page and "Data Analyst" in page and "Applied AI Engineer" not in page   # open jobs only
    assert "MISSING MUST-HAVE: databricks" in page                                            # the Allianz lesson
    assert "&lt;b&gt;bold" in page and "<b>bold</b>" not in page                              # no HTML from ads
    nl = c.get("/?country=Netherlands").text
    assert "Adyen" in nl and "Data Analyst" not in nl
    assert "Adyen" not in c.get("/?min_score=60").text
    assert "Moss" in c.get("/tracker").text
    charts = c.get("/charts").text
    assert "applications" in charts and "<svg" in charts


def test_status_change_needs_the_page_token(app):
    c, store, j = app["client"], app["store"], app["jobs"][1]
    assert c.post(f"/job/{j.uid}/status", data={"status": "applied"}).status_code == 400
    r = c.post(f"/job/{j.uid}/status", data={"status": "applied", "token": app["token"]}, follow_redirects=False)
    assert r.status_code == 303 and store.get(j.uid)["status"] == "applied" and store.get(j.uid)["applied_at"]


def test_other_websites_cannot_post(app):
    j = app["jobs"][1]
    r = app["client"].post(f"/job/{j.uid}/status", data={"status": "skipped", "token": app["token"]},
                           headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and app["store"].get(j.uid)["status"] == "new"


def test_apply_opens_the_form_filler_never_submits(app):
    j = app["jobs"][0]
    r = app["client"].post(f"/job/{j.uid}/apply", data={"token": app["token"]})
    assert app["launched"] == [j.uid] and "click Submit yourself" in r.text


def test_prepare_runs_in_the_background(app, monkeypatch):
    import jobhunter.pipeline as pipeline
    done = []
    monkeypatch.setattr(pipeline, "prepare_job", lambda settings, store, job: done.append(job["uid"]))
    j = app["jobs"][0]
    app["client"].post(f"/job/{j.uid}/prepare", data={"token": app["token"]})
    import time
    for _ in range(50):
        if done:
            break
        time.sleep(0.02)
    assert done == [j.uid]


def test_files_only_from_the_output_folder(app, tmp_path):
    j = app["jobs"][0]
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    app["store"].update(j.uid, cv_path=str(secret))
    assert app["client"].get(f"/file/{j.uid}/cv").status_code == 404


def test_helpers():
    cfg = {"also_countries": ["india"]}
    assert country_of("Amsterdam", cfg) == "Netherlands" and country_of("Hyderabad, India", cfg) == "India"
    assert country_of("Berlin", cfg) == "Germany" and country_of("", cfg) == "Germany"
    assert country_of("London, UK", cfg) == "UK" and country_of("Remote", cfg) == "Remote"
    assert web_link("www.allianzdeutschland.de") == "https://www.allianzdeutschland.de"
    assert web_link("https://a.de/x") == "https://a.de/x" and web_link("") == ""
    import datetime as dt
    rows = [{"status": "applied", "applied_at": "2026-11-17", "source": "email", "location": "Berlin",
             "collected_at": "2026-11-17"},
            {"status": "rejected", "applied_at": "2026-11-18", "source": "email", "location": "Berlin",
             "collected_at": "2026-11-18"},
            {"status": "new", "applied_at": "", "source": "arbeitnow", "location": "Amsterdam", "collected_at": "2026-11-20"}]
    d = charts_data(rows, {"also_countries": ["netherlands"]}, today=dt.date(2026, 11, 24))
    assert d["applied"] == 2 and d["reply_rate"] == 50 and ("Netherlands", 1) in d["by_country"]
    assert sum(v for _, v in d["per_week"]) == 2


def test_upload_folder_holds_only_the_current_job(tmp_path):
    from jobhunter.upload import fill_upload_folder
    out = tmp_path / "output" / "2026-11-19_Jobgether"
    out.mkdir(parents=True)
    (out / "CV_Nanda_Mummadireddy_Jobgether.docx").write_text("cv")
    (out / "CV_Nanda_Mummadireddy_Jobgether.pdf").write_text("cvpdf")
    (out / "Cover_Letter_Nanda_Mummadireddy_Jobgether.docx").write_text("letter")
    folder = tmp_path / "_UPLOAD"
    folder.mkdir()
    (folder / "CV_Old_Job.pdf").write_text("old")
    (folder / "keep-me.png").write_text("not ours")
    profile = {"personal": {"first_name": "Nanda Kishore Reddy", "last_name": "Mummadireddy"}}
    fill_upload_folder({"company": "Jobgether", "title": "Revenue Data Analyst", "url": "https://x"},
                       out / "CV_Nanda_Mummadireddy_Jobgether.docx",
                       out / "Cover_Letter_Nanda_Mummadireddy_Jobgether.docx", "Email  me@x", profile, folder=folder)
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["CV_Nanda_Mummadireddy.docx", "CV_Nanda_Mummadireddy.pdf", "Cover_Letter_Nanda_Mummadireddy.docx",
                     "answers.txt", "keep-me.png"]
    assert "Jobgether - Revenue Data Analyst" in (folder / "answers.txt").read_text()


def test_lever_and_workday_open_in_your_normal_browser():
    from jobhunter.autofill import manual_only
    assert manual_only("https://jobs.lever.co/jobgether/0172/apply")
    assert manual_only("https://ea.wd1.myworkdayjobs.com/en-US/EA/job/x")
    assert not manual_only("https://job-boards.greenhouse.io/adyen/jobs/1")


def test_files_button_and_copy_answers(tmp_path, monkeypatch):
    import jobhunter.dashboard as d
    out = tmp_path / "output"
    (out / "job").mkdir(parents=True)
    (out / "job" / "CV_A_B_X.docx").write_text("cv")
    (out / "job" / "Cover_Letter_A_B_X.docx").write_text("l")
    (out / "job" / "tailoring.json").write_text('{"letter": ["I like data."]}')
    monkeypatch.setattr(d, "OUTPUT_DIR", out)
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    job = Job(source="t", external_id="1", company="Acme", title="Analyst", url="https://x", location="Berlin")
    store.save([job])
    store.update(job.uid, cv_path=str(out / "job" / "CV_A_B_X.docx"),
                 letter_path=str(out / "job" / "Cover_Letter_A_B_X.docx"))

    class P(S):
        profile = {"personal": {"first_name": "Ann", "last_name": "Bee", "email": "ann@x.de"}}
        upload_dir = tmp_path / "_UPLOAD"
    opened = []
    c = TestClient(create_app(P(), store, launcher=print, opener=opened.append))
    page = c.get(f"/job/{job.uid}").text
    assert "ann@x.de" in page and "data-copy" in page and "I like data." in page and "/static/copy.js" in page
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    r = c.post(f"/job/{job.uid}/files", data={"token": token})
    assert "_UPLOAD" in r.text and opened == [P.upload_dir]
    assert (P.upload_dir / "CV_Ann_Bee.docx").is_file() and (P.upload_dir / "answers.txt").is_file()
    assert c.get("/static/copy.js").status_code == 200


def test_apply_on_a_closed_job_removes_it(app, tmp_path):
    store = app["store"]
    j = app["jobs"][1]
    launched = []
    c = TestClient(create_app(S(), store, launcher=launched.append, checker=lambda job: False))
    token = re.search(r'name="token" value="([^"]+)"', c.get(f"/job/{j.uid}").text).group(1)
    r = c.post(f"/job/{j.uid}/apply", data={"token": token})
    assert launched == [] and "no longer accepts applications" in r.text
    assert store.get(j.uid)["status"] == "skipped"


def test_apply_myself_opens_chrome_and_the_upload_folder(tmp_path, monkeypatch):
    import jobhunter.dashboard as d
    out = tmp_path / "output"
    (out / "job").mkdir(parents=True)
    (out / "job" / "CV_A_B_X.pdf").write_text("cv")
    monkeypatch.setattr(d, "OUTPUT_DIR", out)
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    job = Job(source="email", external_id="9", company="Sandvik", title="Data Analyst",
              url="https://www.linkedin.com/jobs/view/4491636512/")
    store.save([job])
    store.update(job.uid, cv_path=str(out / "job" / "CV_A_B_X.pdf"))

    class P(S):
        profile = {"personal": {"first_name": "Ann", "last_name": "Bee"}}
        upload_dir = tmp_path / "_UPLOAD"
    folders, pages = [], []
    c = TestClient(create_app(P(), store, launcher=print, opener=folders.append, browser=pages.append,
                              checker=lambda j: None))
    page = c.get(f"/job/{job.uid}").text
    assert "Apply myself in Chrome" in page
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    r = c.post(f"/job/{job.uid}/manual", data={"token": token})
    assert pages == ["https://www.linkedin.com/jobs/view/4491636512/"] and folders == [P.upload_dir]
    assert (P.upload_dir / "CV_Ann_Bee.pdf").is_file()
    assert "Sandvik - Data Analyst" in r.text and "opened in Chrome" in r.text


def test_apply_myself_writes_the_cv_first_when_there_is_none(tmp_path, monkeypatch):
    import jobhunter.dashboard as d
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(d, "OUTPUT_DIR", out)
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    job = Job(source="arbeitnow", external_id="7", company="Acme", title="Data Analyst", url="https://www.arbeitnow.com/x")
    store.save([job])

    def prepare(settings, store_, j):
        (out / "acme").mkdir()
        (out / "acme" / "CV_A_B_Acme.pdf").write_text("cv")
        store_.update(j["uid"], cv_path=str(out / "acme" / "CV_A_B_Acme.pdf"), status="prepared")

    class P(S):
        profile = {"personal": {"first_name": "Ann", "last_name": "Bee"}}
        upload_dir = tmp_path / "_UPLOAD"
    pages = []
    c = TestClient(create_app(P(), store, launcher=print, opener=lambda f: None, browser=pages.append,
                              checker=lambda j: None, preparer=prepare))
    page = c.get(f"/job/{job.uid}").text
    assert "Apply myself in Chrome" in page                 # shown even before a CV exists
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    c.post(f"/job/{job.uid}/manual", data={"token": token})
    assert pages == ["https://www.arbeitnow.com/x"] and (P.upload_dir / "CV_Ann_Bee.pdf").is_file()


def test_posted_within_filter(tmp_path):
    import datetime as dt
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    today = dt.date.today()
    jobs = [Job(source="t", external_id=str(i), company=f"C{i}", title=f"Data Analyst {i}", url=f"https://x/{i}",
                location="Berlin", posted_at=(today - dt.timedelta(days=d)).isoformat())
            for i, d in enumerate((0, 2, 5, 12, 200))]
    store.save(jobs)
    c = TestClient(create_app(S(), store, launcher=print, checker=lambda j: None))
    shown = lambda age: sum(f"Data Analyst {i}" in c.get(f"/?age={age}").text for i in range(5))
    assert (shown("1"), shown("2"), shown("7"), shown("14"), shown("all")) == (1, 2, 3, 4, 5)
    page = c.get("/").text                                  # default: 7 days (no limit in settings)
    assert "Data Analyst 2" in page and "Data Analyst 4" not in page and "Last 48 hours" in page
