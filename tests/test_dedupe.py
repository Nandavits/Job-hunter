import json

import pytest
from conftest import FIX, FakeResponse

from jobhunter.dedupe import normalize_company, normalize_title, same_job
from jobhunter.pipeline import run
from jobhunter.storage import Store


@pytest.mark.parametrize("a, b", [
    ("Data Engineer (m/w/d)", "Data Engineer (all genders)"),
    ("Data Engineer (w/m/d) - Remote", "Data Engineer"),
    ("Data Engineer (all genders) Berlin - Jobbird.com", "Data Engineer (m/f/d)"),
    ("Data Engineer | Berlin", "Data Engineer, m/w/d"),
])
def test_same_role_different_wording(a, b):
    assert same_job("Acme GmbH", a, "Acme", b)


@pytest.mark.parametrize("a, b", [
    ("Junior Data Engineer", "Senior Data Engineer"),
    ("Data Analyst - Marketing", "Data Analyst - Finance"),
    ("Data Engineer II", "Data Engineer III"),
    ("Data Engineer", "Analytics Engineer"),
])
def test_different_roles(a, b):
    assert not same_job("Acme", a, "Acme", b)


def test_company_names():
    assert normalize_company("Trade Republic Bank GmbH") == "trade republic bank"
    assert same_job("Trade Republic Bank GmbH", "Data Platform Engineer", "Trade Republic", "Data Platform Engineer")
    assert not same_job("Acme", "Data Engineer", "Other GmbH", "Data Engineer")
    assert normalize_title("Data / BI Analyst - Operations (m/w/d)") == "data bi analyst operations"


def _repost(session, new_id):
    data = json.loads((FIX / "greenhouse.json").read_text())
    data["jobs"][0]["id"] = new_id                      # same role, new posting id
    data["jobs"][0]["title"] = "Data Engineer (all genders)"
    original = session.get

    def get(url, params=None, headers=None, timeout=None):
        if "greenhouse.io/v1/boards/acme/jobs" in url:
            return FakeResponse(data=data)
        return original(url, params, headers, timeout)
    session.get = get


def test_repost_of_applied_job_is_skipped(settings, session):
    run(settings, send_digest=False, session=session)
    store = Store(settings.database_url)
    original = next(j for j in store.list(limit=100) if j["company"] == "Acme" and j["title"] == "Data Engineer")
    store.mark_applied(original["uid"])

    _repost(session, 9999)
    result = run(settings, send_digest=False, session=session)
    assert result["reposts_already_applied"] == 1 and result["new"] == 0
    repost = next(j for j in store.list(status="skipped", limit=100) if j["title"] == "Data Engineer (all genders)")
    assert "already applied" in repost["excluded_reason"] and original["uid"][:8] in repost["excluded_reason"]
    assert "Skipped 1 repost" in open(result["digest"]).read()


def test_repost_of_job_not_applied_counts_as_duplicate(settings, session):
    run(settings, send_digest=False, session=session)
    _repost(session, 8888)
    result = run(settings, send_digest=False, session=session)
    assert result["duplicates"] == 1 and result["new"] == 0


def test_reapply_allowed_after_window(settings, session):
    settings.config["search"]["reapply_after_days"] = 180
    run(settings, send_digest=False, session=session)
    store = Store(settings.database_url)
    original = next(j for j in store.list(limit=100) if j["company"] == "Acme" and j["title"] == "Data Engineer")
    store.mark_applied(original["uid"])
    store.update(original["uid"], applied_at="2025-01-01")   # applied long ago
    _repost(session, 7777)
    result = run(settings, send_digest=False, session=session)
    assert result["reposts_already_applied"] == 0


def test_marking_applied_hides_copies_from_other_sources(settings):
    from jobhunter.models import Job
    store = Store(settings.database_url)
    a = Job(source="greenhouse", external_id="1", company="Acme GmbH", title="Data Engineer (m/w/d)", url="u")
    b = Job(source="email", external_id="linkedin:5", company="Acme", title="Data Engineer", url="v")
    store.save([a, b])
    assert store.mark_applied(a.uid) == 1
    assert store.get(b.uid)["status"] == "skipped"
