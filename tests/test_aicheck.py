"""Claude reads the whole ad against the profile: every required skill must be yours."""
from types import SimpleNamespace

from jobhunter import aicheck

PROFILE = {"default_title": "Data Engineer", "skills": {"Languages": ["SQL", "Python"], "Cloud": ["Google Cloud (GCP)"]},
           "experience": [], "education": [{"degree": "M.Sc. Data Science"}]}
AD = "x" * 300


class FakeClient:
    def __init__(self, result):
        self.calls = 0
        self.result = result
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.result)])


def job(uid="u1"):
    return {"uid": uid, "title": "Data Engineer", "company": "sonotact", "description": AD}


def test_missing_required_skill_hides_the_job_optional_does_not():
    result = {"requirements": [{"skill": "SQL", "required": True, "candidate_has": True},
                               {"skill": "one of AWS/Azure/GCP", "required": True, "candidate_has": True},
                               {"skill": "Snowflake", "required": True, "candidate_has": False},
                               {"skill": "GA4", "required": False, "candidate_has": False}],
              "min_years": None, "german_level": "none", "all_required_needed": True}
    v = aicheck.verdict(result, {"max_years_experience": 1})
    assert v["skip"] == "AI check: missing required Snowflake" and v["optional_missing"] == ["GA4"]
    result["requirements"][2]["candidate_has"] = True
    v = aicheck.verdict(result, {"max_years_experience": 1})
    assert v["skip"] == "" and v["score"] >= 85


def test_ad_says_you_need_not_tick_every_box():
    result = {"requirements": [{"skill": "Snowflake", "required": True, "candidate_has": False}],
              "min_years": 1, "german_level": "none", "all_required_needed": False}
    assert aicheck.verdict(result, {"max_years_experience": 1})["skip"] == ""


def test_years_and_german_from_the_reading():
    result = {"requirements": [{"skill": "SQL", "required": True, "candidate_has": True}],
              "min_years": 3, "german_level": "C1", "all_required_needed": True}
    v = aicheck.verdict(result, {"max_years_experience": 1, "german_level": "A2"})
    assert v["skip"] == "AI check: asks for 3+ years experience"
    result["min_years"] = None
    v = aicheck.verdict(result, {"max_years_experience": 1, "german_level": "A2"})
    assert v["german"] == "C1" and v["score"] <= 60


def test_each_ad_is_read_once_and_again_when_it_changes():
    client = FakeClient({"requirements": [], "min_years": None, "german_level": "none", "all_required_needed": True})
    assert aicheck.check(job(), PROFILE, "", client=client) is not None
    aicheck.check(job(), PROFILE, "", client=client)
    assert client.calls == 1
    changed = {**job(), "description": AD + " Snowflake"}
    aicheck.check(changed, PROFILE, "", client=client)
    assert client.calls == 2
    assert aicheck.check({**job("u2"), "description": "short"}, PROFILE, "", client=client) is None


def test_summary_text():
    result = {"requirements": [{"skill": "SQL", "required": True, "candidate_has": True},
                               {"skill": "SSMS", "required": True, "candidate_has": False}],
              "min_years": 2, "german_level": "none", "all_required_needed": True}
    text = aicheck.summary(result, {})
    assert "MISSING REQUIRED: SSMS" in text and "Asks 2 years" in text and text.endswith("(AI check)")


def test_answer_as_text_or_string_is_understood():
    from jobhunter.aicheck import _parse
    msg = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"requirements": "[{\\"skill\\": \\"SQL\\", \\"required\\": true, \\"candidate_has\\": true}]", "min_years": 2}')])
    data = _parse(msg)
    assert data["requirements"][0]["skill"] == "SQL" and data["min_years"] == 2 and data["german_level"] == "none"


def test_retries_with_the_tool_forced():
    class Flaky(FakeClient):
        def create(self, **kw):
            self.calls += 1
            if kw["tool_choice"]["type"] == "auto":
                return SimpleNamespace(content=[SimpleNamespace(type="text", text="Let me think...")])
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.result)])
    client = Flaky({"requirements": [{"skill": "SQL", "required": True, "candidate_has": True}]})
    assert aicheck.check(job("u9"), PROFILE, "", client=client)["requirements"][0]["skill"] == "SQL"
    assert client.calls == 2
