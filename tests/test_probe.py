"""Finding careers pages from company names (companies whose ads don't link to their careers page)."""
import json

from jobhunter import discovery


class Resp:
    def __init__(self, status=200, text="", data=None):
        self.status_code, self.text = status, text if data is None else json.dumps(data)
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


class FakeSession:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, **kw):
        self.asked.append(url)
        for part, resp in self.pages.items():
            if part in url:
                return resp
        return Resp(404)


RSS = ("<rss><channel><title>smartclip Europe - Jobs</title><item><title>Data Scientist</title>"
       "<link>https://careers.smartclip.tv/jobs/1</link><tt:city>Berlin</tt:city></item></channel></rss>")


def test_slugs_from_names():
    assert discovery.slugs_for("smartclip Europe GmbH") == ["smartclip"]
    assert discovery.slugs_for("N26 GmbH") == ["n26"]
    assert "traderepublic" in discovery.slugs_for("Trade Republic Bank GmbH")
    assert discovery.slugs_for("Müller & Söhne AG")[0] == "muellersoehne"
    assert discovery.slugs_for("GmbH") == []


def test_finds_teamtailor_page_by_name(tmp_path):
    f = tmp_path / "d.json"
    s = FakeSession({"smartclip.teamtailor.com/jobs.rss": Resp(text=RSS)})
    assert discovery.probe(s, ["smartclip Europe GmbH"], path=f) == 1
    entry = discovery.companies(f)["teamtailor:smartclip"]
    assert entry["name"] == "smartclip Europe GmbH" and entry["how"] == "name"
    assert discovery.due(path=f)[0]["slug"] == "smartclip"


def test_greenhouse_board_of_another_company_is_ignored(tmp_path):
    s = FakeSession({"boards-api.greenhouse.io/v1/boards/acme": Resp(data={"name": "Totally Different Inc"})})
    assert discovery.probe(s, ["Acme GmbH"], path=tmp_path / "d.json") == 0


def test_lever_needs_jobs_in_germany(tmp_path):
    us = [{"categories": {"location": "New York"}}]
    de = [{"categories": {"location": "Berlin, Germany"}}]
    assert discovery.probe(FakeSession({"api.lever.co/v0/postings/acme": Resp(data=us)}),
                           ["Acme"], path=tmp_path / "a.json") == 0
    assert discovery.probe(FakeSession({"api.lever.co/v0/postings/acme": Resp(data=de)}),
                           ["Acme"], path=tmp_path / "b.json") == 1


def test_names_are_not_tried_again_soon_and_limit(tmp_path):
    f = tmp_path / "d.json"
    s = FakeSession({})
    discovery.probe(s, ["Alpha GmbH", "Beta AG", "Gamma SE"], path=f, limit=2)
    first = len(s.asked)
    assert first > 0
    tried = discovery.load(f)["_tried"]
    assert "alpha gmbh" in tried and "beta ag" in tried and "gamma se" not in tried
    discovery.probe(s, ["Alpha GmbH", "Beta AG"], path=f)
    assert len(s.asked) == first                       # both tried already
    assert discovery.companies(f) == {}


def test_known_companies_are_skipped(tmp_path):
    s = FakeSession({})
    discovery.probe(s, ["Stripe"], configured=[{"name": "Stripe", "ats": "greenhouse", "slug": "stripe"}],
                    path=tmp_path / "d.json")
    assert s.asked == []
