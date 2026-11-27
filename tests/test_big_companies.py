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
