from jobhunter.style import problems, tidy


def test_ai_phrases_are_found():
    text = "I am excited to leverage my passion for data in this fast-paced role — a perfect fit!"
    assert {"excited", "leverage", "passion", "fast-paced", "perfect fit"} <= set(problems(text))
    assert problems("I built dbt models on PostgreSQL for Superset dashboards.") == []


def test_tidy_removes_dashes_and_exclamations():
    assert tidy("I built pipelines — in Airflow!") == "I built pipelines, in Airflow."
