"""Filtering and scoring: drop jobs you can't get, rank the rest by fit."""
from __future__ import annotations

import re

from .models import Job

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

# Phrases that mean "German at level X is required" (checked per sentence).
GERMAN_PHRASES: list[tuple[str, re.Pattern]] = [
    ("C1", re.compile(
        r"fluen(?:t|cy)[^.\n]{0,25}\bgerman\b|\bgerman\b[^.\n]{0,15}fluen(?:t|cy)|native[- ]level german|"
        r"native german|excellent (?:command of )?german|proficien(?:t|cy) in german|"
        r"business[- ]fluent german|full professional proficiency in german|german[^.\n]{0,15}\bnative\b|"
        r"flie(?:ß|ss)end(?:e|es)?\s+deutsch|deutsch\w*\s+(?:in wort und schrift\s+)?flie(?:ß|ss)end|"
        r"verhandlungssicher\w*\s+deutsch|deutsch\w*[^.\n]{0,25}verhandlungssicher|"
        r"sehr gute\w*\s+deutsch|deutsch\s+auf\s+muttersprach|muttersprachlich\w*\s+deutsch|"
        r"exzellente\w*\s+deutsch|hervorragende\w*\s+deutsch", re.I)),
    ("B2", re.compile(
        r"good (?:command of |knowledge of |level of )?german|solid german|strong german|"
        r"\bgute\w*\s+deutsch|german (?:language )?skills (?:are )?required|german is required|"
        r"must speak german|german speaking|german-speaking|deutschkenntnisse erforderlich", re.I)),
    ("B1", re.compile(r"conversational german|basic german is required|grundkenntnisse in deutsch", re.I)),
]

LEVEL_NEAR_GERMAN = [
    re.compile(r"\b(?:german|deutsch)\w*[^.\n]{0,40}?\b([ABC][12])\b", re.I),
    re.compile(r"\b([ABC][12])\b[^.\n]{0,30}?\b(?:german|deutsch)", re.I),
]

OPTIONAL_MARKERS = re.compile(
    r"\bplus\b|nice[- ]to[- ]have|an advantage|advantageous|\bbonus\b|beneficial|"
    r"\bpreferred\b|not (?:a )?(?:must|required|necessary)|no german|von vorteil|"
    r"wünschenswert|wuenschenswert|optional|is helpful|would be great|keine deutsch", re.I)

ENGLISH_WORKPLACE = re.compile(
    r"(?:working|company|office|team|business) language is english|english[- ]speaking (?:team|company|environment)|"
    r"english as (?:our|the) (?:working|company) language|all communication (?:is )?in english|"
    r"german (?:is )?not required|no german (?:is )?(?:required|needed)", re.I)

GERMAN_STOPWORDS = set("und der die das wir sie für mit eine einen ist von zu auf bei uns ihre dein deine "
                       "oder als auch werden sowie sind im dich du über".split())
ENGLISH_STOPWORDS = set("and the we you for with a an is of to on at our your or as also be are in "
                        "will this that about".split())

YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:[-–]|to|bis)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs|jahre?n?)\b", re.I)
EXPERIENCE_WORDS = re.compile(r"experience|erfahrung", re.I)

FOREIGN = re.compile(
    r"\b(?:united states|usa|u\.s\.|canada|india|brazil|mexico|singapore|australia|united kingdom|uk|england|"
    r"london|ireland|dublin|netherlands|amsterdam|rotterdam|belgium|brussels|brüssel|france|paris|spain|madrid|"
    r"barcelona|portugal|lisbon|lissabon|italy|milan|mailand|austria|österreich|vienna|wien|switzerland|schweiz|"
    r"zurich|zürich|poland|polen|warsaw|warschau|czech|prague|prag|denmark|copenhagen|sweden|stockholm|"
    r"norway|oslo|finland|helsinki|luxembourg|luxemburg|new york|san francisco|toronto)\b", re.I)
IN_GERMANY = re.compile(r"\b(?:germany|deutschland|bundesweit)\b", re.I)

FAR_AWAY = re.compile(r"\b(?:united states|usa|u\.s\.|canada|india|brazil|mexico|singapore|australia|"
                      r"new york|san francisco|london|toronto)\b", re.I)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?;])\s+|\n|•", text) if s.strip()]


def required_german_level(text: str) -> tuple[str, str]:
    """Return (level, evidence). Level '' means no German requirement found."""
    best, evidence = "", ""
    for sentence in _sentences(text):
        if not re.search(r"german|deutsch", sentence, re.I):
            continue
        if OPTIONAL_MARKERS.search(sentence):
            continue
        level = ""
        for lvl, pattern in GERMAN_PHRASES:
            if pattern.search(sentence):
                level = lvl
                break
        for pattern in LEVEL_NEAR_GERMAN:
            m = pattern.search(sentence)
            if m and m.group(1).upper() in LEVELS:
                level = max(level, m.group(1).upper(), key=lambda l: LEVELS.index(l) if l else -1)
        if level and (not best or LEVELS.index(level) > LEVELS.index(best)):
            best, evidence = level, sentence.strip()[:200]
    return best, evidence


def written_in_german(text: str) -> bool:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    if len(words) < 60:
        return False
    de = sum(w in GERMAN_STOPWORDS for w in words)
    en = sum(w in ENGLISH_STOPWORDS for w in words)
    return de > 2 * en


def min_years_required(text: str) -> int | None:
    for sentence in _sentences(text):
        if not EXPERIENCE_WORDS.search(sentence):
            continue
        m = YEARS.search(sentence)
        if m:
            value = int(m.group(1))
            if 0 < value <= 20:
                return value
    return None


def _contains(haystack: str, needle: str) -> bool:
    needle = needle.lower().strip()
    if re.fullmatch(r"[\w ]+", needle):
        return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None
    return needle in haystack


def title_matches(job: Job, cfg: dict) -> str:
    """Return '' if the title is fine, else a reason for dropping it."""
    title = job.title.lower()
    if not any(_contains(title, kw) for kw in cfg.get("title_keywords", [])):
        return "title not in target roles"
    for kw in cfg.get("exclude_title_keywords", []):
        if _contains(title, kw):
            return f"title contains '{kw}'"
    return ""


def location_matches(job: Job, cfg: dict) -> str:
    loc = (job.location or "").lower()
    if not loc:
        return ""
    if cfg.get("anywhere_in_germany"):
        # Any German city is fine; drop only jobs that are clearly abroad.
        if FOREIGN.search(loc) and not (IN_GERMANY.search(loc) or any(
                _contains(loc, l) for l in cfg.get("locations", []) if l not in ("remote", "germany"))):
            return f"location '{job.location}'"
        return ""
    if FAR_AWAY.search(loc) and not any(_contains(loc, l) for l in cfg.get("locations", []) if l != "remote"):
        return f"location '{job.location}'"
    if job.remote or any(_contains(loc, l) for l in cfg.get("locations", [])):
        return ""
    return f"location '{job.location}'"


def quick_keep(cfg: dict):
    """Cheap pre-filter on title + location (used before fetching full descriptions)."""
    def keep(job: Job) -> bool:
        return not title_matches(job, cfg) and not location_matches(job, cfg)
    return keep


def score_job(job: Job, cfg: dict) -> int:
    text = f"{job.title}\n{job.description}".lower()
    weights: dict = cfg.get("skills", {})
    total = sum(weights.values()) or 1
    matched = [s for s in weights if _contains(text, s)]
    job.matched_skills = matched
    skill_part = 60 * sum(weights[s] for s in matched) / total

    title = job.title.lower()
    strong = ("data engineer", "analytics engineer", "bi engineer", "data analyst")
    title_part = 30 if any(t in title for t in strong) else 20
    bonus = 0
    if ENGLISH_WORKPLACE.search(job.description):
        bonus += 10
    if re.search(r"\bjunior\b|\bentry\b|graduate|berufseinsteiger", title):
        bonus += 5
    return int(min(100, round(skill_part + title_part + bonus)))


def evaluate(job: Job, cfg: dict) -> Job:
    """Fill in score / german_required / excluded_reason on the job."""
    job.excluded_reason = title_matches(job, cfg) or location_matches(job, cfg)
    if job.excluded_reason:
        return job

    my_level = cfg.get("german_level", "A2").upper()
    level, evidence = required_german_level(job.description)
    if not level and written_in_german(job.description) and not ENGLISH_WORKPLACE.search(job.description):
        level, evidence = "B2", "posting is written in German"
    if level and LEVELS.index(level) > LEVELS.index(my_level):
        job.german_required = True
        job.excluded_reason = f"needs German {level}: {evidence}"
        return job

    years = min_years_required(job.description)
    if years is not None and years > cfg.get("max_years_experience", 99):
        job.excluded_reason = f"asks for {years}+ years experience"
        return job

    job.score = score_job(job, cfg)
    has_description = len(job.description) > 200
    if has_description and job.score < cfg.get("min_score", 0):
        job.excluded_reason = f"low match score ({job.score})"
    return job


def filter_jobs(jobs: list[Job], cfg: dict) -> tuple[list[Job], list[Job]]:
    kept, dropped = [], []
    for job in jobs:
        evaluate(job, cfg)
        (dropped if job.excluded_reason else kept).append(job)
    kept.sort(key=lambda j: j.score, reverse=True)
    return kept, dropped
