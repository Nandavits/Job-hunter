"""Your job search, worked out from YOUR CV data (config/profile.yaml).

Job Hunter is for anyone: a tester from Cisco with a data science master's should get testing and
data science jobs, not the jobs of whoever wrote the example settings. When config.yaml does not set
its own search (`search: from_profile: true`, or the job titles are still the example ones), the job
titles, the job agency search words and the skill weights come from profile.yaml:

  target_titles   (optional) the jobs you want, e.g. [Data Scientist, QA Automation Engineer]
  default_title   the title on your CV
  experience      your job titles (Junior / Senior / Working Student are ignored)
  education       e.g. "M.Sc. Data Science" adds data science jobs
  skills          every skill becomes a scoring skill

A config.yaml with its own job titles is used exactly as written (from_profile: false keeps it so).
"""
from __future__ import annotations

import re

# Words in your titles / degrees -> the job titles to search for (English, plus German words for the
# federal job agency, whose ads are mostly German).
FAMILIES = [
    (r"data scien|machine learning|\bml\b|deep learning|künstliche intelligenz|\bki\b",
     ["data scientist", "machine learning engineer", "ml engineer", "ai engineer", "data analyst"],
     ["Data Scientist", "Machine Learning Engineer", "KI Engineer"]),
    (r"\bai\b|artificial intelligence|llm|genai",
     ["ai engineer", "machine learning engineer", "ml engineer"], ["AI Engineer"]),
    (r"test|\bqa\b|quality assurance|sdet|qualitätssicherung",
     ["qa engineer", "test engineer", "test automation", "software engineer in test", "sdet",
      "quality assurance", "qa automation", "softwaretester", "testautomatisierung"],
     ["QA Engineer", "Test Automation Engineer", "Softwaretester", "Testautomatisierung"]),
    (r"data engineer|etl|pipeline|data platform",
     ["data engineer", "analytics engineer", "etl developer", "data platform"],
     ["Data Engineer", "Analytics Engineer", "Dateningenieur"]),
    (r"analyst|analytics|business intelligence|\bbi\b|reporting",
     ["data analyst", "bi analyst", "business intelligence", "bi developer", "datenanalyst"],
     ["Data Analyst", "BI Analyst", "Datenanalyst"]),
    (r"software (?:engineer|developer)|developer|programmer|entwickler|backend|full.?stack",
     ["software engineer", "software developer", "python developer", "backend developer", "softwareentwickler"],
     ["Software Engineer", "Softwareentwickler", "Python Developer"]),
    (r"devops|cloud|site reliability|\bsre\b|platform engineer",
     ["devops engineer", "cloud engineer", "platform engineer"], ["DevOps Engineer", "Cloud Engineer"]),
    (r"frontend|front-end|web developer|webentwickl",
     ["frontend developer", "web developer"], ["Frontend Developer", "Webentwickler"]),
]
NOISE = re.compile(r"\b(junior|jr\.?|senior|sr\.?|lead|principal|staff|working student|werkstudent(?:in)?|"
                   r"intern(?:ship)?|trainee|praktikant(?:in)?|associate)\b,?|\((?:[mwfdx]/[mwfdx](?:/[mwfdx])?|all genders?)\)", re.I)


def _clean(title: str) -> str:
    title = NOISE.sub(" ", str(title or ""))
    return re.sub(r"\s+", " ", re.sub(r"[,;|]+", " ", title)).strip(" -&").lower()


def _profile_text(profile: dict) -> list[str]:
    texts = [*(profile.get("target_titles") or []), profile.get("default_title") or ""]
    texts += [e.get("title", "") for e in profile.get("experience") or [] if isinstance(e, dict)]
    texts += [e.get("degree", "") for e in profile.get("education") or [] if isinstance(e, dict)]
    return [t for t in texts if t]


def plan(profile: dict) -> dict:
    """{'title_keywords': [...], 'queries': [...]} for this person (empty lists if the profile says nothing)."""
    titles: list[str] = []
    queries: list[str] = []
    wanted = [_clean(t) for t in profile.get("target_titles") or [] if _clean(t)]
    titles += wanted                                        # your own words first
    queries += [t.title() for t in wanted]
    text = " | ".join(_profile_text(profile)).lower()
    for pattern, family_titles, family_queries in FAMILIES:
        if re.search(pattern, text):
            titles += family_titles
            queries += family_queries
    if not wanted:                                          # your CV title itself, e.g. "data & analytics engineer"
        cv_title = _clean(profile.get("default_title") or "")
        if cv_title and len(cv_title.split()) <= 4:
            titles.insert(0, cv_title)
    titles = list(dict.fromkeys(t for t in titles if t))
    queries = list(dict.fromkeys(q for q in queries if q))[:12]
    return {"title_keywords": titles, "queries": queries}


def uses_profile(search: dict, example_titles: list[str]) -> bool:
    """Should the search come from the profile? Yes if asked to, or if the titles were never changed."""
    flag = search.get("from_profile")
    if flag is not None:
        return bool(flag)
    titles = [str(t).lower() for t in search.get("title_keywords") or []]
    return not titles or (bool(example_titles) and set(titles) <= {str(t).lower() for t in example_titles})


def switch_on(config_file) -> bool:
    """Set  search: from_profile: true  in config.yaml (keeps every other line and comment)."""
    from pathlib import Path
    path = Path(config_file)
    text = path.read_text(encoding="utf-8")
    if re.search(r"(?m)^\s+from_profile:", text):
        new = re.sub(r"(?m)^(\s+)from_profile:.*$", r"\1from_profile: true", text)
    elif re.search(r"(?m)^search:\s*$", text):
        new = re.sub(r"(?m)^search:\s*$", "search:\n  from_profile: true   # job titles, search words and skills come "
                                         "from profile.yaml", text, count=1)
    else:
        return False
    path.write_text(new, encoding="utf-8")
    return True
