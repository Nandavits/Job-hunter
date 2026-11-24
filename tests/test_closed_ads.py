"""Jobs whose ad says "No longer accepting applications" leave the open list (HiredBuddy case)."""
import datetime as dt

from jobhunter.linkcheck import AD_CLOSED, ad_closed, close_closed_ads
from jobhunter.models import Job
from jobhunter.storage import Store


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class Session:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, **kw):
        self.asked.append(url)
        return self.pages.get(url, Resp("", 404))


LI = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4300000001"
CLOSED_PAGE = '<figure class="closed-job"><figcaption>No longer accepting applications</figcaption></figure>'


def test_ad_closed_reads_linkedins_public_page():
    s = Session({LI: Resp(CLOSED_PAGE)})
    assert ad_closed(s, "https://www.linkedin.com/jobs/view/data-analyst-at-hiredbuddy-4300000001/") is True
    assert s.asked == [LI]
    assert ad_closed(Session({LI: Resp("<p>Apply now. SQL and Python.</p>")}),
                     "https://de.linkedin.com/jobs/view/4300000001") is None
    assert ad_closed(Session({}), "https://www.linkedin.com/jobs/view/4300000001") is None   # 404: can't tell
    assert ad_closed(Session({"https://www.stepstone.de/x": Resp("Diese Stellenanzeige ist nicht mehr verfügbar")}),
                     "https://www.stepstone.de/x") is True


def test_close_closed_ads(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    gone = Job(source="email", external_id="1", company="HiredBuddy", title="Data Analyst",
               url="https://www.linkedin.com/jobs/view/4300000001/")
    fine = Job(source="email", external_id="2", company="Acme", title="Data Analyst",
               url="https://www.linkedin.com/jobs/view/4300000002/")
    store.save([gone, fine])
    s = Session({LI: Resp(CLOSED_PAGE),
                 "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4300000002": Resp("<p>Apply</p>")})
    checked = tmp_path / "checks.json"
    now = dt.datetime(2026, 11, 24, 12, 0)
    assert close_closed_ads(store, s, checked_file=checked, now=now) == 1
    assert store.get(gone.uid)["status"] == "skipped" and store.get(gone.uid)["excluded_reason"] == AD_CLOSED
    assert store.get(fine.uid)["status"] == "new"
    s.asked.clear()
    close_closed_ads(store, s, checked_file=checked, now=now + dt.timedelta(hours=1))
    assert s.asked == []                                   # not asked again within 12 hours
    close_closed_ads(store, s, checked_file=checked, now=now + dt.timedelta(hours=13))
    assert len(s.asked) == 1


def test_a_removed_linkedin_ad_closes_after_two_checks(tmp_path, monkeypatch):
    import time
    from jobhunter.linkcheck import AD_REMOVED
    monkeypatch.setattr(time, "sleep", lambda s: None)
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    job = Job(source="email", external_id="3", company="Grainset Data", title="Junior BI Developer",
              url="https://www.linkedin.com/jobs/view/4490411605/")
    store.save([job])
    s, checked, now = Session({}), tmp_path / "c.json", dt.datetime(2026, 11, 24, 12)
    close_closed_ads(store, s, checked_file=checked, now=now)
    assert store.get(job.uid)["status"] == "new"           # one 404 could be LinkedIn being busy
    close_closed_ads(store, s, checked_file=checked, now=now + dt.timedelta(hours=13))
    assert store.get(job.uid)["status"] == "skipped" and store.get(job.uid)["excluded_reason"] == AD_REMOVED


def test_old_check_file_format_still_works(tmp_path):
    import json
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    job = Job(source="email", external_id="4", company="A", title="Data Analyst",
              url="https://www.linkedin.com/jobs/view/4300000009/")
    store.save([job])
    checked = tmp_path / "c.json"
    checked.write_text(json.dumps({job.uid: "2026-11-24T11:00:00"}))
    s = Session({})
    close_closed_ads(store, s, checked_file=checked, now=dt.datetime(2026, 11, 24, 12))
    assert s.asked == []                                   # checked an hour ago: skipped this time
