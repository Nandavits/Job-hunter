from jobhunter.outreach import NOTE_LIMIT, draft, people_search_url, template

JOB = {"company": "Acme GmbH", "title": "Junior Data Engineer", "description": "Python, SQL, dbt, Airflow."}


class FakeClient:
    def __init__(self, text):
        self.text = text
        self.messages = self

    def create(self, **kw):
        class Block:
            type = "text"
        b = Block(); b.text = self.text

        class Msg:
            content = [b]
        return Msg()


def test_template_note_fits_linkedin_limit(profile):
    o = template(profile, JOB, ["dbt", "Apache Airflow", "PostgreSQL", "Docker"])
    assert len(o.note) <= NOTE_LIMIT and "Junior Data Engineer" in o.note and "Acme GmbH" in o.note
    assert "linkedin.com/search/results/people" in o.search_url and "Acme" in o.search_url


def test_claude_draft_used_when_clean(profile):
    text = ('{"note": "Hi, I applied for the Junior Data Engineer role at Acme. I build dbt models on PostgreSQL.", '
            '"message": "Hi, I applied this week. I build dbt models and Airflow DAGs. Happy to answer questions."}')
    o = draft(profile, JOB, ["p"], ["dbt"], client=FakeClient(text))
    assert o.engine == "claude" and o.note.startswith("Hi, I applied")


def test_claude_draft_with_ai_phrases_falls_back(profile):
    text = '{"note": "Hi, I am excited to leverage my passion.", "message": "Truly a perfect fit."}'
    assert draft(profile, JOB, ["p"], ["dbt"], client=FakeClient(text)).engine == "template"


def test_people_search_for_analyst_roles():
    assert "Analytics" in people_search_url("Acme", "Data Analyst")


def test_claude_plain_text_format(profile):
    text = ("NOTE: Hi, I applied for the Junior Data Engineer role at Acme. I build dbt models on PostgreSQL.\n"
            "MESSAGE:\nHi, thanks for connecting.\n\nI build dbt models and Airflow DAGs at Curagita.\n\nBest regards,\nNanda")
    o = draft(profile, JOB, ["p"], ["dbt"], client=FakeClient(text))
    assert o.engine == "claude" and "Airflow DAGs" in o.message and o.message.endswith("Nanda")
