"""German ads are kept by default (many companies still hire English speakers) and clearly marked."""
from jobhunter.filters import evaluate
from jobhunter.models import Job

DE_AD = ("Wir suchen dich als Data Engineer in unserem Team in Berlin. Du arbeitest mit SQL, Python, dbt und "
         "Airflow und baust Datenpipelines für unsere Kunden. Sehr gute Deutschkenntnisse (C1) und gute "
         "Englischkenntnisse sind erforderlich. Wir bieten dir ein modernes Arbeitsumfeld und flexible "
         "Arbeitszeiten, Homeoffice und ein tolles Team. Bewirb dich jetzt mit deinem Lebenslauf bei uns.")
CFG = {"title_keywords": ["data engineer", "data analyst"], "locations": ["berlin"], "german_level": "A2",
       "skills": {"sql": 3, "python": 2, "dbt": 3, "airflow": 2}, "max_years_experience": 2}


def job(title="Data Engineer (m/w/d)", text=DE_AD):
    return Job(source="t", external_id="1", company="Acme", title=title, url="https://x", location="Berlin",
               description=text)


def test_german_ad_is_kept_and_marked():
    j = evaluate(job(), CFG)
    assert j.excluded_reason == "" and j.german_required and j.extra["german"].startswith("C1")
    assert j.score > 0


def test_german_ads_skip_still_works():
    j = evaluate(job(), {**CFG, "german_ads": "skip"})
    assert j.excluded_reason.startswith("needs German C1")


def test_german_job_titles_count():
    assert evaluate(job(title="Datenanalyst (m/w/d)"), CFG).excluded_reason == ""
    assert evaluate(job(title="Buchhalter (m/w/d)"), CFG).excluded_reason == "title not in target roles"


def test_why_this_score_says_german(tmp_path):
    from jobhunter.excel import fit_summary
    text = fit_summary({"title": "Data Engineer", "description": DE_AD}, CFG)
    assert "Asks German C1" in text or "German ad" in text


def test_skipped_german_jobs_come_back(tmp_path):
    from jobhunter.pipeline import rescore_open_jobs
    from jobhunter.storage import Store
    store = Store(f"sqlite:///{tmp_path / 'j.db'}")
    j = job()
    store.save([j])
    store.update(j.uid, status="skipped", excluded_reason="needs German C1: sehr gute deutschkenntnisse")
    rescore_open_jobs(store, CFG)
    assert store.get(j.uid)["status"] == "new"
    store.update(j.uid, status="skipped", excluded_reason="needs German C1: x")
    rescore_open_jobs(store, {**CFG, "german_ads": "skip"})
    assert store.get(j.uid)["status"] == "skipped"
