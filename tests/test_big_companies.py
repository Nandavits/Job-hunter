from jobhunter.collectors import ats

FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Jobs</title>
<item><title><![CDATA[Data Analyst (f/m/x) (Munich, BY, DE, 80788)]]></title>
<description><![CDATA[<p>Python and <b>SQL</b> required.</p>]]></description>
<pubDate>Fri, 27 Nov 2026 2:01:00 GMT</pubDate>
<link>https://jobs.bmwgroup.com/job/Munich-Data-Analyst-BY-80788/1413819133/</link></item>
<item><title><![CDATA[Controls Engineer (Marshall, IL, US, 62441)]]></title><description>x</description>
<pubDate>Fri, 27 Nov 2026 2:01:00 GMT</pubDate><link>https://jobs.bmwgroup.com/job/x/1413819134/</link></item>
</channel></rss>"""


def test_successfactors_feed_keeps_german_jobs_with_full_text():
    jobs = ats.parse_successfactors(FEED, "BMW Group", "jobs.bmwgroup.com")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Data Analyst (f/m/x)"
    assert job.location == "Munich, Germany"
    assert "Python and SQL required" in job.description
    assert job.posted_at == "2026-11-27"
    assert job.external_id == "jobs.bmwgroup.com:1413819133"
    assert job.source == "successfactors"


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class FakeSession:
    def __init__(self):
        self.bodies = []

    def post(self, url, json=None, timeout=None):
        self.bodies.append(json)
        if json["limit"] == 1:
            return FakeResp({"facets": [{"facetParameter": "Country", "values": [
                {"descriptor": "India", "id": "in1"}, {"descriptor": "Germany", "id": "de1"}]}]})
        return FakeResp({"jobPostings": [{"title": "Data Engineer", "externalPath": "/job/Eschborn/Data_R1",
                                          "locationsText": "Eschborn"}]})

    def get(self, *a, **k):
        return None


def test_workday_asks_the_site_for_jobs_in_germany_only(monkeypatch):
    monkeypatch.setattr(ats, "get", lambda *a, **k: None)
    session = FakeSession()
    jobs = ats.fetch_workday(session, {"name": "Deutsche Bank", "ats": "workday", "host": "db.wd3.myworkdayjobs.com",
                                       "tenant": "db", "site": "DBWebsite", "queries": ["data"]})
    assert session.bodies[1]["appliedFacets"] == {"Country": ["de1"]}
    assert [j.title for j in jobs] == ["Data Engineer"]       # Eschborn kept: the site filtered for Germany


def test_links_to_workday_and_successfactors_sites_are_recognised():
    found = ats.detect_ats("Apply: https://db.wd3.myworkdayjobs.com/en-US/DBWebsite/job/Berlin/Analyst_R123 "
                           "or https://jobs.bmwgroup.com/job/Munich-Data-Analyst-BY-80788/1413819133/")
    assert {"ats": "workday", "slug": "db/DBWebsite", "host": "db.wd3.myworkdayjobs.com", "tenant": "db",
            "site": "DBWebsite"} in found
    assert {"ats": "successfactors", "slug": "jobs.bmwgroup.com", "host": "jobs.bmwgroup.com"} in found


def test_big_companies_are_searched_with_your_own_titles(monkeypatch):
    from jobhunter import collectors, discovery

    seen = []
    monkeypatch.setattr(collectors.ats, "fetch_company", lambda s, c: seen.append(c) or [])
    monkeypatch.setattr(discovery, "due", lambda *a, **k: [])

    class S:
        sources = {"arbeitsagentur": {"queries": ["Data Analyst", "Data Engineer"]}, "discover_companies": False}

        def env(self, *a):
            return ""
    collectors.collect_all(S(), session=object())
    bmw = [c for c in seen if c["name"] == "BMW Group"][0]
    assert bmw["queries"] == ["Data Analyst", "Data Engineer"]
    assert {c["name"] for c in seen} >= {"BMW Group", "Bosch", "Deutsche Bank"}


def test_big_companies_can_be_switched_off(monkeypatch):
    from jobhunter import collectors, discovery

    seen = []
    monkeypatch.setattr(collectors.ats, "fetch_company", lambda s, c: seen.append(c) or [])
    monkeypatch.setattr(discovery, "due", lambda *a, **k: [])

    class S:
        sources = {"big_companies": False, "discover_companies": False}

        def env(self, *a):
            return ""
    collectors.collect_all(S(), session=object())
    assert seen == []


def test_candidates_are_tested_once_and_only_working_ones_used(monkeypatch):
    from jobhunter import bigcompanies
    monkeypatch.setattr(bigcompanies, "CHECK_PER_RUN", 10_000)
    works = {"jobs.mahle.com", "nvidia/NVIDIAExternalCareerSite"}
    calls = []
    monkeypatch.setattr(bigcompanies, "answers", lambda s, c: calls.append(c) or c["slug"] in works)
    first = bigcompanies.boards(session=object())
    names = {c["name"] for c in first}
    assert {"MAHLE", "NVIDIA", "BMW Group"} <= names
    assert "Mercedes-Benz" not in names
    n = len(calls)
    second = bigcompanies.boards(session=object())             # nothing tested again before RETRY_DAYS
    assert len(calls) == n and second == first


def test_once_a_company_site_is_found_its_other_addresses_are_not_tried(monkeypatch):
    from jobhunter import bigcompanies
    monkeypatch.setattr(bigcompanies, "CHECK_PER_RUN", 10_000)
    monkeypatch.setattr(bigcompanies, "answers", lambda s, c: c["slug"] == "jobs.mahle.com")
    bigcompanies.boards(session=object())
    tried = []
    monkeypatch.setattr(bigcompanies, "answers", lambda s, c: tried.append(c) or False)
    bigcompanies._save({**bigcompanies._load(), **{k: {**v, "checked": "2000-01-01"}
                                                     for k, v in bigcompanies._load().items() if not v["ok"]}})
    bigcompanies.boards(session=object())
    assert not [c for c in tried if c["name"] == "MAHLE"]


def test_successfactors_feed_is_asked_for_as_rss(monkeypatch):
    sent = {}

    def fake_get(session, url, params=None, headers=None, **kw):
        sent.update(headers or {})
        return None
    monkeypatch.setattr(ats, "get", fake_get)
    ats.fetch_successfactors(object(), {"name": "BMW Group", "host": "jobs.bmwgroup.com", "queries": ["data"]})
    assert "rss" in sent["Accept"]          # BMW answers 406 to the default "Accept: application/json"


def test_workday_waits_and_retries_when_asked_to_slow_down(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(ats, "get", lambda *a, **k: None)
    replies = iter([FakeResp({}, 429), FakeResp({"facets": []}),
                    FakeResp({}, 429), FakeResp({"jobPostings": [{"title": "Data Analyst",
                                                                  "externalPath": "/job/Berlin/Data_R2",
                                                                  "locationsText": "Berlin"}]})])

    class S:
        def post(self, *a, **k):
            return next(replies)
    jobs = ats.fetch_workday(S(), {"name": "Airbus", "host": "ag.wd3.myworkdayjobs.com", "tenant": "ag",
                                   "site": "Airbus", "queries": ["data"]})
    assert [j.title for j in jobs] == ["Data Analyst"]


def test_a_general_job_board_link_is_not_the_ad():
    from jobhunter.autofill import is_homepage, target_url
    assert is_homepage("https://www.siemens.de/jobboerse")
    assert is_homepage("https://www.example.com/de/karriere/stellenangebote/")
    assert not is_homepage("https://jobs.siemens.com/careers/job/563156123456")
    assert not is_homepage("https://www.example.com/karriere/data-analyst-berlin")
    job = {"apply_url": "https://www.siemens.de/jobboerse",
           "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-123-S"}
    assert target_url(job) == job["url"]


def test_graduate_programmes_about_data_or_it_count_when_switched_on():
    from jobhunter.filters import title_matches
    from jobhunter.models import Job
    cfg = {"title_keywords": ["data analyst"], "exclude_title_keywords": ["senior"], "graduate_roles": True}

    def t(title):
        return title_matches(Job(source="x", external_id=title, company="Deutsche Bank", title=title, url="u"), cfg)
    assert t("Graduate Programme 2027 – Technology, Data & Innovation") == ""
    assert t("Trainee Data & Analytics (m/w/d)") == ""
    assert t("Traineeprogramm IT (w/m/d)") == ""
    assert t("Absolvent Informatik – Einstiegsprogramm") == ""
    assert t("Graduate Programme Sales") == "title not in target roles"        # not about data or IT
    assert t("Trainee Vertrieb (m/w/d)") == "title not in target roles"
    assert title_matches(Job(source="x", external_id="1", company="c", title="Trainee Data", url="u"),
                         {**cfg, "graduate_roles": False}) == "title not in target roles"
