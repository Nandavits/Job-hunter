from jobhunter import career
from jobhunter.models import Job


def test_linkedin_offsite_link_is_extracted():
    html = ('<code id="applyUrl" style="display: none"><!--"https://www.linkedin.com/jobs/view/externalApply/123?'
            'url=https%3A%2F%2Fckm-group.jobs.personio.de%2Fjob%2F555%3Flanguage%3Den&urlHash=x"--></code>')
    assert career.linkedin_offsite_link(html) == "https://ckm-group.jobs.personio.de/job/555?language=en"
    assert career.linkedin_offsite_link('<a href="https://www.linkedin.com/login">x</a>') == ""


def test_slug_guesses():
    assert career.slug_guesses("CKM Group GmbH")[:2] == ["ckmgroup", "ckm-group"]


def test_company_board_match(monkeypatch):
    def fake_fetch(session, company):
        if company["ats"] == "personio" and company["slug"] == "ckm-group":
            return [Job("personio", "1", "CKM Group", "Junior Data Engineer (m/w/d)",
                        "https://ckm-group.jobs.personio.de/job/1", apply_url="https://ckm-group.jobs.personio.de/job/1#apply")]
        return []
    monkeypatch.setattr(career.ats, "fetch_company", fake_fetch)
    link = career.find_career_link(None, {"url": "https://www.stepstone.de/stellenangebote--x.html",
                                          "company": "CKM Group", "title": "Junior Data Engineer (m/w/d)"})
    assert link[0].endswith("#apply")


def test_job_board_detection():
    assert career.is_job_board("https://www.linkedin.com/jobs/view/1/")
    assert not career.is_job_board("https://jobs.ashbyhq.com/moss/1")
