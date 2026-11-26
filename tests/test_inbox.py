"""Replies to applications. The example texts are shortened versions of real emails (Nov 2026)."""
from email.message import EmailMessage

import pytest

from jobhunter import inbox
from jobhunter.inbox import apply_reply, check_replies, classify, company_keys, match_job
from jobhunter.models import Job
from jobhunter.storage import Store

MOSS = ("Thank you for your interest in the Applied AI Engineer position. After careful consideration, we've "
        "determined that there isn't a fit at this time, and we will not be moving forward with your application.")
ABLEFY_NO = ("Thank you again for applying for the position of Data Analyst - Mid Level (F/M/X) at ablefy. "
             "We therefore regret to inform you that, on this occasion, we will not proceed with your application.")
ABLEFY_OK = ("Thank you for applying to ablefy. We're confirming that we have received your application for the "
             "opportunity of Data Analyst - Mid Level (F/M/X) and you're on our radar.")
JOIN = "Vervollständigen Sie Ihre Bewerbung an AgentixLake GmbH"
INVITE = ("Hi Nanda, thanks for applying to Statista! We would like to invite you to a first interview. "
          "Unfortunately our team is out on Friday, so please book a slot via calendly.com/statista-talent.")
DE_NO = "Sehr geehrter Herr Mummadireddy, leider müssen wir Ihnen mitteilen, dass wir Ihre Bewerbung nicht berücksichtigen können."
AMAZON = "Vielen Dank für Ihr Interesse an der Stelle. Wenn du noch an der Bewerbung arbeitest: Keine Sorge."


def test_classify_real_replies():
    assert classify(MOSS) == "rejected"
    assert classify(ABLEFY_NO) == "rejected"            # also says 'thank you for applying': rejection wins
    assert classify(ABLEFY_OK) == "received"
    assert classify(JOIN) == "incomplete"
    assert classify(INVITE) == "interview"              # 'unfortunately' inside an invitation is not a rejection
    assert classify(DE_NO) == "rejected"
    assert classify(AMAZON) == ""
    tendent = ("YOU'RE INVITED TO COMPLETE AN EVALUATION. Hi Nanda Kishore, You've been invited to complete an "
               "evaluation as part of the hiring process for the Manufacturing Data Analyst role at Almetra.")
    assert classify(tendent) == "interview"
    assert classify("Your evaluation for Manufacturing Data Analyst at Almetra\nStart evaluation") == "interview"
    flink = ("After careful consideration of your application, we regret to inform you that we will not proceed "
             "with your application for the Data Engineer (m/f/d) position with Flink.")
    assert classify(flink) == "rejected"
    assert classify("Nanda, 5 roles close to your application this week") == ""


def test_company_keys():
    assert company_keys("Almetra (formerly Deltia)") == ["almetra formerly", "almetra", "deltia"] or \
        {"almetra", "deltia"} <= set(company_keys("Almetra (formerly Deltia)"))
    assert "trade republic" in company_keys("Trade Republic")
    assert company_keys("Moss") == ["moss"]


JOBS = [{"uid": "1", "company": "Moss", "title": "Applied AI Engineer", "status": "applied"},
        {"uid": "2", "company": "Ablefy", "title": "Data Analyst - Mid Level (F/M/X)", "status": "applied"},
        {"uid": "3", "company": "AgentixLake GmbH", "title": "Working Student Data Engineer", "status": "applied"},
        {"uid": "4", "company": "Almetra (formerly Deltia)", "title": "Manufacturing Data Analyst", "status": "applied"},
        {"uid": "5", "company": "Statista", "title": "Analytics Engineer (m/f/d)", "status": "applied"}]


def test_match_by_sender_subject_or_ats_text():
    assert match_job("Moss Talent Acquisition Team careers@getmoss.com", "Moss - Application Update", MOSS, JOBS)["uid"] == "1"
    assert match_job("Esra no-reply@ablefy.breezy-mail.com", "Re: Ablefy opportunity", ABLEFY_NO, JOBS)["uid"] == "2"
    assert match_job("noreply@join.com", JOIN, JOIN, JOBS)["uid"] == "3"
    assert match_job("no-reply@ashbyhq.com", "Thanks for applying!", "Thanks for applying to Almetra!", JOBS)["uid"] == "4"
    # a newsletter that names no company you applied to, and one naming two companies: no match
    assert match_job("contact@talentglade.com", "5 roles close to your application", "Data roles", JOBS) is None
    assert match_job("news@x.com", "Moss and Statista are hiring", "", JOBS) is None


@pytest.fixture
def store(tmp_path):
    s = Store(f"sqlite:///{tmp_path / 'jobs.db'}")
    for company, title in (("Moss", "Applied AI Engineer"), ("Statista", "Analytics Engineer (m/f/d)"),
                           ("Ablefy", "Data Analyst")):
        job = Job(source="t", external_id=company, company=company, title=title, url=f"https://x/{company}")
        s.save([job])
        s.update(job.uid, status="applied")
    return s


def _by(store, company):
    return next(j for j in store.list(limit=100) if j["company"] == company)


def test_status_only_moves_forward(store):
    moss = _by(store, "Moss")
    assert apply_reply(store, moss, "received", "Thanks for applying", "2026-11-17") == "note"
    assert _by(store, "Moss")["status"] == "applied"
    assert apply_reply(store, _by(store, "Moss"), "rejected", "Moss - Application Update", "2026-11-23") == "rejected"
    moss = _by(store, "Moss")
    assert moss["status"] == "rejected" and "rejection" in moss["notes"] and "application received" in moss["notes"]
    assert apply_reply(store, moss, "interview", "old invite", "2026-11-20") == "note"     # never backwards
    assert _by(store, "Moss")["status"] == "rejected"
    assert apply_reply(store, _by(store, "Moss"), "interview", "old invite", "2026-11-20") == ""  # same note twice: nothing


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL for check_replies."""
    def __init__(self, messages):
        self.messages = messages

    def __call__(self, host):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a):
        pass

    def select(self, folder, readonly=False):
        assert readonly is True                    # never changes the mailbox
        return "OK", [b"1"]

    def search(self, *a):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, nums, what):
        if b"," in nums or b"HEADER" in what.encode():
            out = []
            for n in nums.split(b","):
                raw = self.messages[int(n) - 1].as_bytes()
                out += [(n + b" (BODY[HEADER] {1}", raw), b")"]
            return "OK", out
        return "OK", [(nums + b" (BODY[] {1}", self.messages[int(nums) - 1].as_bytes()), b")"]


def _mail(sender, subject, body, n):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = sender, subject, f"<m{n}@x>"
    m["Date"] = "Mon, 23 Nov 2026 08:08:26 +0000"
    m.set_content(body)
    return m


def test_check_replies_end_to_end(store, tmp_path, monkeypatch):
    mails = [_mail("Moss Talent <careers@getmoss.com>", "Moss - Application Update", MOSS, 1),
             _mail("Statista Talent <jobs@statista.com>", "Your application at Statista", INVITE, 2),
             _mail("jobalerts-noreply@linkedin.com", "Ablefy is hiring", "unfortunately ablefy application", 3),
             _mail("contact@talentglade.com", "5 roles close to your application", "Data roles", 4)]
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", FakeIMAP(mails))
    seen = tmp_path / "seen.json"
    changes = check_replies(store, "me@gmail.com", "pw", {"days_back": 14}, seen,
                            skip_senders=["jobalerts-noreply@linkedin.com"])
    assert _by(store, "Moss")["status"] == "rejected"
    assert _by(store, "Statista")["status"] == "interview"
    assert _by(store, "Ablefy")["status"] == "applied"           # job-alert email is ignored
    assert len(changes) == 2
    assert check_replies(store, "me@gmail.com", "pw", {}, seen) == []   # already read: nothing twice


def test_receipt_that_explains_rejections_is_not_a_rejection():
    from jobhunter.inbox import classify
    sandvik = ("Thank you for submitting your application for Data Analyst. Hello! Thank you for applying for a "
               "position with us, this is a confirmation that we received your application. We have a limited "
               "ability to give personal feedback to those who unfortunately don't proceed to the next step of "
               "the process. If that's the case, you'll receive your feedback by e-mail.")
    assert classify(sandvik) == "received"
    assert classify("Unfortunately we have decided not to continue with your application.") == "rejected"
    alva = ("Thank you for interest in Sandvik - please conduct an assessment to complete your application. "
            "Sandvik has invited you to take part in an online assessment as part of a recruitment process")
    assert classify(alva) == "interview"


def test_invitation_after_a_misread_rejection_wins(tmp_path):
    from jobhunter.inbox import apply_reply
    from jobhunter.models import Job
    from jobhunter.storage import Store
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    job = Job(source="email", external_id="s", company="Sandvik", title="Data Analyst", url="https://x/s")
    store.save([job])
    store.update(job.uid, status="rejected", notes="[2026-11-24] rejection: Thank you for submitting")
    row = store.get(job.uid)
    assert apply_reply(store, row, "interview", "please conduct an assessment", "2026-11-25") == "interview"
    old = Job(source="email", external_id="o", company="Acme", title="Data Analyst", url="https://x/o")
    store.save([old])
    store.update(old.uid, status="rejected", notes="[2026-11-20] rejection: we regret")
    assert apply_reply(store, store.get(old.uid), "interview", "invite", "2026-11-10") == "note"


def test_talent_pool_and_data_protection_mail_is_not_an_interview():
    from jobhunter.inbox import classify
    constaff = ("Constaff GmbH - Data protection in the application process. First of all, we would like to thank "
                "you for your application and your interest in our company. May we also reach out to you in the "
                "future with suitable project inquiries? If so, we would gladly invite you to become part of our "
                "pool of experts by consenting to the inclusion of your personal data through the feedback portal.")
    assert classify(constaff) == "received"
    assert classify("We would like to invite you to an interview next week.") == "interview"
