"""Companies found in job ads are remembered and their careers pages checked every run."""
from jobhunter import discovery
from jobhunter.collectors.ats import detect_ats, fetch_recruitee
from jobhunter.models import Job


def test_detects_the_common_careers_platforms():
    text = ("apply at https://acme.jobs.personio.de/job/123 or https://jobs.smartrecruiters.com/BoschGroup/7 "
            "or https://tiny-startup.recruitee.com/o/data-analyst and https://jobs.lever.co/cred/abc")
    got = {(e["ats"], e["slug"]) for e in detect_ats(text)}
    assert {("personio", "acme"), ("smartrecruiters", "BoschGroup"), ("recruitee", "tiny-startup"),
            ("lever", "cred")} <= got


def test_learn_due_and_mark(tmp_path):
    f = tmp_path / "found.json"
    jobs = [Job(source="arbeitsagentur", external_id="1", company="Acme GmbH", title="Data Analyst",
                url="https://www.arbeitsagentur.de/x", apply_url="https://acme.jobs.personio.de/job/1"),
            {"company": "Moss", "url": "https://jobs.ashbyhq.com/moss/123", "apply_url": "", "description": ""},
            {"company": "Configured", "url": "https://jobs.lever.co/cred/1"}]
    assert discovery.learn(jobs, configured=[{"ats": "lever", "slug": "cred"}], path=f) == 2
    assert discovery.learn(jobs, path=f) == 1                  # cred is new without the configured list
    first = discovery.due(path=f)
    assert {c["slug"] for c in first} == {"acme", "moss", "cred"}
    for c in first:
        discovery.mark(c, 3, path=f)
    assert all(c["checked"] and c["jobs"] == 3 for c in discovery.load(path=f).values())
    for _ in range(5):
        discovery.mark(first[1], None, path=f)
    assert first[1]["slug"] not in {c["slug"] for c in discovery.due(path=f)}   # gone for good


def test_recruitee():
    class R:
        status_code = 200
        def json(self):
            return {"offers": [{"id": 9, "title": "Junior Data Analyst", "city": "Berlin", "country": "Germany",
                                "careers_url": "https://tiny.recruitee.com/o/junior-data-analyst",
                                "description": "<p>SQL and Python</p>", "published_at": "2026-11-24 10:00:00 UTC"}]}
    class S:
        def get(self, url, **kw):
            assert url == "https://tiny.recruitee.com/api/offers/"
            return R()
    [job] = fetch_recruitee(S(), {"name": "Tiny", "slug": "tiny"})
    assert (job.title, job.location, job.posted_at) == ("Junior Data Analyst", "Berlin, Germany", "2026-11-24")
    assert "SQL and Python" in job.description
