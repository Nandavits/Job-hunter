"""Everyone gets jobs for their own CV, not the example's (a Cisco tester with a data science M.Sc.)."""
from jobhunter.config import Settings
from jobhunter.searchplan import plan, uses_profile

TESTER = {
    "default_title": "Software Engineer in Test",
    "experience": [{"title": "Software Engineer - Testing", "company": "Cisco"},
                   {"title": "Junior QA Engineer", "company": "X"}],
    "education": [{"degree": "M.Sc. Data Science"}, {"degree": "B.Tech Computer Science"}],
    "skills": {"Testing": ["Selenium", "pytest", "Jenkins"], "Data": ["Python", "SQL", "scikit-learn"]},
}


def test_tester_with_data_science_degree_gets_her_own_jobs():
    p = plan(TESTER)
    for t in ("data scientist", "qa engineer", "test automation", "software engineer in test", "software engineer"):
        assert t in p["title_keywords"]
    assert "dbt" not in str(p) and "bi analyst" not in p["title_keywords"]
    assert "Data Scientist" in p["queries"] and "Softwaretester" in p["queries"]


def test_target_titles_come_first_and_old_jobs_do_not_add_titles():
    p = plan({**TESTER, "target_titles": ["Data Scientist (m/w/d)", "Junior ML Engineer"]})
    assert p["title_keywords"][:2] == ["data scientist", "ml engineer"]
    assert "qa engineer" not in p["title_keywords"] and "bi analyst" not in p["title_keywords"]


def test_network_titles():
    p = plan({"target_titles": ["Network Security Engineer"]})
    assert "network engineer" in p["title_keywords"] and "Netzwerkadministrator" in p["queries"]


def test_settings_use_the_profile_and_not_the_example(config, profile):
    config["search"]["from_profile"] = True
    s = Settings(config=config, profile=TESTER)
    assert "qa engineer" in s.search["title_keywords"] and "bi analyst" not in s.search["title_keywords"]
    assert "selenium" in s.search["skills"] and "dbt" not in s.search["skills"]
    assert "Softwaretester" in s.sources["arbeitsagentur"]["queries"]


def test_own_settings_are_kept():
    mine = {"search": {"from_profile": False, "title_keywords": ["data engineer"], "skills": {"dbt": 3}},
            "sources": {"arbeitsagentur": {"queries": ["Data Engineer"]}}}
    s = Settings(config=mine, profile=TESTER)
    assert s.search["title_keywords"] == ["data engineer"] and s.sources["arbeitsagentur"]["queries"] == ["Data Engineer"]
    # no flag, own titles: kept as well
    assert not uses_profile({"title_keywords": ["data engineer", "my own title"]}, ["data engineer"])
    assert uses_profile({"title_keywords": ["data engineer"]}, ["data engineer", "bi analyst"])


def test_switch_on_keeps_the_rest(tmp_path):
    from jobhunter.searchplan import switch_on
    f = tmp_path / "config.yaml"
    f.write_text("# mine\nsearch:\n  title_keywords:\n    - data engineer\nsources: {}\n", encoding="utf-8")
    assert switch_on(f)
    import yaml
    data = yaml.safe_load(f.read_text())
    assert data["search"]["from_profile"] is True and data["search"]["title_keywords"] == ["data engineer"]
    assert f.read_text().startswith("# mine")
    f.write_text("search:\n  from_profile: false\n", encoding="utf-8")
    switch_on(f)
    assert yaml.safe_load(f.read_text())["search"]["from_profile"] is True


def test_someone_elses_skills_in_config_are_ignored(config):
    config["search"]["from_profile"] = True
    config["search"]["skills"] = {"dbt": 3, "airflow": 3, "n8n": 2, "superset": 1}     # Nan's, not the example
    s = Settings(config=config, profile=TESTER)
    assert not {"dbt", "airflow", "n8n", "superset"} & set(s.search["skills"])
    assert {"selenium", "pytest", "python"} <= set(s.search["skills"])
