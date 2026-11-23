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


def test_also_countries_lets_india_through():
    from jobhunter.filters import location_matches
    cfg = {**CFG, "locations": ["berlin", "remote", "germany"]}
    j = job("Data Engineer"); j.location = "Hyderabad, Telangana, India"
    assert location_matches(j, cfg)
    assert not location_matches(j, {**cfg, "also_countries": ["india"]})
    j.location = "Ivry-sur-Seine"
    assert location_matches(j, {**cfg, "also_countries": ["india"]})


def test_max_two_years_and_jobs_without_ad_text():
    cfg = {**CFG, "max_years_experience": 2, "skip_jobs_without_ad_text": True}
    three = "You have 3-5 years of experience with SQL and Python. We are an English speaking team. " * 4
    two = "You have 2+ years of experience with SQL and Python. We are an English speaking team. " * 4
    assert evaluate(job("Data Analyst", three), cfg).excluded_reason == "asks for 3+ years experience"
    assert not evaluate(job("Data Analyst", two), cfg).excluded_reason
    assert evaluate(job("Data Analyst"), cfg).excluded_reason.startswith("no ad text")


def test_settings_checker_catches_bad_values():
    from jobhunter.checksettings import check_text
    ok, report = check_text("search:\n  title_keywords: [data]\n  max_years_experience: two\n"
                            "  also_countries: [indien]\nsources:\n  arbeitnow: {enabled: true}\n", "config.yaml")
    text = "\n".join(report)
    assert not ok and "max_years_experience" in text and "indien" in text
    ok, _ = check_text("search:\n  title_keywords: [data]\n  max_years_experience: 2\n"
                       "  also_countries: [india]\nsources:\n  arbeitnow: {enabled: true}\n", "config.yaml")
    assert ok


def test_several_years_and_or_more():
    from jobhunter.filters import min_years_required
    assert min_years_required("Experience: 3 or more years of hands-on work in data science.") == 3
    assert min_years_required("Several years of professional experience in data engineering.") == 3
    assert min_years_required("Sie bringen mehrjährige Berufserfahrung im Bereich BI mit.") == 3
    assert min_years_required("Several years of hands-on data engineering, including Spark.") == 3
