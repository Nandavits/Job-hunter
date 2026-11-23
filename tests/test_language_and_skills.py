from jobhunter.config import effective_skills, _example_skills
from jobhunter.filters import evaluate, german_title, score_job
from jobhunter.models import Job

CFG = {"title_keywords": ["data", "analyst", "engineer", "datenanalyst"], "locations": ["berlin", "remote"],
       "anywhere_in_germany": True, "german_level": "A2", "skills": {"sql": 3, "python": 2}, "min_score": 0}
ENGLISH = ("We are an English speaking team in Berlin. You will build reports with SQL and Python, "
           "work with stakeholders and improve our data models. " * 4)


def job(title, description=""):
    return Job(source="test", external_id=title, title=title, company="Acme", location="Berlin",
               url="https://example.com/" + title.replace(" ", "-"), description=description)


def test_german_titles_are_recognised():
    assert german_title("Sachbearbeiter Datenmanagement (m/w/d)")
    assert german_title("Datenanalyst (m/w/d) im Controlling")
    assert german_title("Data Analyst (m/w/d) für unser Team")
    for english in ("Senior Data Engineer (m/w/d)", "BI Analyst - Remote", "Data Engineer (all genders)",
                    "Analytics Engineer with Python", "Junior Data Analyst"):
        assert not german_title(english), english


def test_german_title_without_ad_text_is_dropped():
    j = evaluate(job("Datenanalyst (m/w/d) im Controlling"), CFG)
    assert j.german_required and "German" in j.excluded_reason


def test_german_title_with_long_english_ad_is_kept():
    j = evaluate(job("Data Analyst (m/w/d) für unser Team", ENGLISH), CFG)
    assert not j.excluded_reason


def test_short_german_ad_is_dropped():
    text = ("Wir suchen dich für unser Team in Berlin. Du arbeitest mit SQL und Python und bist für die "
            "Auswertung der Daten zuständig. Wir bieten dir eine unbefristete Stelle mit flexiblen "
            "Arbeitszeiten und ein tolles Team, das sich auf dich freut.")
    j = evaluate(job("Data Analyst", text), CFG)
    assert j.excluded_reason == "needs German B2: posting is written in German"


def test_score_uses_skills_from_the_profile():
    profile = {"skills": {"Languages": ["SQL (advanced)", "R"], "Tools": ["Apache Spark", "ETL/ELT pipelines",
                                                                          "Power BI"]}}
    skills = effective_skills({}, profile)
    assert {"sql", "spark", "etl", "elt", "power bi"} <= set(skills)
    ad = ("You build pipelines in Spark and dashboards in Power BI. SQL is a must. "
          "We are an English speaking team. " * 3)
    with_profile = score_job(job("Data Analyst", ad), {**CFG, "skills": skills})
    without = score_job(job("Data Analyst", ad), {**CFG, "skills": {"sql": 3, "dbt": 3}})
    assert with_profile > without


def test_untouched_example_skills_only_count_when_in_the_profile():
    example = _example_skills()
    assert "dbt" in example and "n8n" in example
    profile = {"skills": {"Languages": ["SQL", "Python"]}, "experience": [{"bullets": ["Built Tableau reports"]}]}
    skills = effective_skills(example, profile)
    assert "tableau" in skills and "sql" in skills
    assert "dbt" not in skills and "n8n" not in skills and "airflow" not in skills


def test_own_config_skills_are_kept():
    skills = effective_skills({"dbt": 3, "looker": 2}, {"skills": {"Languages": ["SQL"]}})
    assert skills["dbt"] == 3 and skills["looker"] == 2 and "sql" in skills


def test_jobs_in_france_uk_and_netherlands_are_dropped():
    from jobhunter.filters import location_matches
    cfg = {**CFG, "locations": ["berlin", "remote", "germany"]}
    for loc in ("Ivry-sur-Seine", "Orly", "Saint-Denis", "Nieuwegein", "Hatfield", "Manchester", "Leeds"):
        j = job("Data Analyst"); j.location = loc
        assert location_matches(j, cfg), loc
    for loc in ("Elmshorn", "Leverkusen", "Tübingen", "Berlin Metropolitan Area", "Remote, Germany"):
        j = job("Data Analyst"); j.location = loc
        assert not location_matches(j, cfg), loc


def test_alert_email_text_is_not_a_job():
    j = job("We found these new jobs that match your search for Praktikum Data, Berlin. Top tip: apply early!")
    j.company = "Strong Fit"
    assert evaluate(j, CFG).excluded_reason.startswith("not a job")


def test_ai_role_with_only_python_scores_lower_than_data_role():
    ai = ("You build LLM agents with LangChain and PyTorch, LlamaIndex pipelines in Python and TypeScript services. "
          "We are an English speaking team. " * 2)
    data = "You build SQL models and Python pipelines in dbt and Airflow. We are an English speaking team. " * 2
    cfg = {**CFG, "skills": {"sql": 3, "python": 2, "dbt": 3, "airflow": 3}}
    assert score_job(job("AI Engineer", ai), cfg) < 55 <= score_job(job("Data Engineer", data), cfg)


def test_years_without_the_word_experience():
    from jobhunter.filters import min_years_required
    assert min_years_required("Your Profile 5-7 years as a data scientist in a customer-facing role.") == 5
    assert min_years_required("You bring 3+ years of building and running data pipelines in production.") == 3
    assert min_years_required("We uphold a 150-year legacy of innovation. Founded 12 years ago in Berlin.") is None
    assert min_years_required("Together we combine more than 25 years of pharmaceutical expertise.") is None


def test_ai_mentions_do_not_punish_data_roles():
    ad = ("You write SQL and Python, build dbt models and Airflow DAGs. We also explore LLMs for internal tools. "
          "We are an English speaking team. " * 2)
    cfg = {**CFG, "skills": {"sql": 3, "python": 2, "dbt": 3, "airflow": 3}}
    assert score_job(job("Data Engineer", ad), cfg) >= 85
