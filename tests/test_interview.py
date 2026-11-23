import json
from types import SimpleNamespace

from docx import Document

from jobhunter.interview import build_prep, fallback, prep_path, prepare_due, prepare_interview, write_prep
from jobhunter.storage import Store

JOB = {"uid": "abc123", "company": "Acme GmbH", "title": "Analytics Engineer", "location": "Berlin",
       "description": "We use dbt, SQL, Airflow and BigQuery. English working language."}


class ToolClient:
    """Returns the prep through the tool, like the real API."""
    def __init__(self, data):
        block = SimpleNamespace(type="tool_use", input=data, id="t1")
        self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[block]))


def _all_text(path):
    return "\n".join(p.text for p in Document(path).paragraphs)


def test_fallback_uses_only_profile_facts_and_names_gaps(profile):
    prep = fallback(profile, JOB)
    assert prep["engine"] == "fallback"
    assert len(prep["questions"]) >= 6
    profile_bullets = {b["text"] for e in profile["experience"] for b in e["bullets"]}
    for q in prep["questions"]:
        if q["kind"] in ("technical", "behavioural"):
            assert all(p in profile_bullets for p in q["answer_points"])   # nothing invented
    assert any(g["gap"] == "BigQuery" for g in prep["gaps"])               # honest about what is missing
    assert len(prep["ask_them"]) >= 4


def test_claude_pack_is_cleaned_and_falls_back_when_broken(profile):
    data = {"pitch": "I build data pipelines.", "what_they_want": ["dbt"],
            "questions": [{"question": f"Q{i}?", "kind": "technical", "answer_points": ["point"]} for i in range(8)],
            "gaps": [{"gap": "BigQuery", "honest_answer": "Not yet used in production."}],
            "ask_them": ["A?", "B?", "C?", "D?"], "study_list": ["dbt tests"]}
    prep = build_prep(profile, JOB, client=ToolClient(data))
    assert prep["engine"] == "claude" and prep["questions"][0]["question"] == "Q0?"
    broken = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("down"))))
    assert build_prep(profile, JOB, client=broken)["engine"] == "fallback"


def test_pack_is_written_as_word_file(profile, tmp_path):
    path = write_prep(fallback(profile, JOB), JOB, tmp_path / "Interview_Prep_Acme.docx")
    text = _all_text(path)
    for heading in ("60-second introduction", "Likely questions", "Gaps", "Questions to ask them"):
        assert heading in text


def test_moving_a_job_to_interview_creates_the_pack_once(settings, tmp_path):
    store = Store(settings.database_url)
    from jobhunter.models import Job
    job = Job(source="manual", external_id="x", company="Acme GmbH", title="Analytics Engineer",
              url="https://example.com/job", location="Berlin", description=JOB["description"])
    store.save([job])
    store.update(job.uid, status="interview")
    made = prepare_due(settings, store, tmp_path)
    assert len(made) == 1 and made[0].exists()
    assert "interview prep written" in store.get(job.uid)["notes"]
    assert prepare_due(settings, store, tmp_path) == []          # not written twice
    again = prepare_interview(settings, store, store.get(job.uid), tmp_path, force=True)
    assert again == prep_path(store.get(job.uid), tmp_path)
