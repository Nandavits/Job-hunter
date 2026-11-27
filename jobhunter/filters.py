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
    r"wünschenswert|wuenschenswert|optional|is helpful|would be great|keine deutsch|desirable|"
    r"\bideally\b|idealerweise|gerne auch|ein plus", re.I)

ENGLISH_WORKPLACE = re.compile(
    r"(?:working|company|office|team|business) language is english|english[- ]speaking (?:team|company|environment)|"
    r"english as (?:our|the) (?:working|company) language|all communication (?:is )?in english|"
    r"german (?:is )?not required|no german (?:is )?(?:required|needed)", re.I)

GERMAN_STOPWORDS = set("und der die das wir sie für mit eine einen ist von zu auf bei uns ihre dein deine "
                       "oder als auch werden sowie sind im dich du über".split())
ENGLISH_STOPWORDS = set("and the we you for with a an is of to on at our your or as also be are in "
                        "will this that about".split())

YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:[-–]|to|bis)?\s*(\d{1,2})?\s*\+?\s*(?:or more\s+|or above\s+|oder mehr\s+)?"
                   r"(?:years?|yrs|jahre?n?)\b", re.I)
# "Several years of professional experience", "mehrjährige Berufserfahrung": counted as 3 years.
SEVERAL_YEARS = re.compile(r"\b(?:several|multiple|many) years\b|mehrjährig\w*|langjährig\w*", re.I)
EXPERIENCE_WORDS = re.compile(r"experience|erfahrung", re.I)

FOREIGN = re.compile(
    r"\b(?:united states|usa|us|u\.s\.|canada|india|brazil|mexico|singapore|australia|united kingdom|uk|england|"
    r"london|ireland|dublin|netherlands|amsterdam|rotterdam|belgium|brussels|brüssel|france|paris|spain|madrid|"
    r"barcelona|portugal|lisbon|lissabon|italy|milan|mailand|austria|österreich|vienna|wien|switzerland|schweiz|"
    r"zurich|zürich|poland|polen|warsaw|warschau|czech|prague|prag|denmark|copenhagen|sweden|stockholm|"
    r"norway|oslo|finland|helsinki|luxembourg|luxemburg|new york|san francisco|toronto|"
    r"nederland|holland|friesland|drachten|utrecht|eindhoven|groningen|den haag|the hague|frankreich|"
    r"bordeaux|lyon|marseille|toulouse|nantes|lille|nice|montpellier|strasbourg|straßburg|españa|valencia|"
    r"italia|rome|rom|turin|porto|polska|krakow|kraków|wroclaw|wrocław|gdansk|budapest|hungary|ungarn|"
    r"romania|rumänien|bucharest|bulgaria|sofia|greece|athens|estonia|tallinn|latvia|riga|lithuania|vilnius|"
    r"belgië|belgique|antwerp|antwerpen|ghent|gent|salzburg|graz|linz|innsbruck|basel|bern|geneva|genf|"
    r"lausanne|danmark|aarhus|sverige|gothenburg|göteborg|malmö|norge|suomi|ukraine|kyiv|serbia|belgrade|"
    r"croatia|zagreb|slovenia|ljubljana|slovakia|bratislava|turkey|istanbul|israel|tel aviv|dubai|uae|"
    r"pakistan|egypt|cairo|nigeria|south africa|philippines|vietnam|indonesia|malaysia|japan|tokyo|china|"
    r"hong kong|korea|seoul|argentina|chile|colombia|bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|"
    r"[a-zé]+-sur-[a-zé]+|saint-[a-zé]+|sainte-[a-zé]+|orly|nanterre|courbevoie|puteaux|levallois|"
    r"boulogne-billancourt|issy-les-moulineaux|rennes|grenoble|sophia antipolis|"
    r"manchester|leeds|hatfield|birmingham|bristol|edinburgh|glasgow|cambridge|oxford|liverpool|nottingham|"
    r"sheffield|newcastle upon tyne|cardiff|belfast|milton keynes|"
    r"nieuwegein|haarlem|leiden|delft|tilburg|breda|nijmegen|arnhem|zwolle|amersfoort|hilversum|almere|"
    r"france|frankrijk|españa|nederland)\b", re.I)
IN_GERMANY = re.compile(r"\b(?:germany|deutschland|bundesweit)\b", re.I)

FAR_AWAY = re.compile(r"\b(?:united states|usa|us|u\.s\.|canada|india|brazil|mexico|singapore|australia|"
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


# Words that only appear in German job titles ("Sachbearbeiter (m/w/d)", "Datenanalyst", "Werkstudent ...").
GERMAN_TITLE = re.compile(
    r"\b(?:mitarbeiter\w*|sachbearbeiter\w*|referent\w*|werkstudent\w*|praktikant\w*|datenanalyst\w*|"
    r"dateningenieur\w*|fachkraft|fachkräfte|kauf(?:mann|frau|leute)|leiter\w*|leitung|berater\w*|"
    r"entwickler\w*|ingenieur\w*|spezialist\w*|verantwortliche\w*|sachgebiet\w*|teamleiter\w*|"
    r"bereich|für|und|im|mit|als|stelle|ausbildung|abschlussarbeit|duales? studium|elternzeitvertretung|"
    r"\w+(?:manager|analyst|engineer)in)\b", re.I)


# Ads written in, or asking for, a language other than English or German (Netherlands, Switzerland ...).
OTHER_LANGUAGE_WORDS = {
    # only words unique to each language ("de", "en" are Dutch and French; "du", "des" also German)
    "Dutch": set("het een van voor met je jouw wij zijn bij ons onze ervaring kennis werken functie "
                 "zoeken bent heb hebt naar ook goede wat wordt worden hebben".split()),
    "French": set("le la les une et pour avec vous nous votre notre est sont dans sur expérience "
                  "poste équipe connaissances".split()),       # not "du"/"des": German words too
}
NEEDS_OTHER_LANGUAGE = re.compile(
    r"\b(?:fluent|native|proficient|excellent|good|strong|business[- ]level)\b[^.]{0,30}\b(Dutch|French|Nederlands|"
    r"Français|Italian|Spanish|Polish|Swedish|Danish|Norwegian|Finnish)\b|\b(Dutch|French|Nederlands|Français)\b[^.]{0,25}"
    r"\b(?:is )?(?:required|a must|mandatory|essential|vereist|obligatoire)\b", re.I)


def other_language(text: str) -> str:
    """'Dutch' or 'French' when the ad is written in it, or asks for it; '' otherwise (English/German ads)."""
    m = NEEDS_OTHER_LANGUAGE.search(text or "")
    if m and not re.search(r"\b(?:plus|advantage|bonus|nice to have|preferred|ideally)\b",
                           text[m.start():m.end() + 40], re.I):
        name = next(g for g in m.groups() if g)
        return {"nederlands": "Dutch", "français": "French"}.get(name.lower(), name.capitalize())
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿ]+", (text or "").lower())
    if len(words) < 25:
        return ""
    english = sum(w in {"the", "and", "you", "with", "for", "our", "are", "will", "your", "of", "to"} for w in words)
    counts = {lang: sum(w in vocab for w in words) for lang, vocab in OTHER_LANGUAGE_WORDS.items()}
    language, hits = max(counts.items(), key=lambda kv: kv[1])
    return language if hits > max(12, 1.5 * english) else ""


def german_title(title: str) -> bool:
    return bool(GERMAN_TITLE.search(title or ""))


def written_in_german(text: str, min_words: int = 60) -> bool:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    if len(words) < min_words:
        return False
    de = sum(w in GERMAN_STOPWORDS for w in words)
    en = sum(w in ENGLISH_STOPWORDS for w in words)
    return de > 2 * en


# "5-7 years as a data scientist", "3+ years building pipelines": a requirement without the word "experience".
YEARS_IN_ROLE = re.compile(
    r"\b(\d{1,2})\s*(?:\+|\s*(?:-|–|to)\s*\d{1,2}\+?)?\s*(?:years?|yrs|jahre?n?)\s+"
    r"(?:as|in|of|building|working|developing|designing|running|hands-on|professional|software|engineering|"
    r"als|in der|im)\b", re.I)
NOT_A_REQUIREMENT = re.compile(r"\bago\b|\bfounded\b|\blegacy\b|\bhistory\b|for the past|\bwe (?:have|combine)|"
                               r"\bour\b|\bseit\b|\bvacation\b", re.I)


NUMBER_WORDS = {"ein": 1, "eine": 1, "einen": 1, "einem": 1, "einer": 1, "eins": 1, "zwei": 2, "drei": 3, "vier": 4,
                "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "zehn": 10, "one": 1, "two": 2,
                "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ten": 10}
NUMBER_WORD = re.compile(r"\b(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) +
                         r")\b(?=\s*\+?\s*(?:(?:[-–]|to|bis)\s*\w+\s*)?(?:jahr|year))", re.I)


def min_years_required(text: str) -> int | None:
    # "Mindestens drei Jahre", "two to three years": the words count like digits
    text = NUMBER_WORD.sub(lambda m: str(NUMBER_WORDS[m.group(1).lower()]), text or "")
    text = re.sub(r"\b(\d{1,2})\s*(?:[-–]|to|bis)\s*(" + "|".join(NUMBER_WORDS) + r")\b",
                  lambda m: f"{m.group(1)}-{NUMBER_WORDS[m.group(2).lower()]}", text, flags=re.I)
    for sentence in _sentences(text):
        if not EXPERIENCE_WORDS.search(sentence):
            continue
        m = YEARS.search(sentence)
        if m:
            value = int(m.group(1))
            if 0 < value <= 20:
                return value
    def near(sentence: str, m: re.Match, width: int = 60) -> str:
        return sentence[max(0, m.start() - width):m.end() + width]

    for sentence in _sentences(text):
        for m in YEARS_IN_ROLE.finditer(sentence):
            if 0 < int(m.group(1)) <= 15 and not NOT_A_REQUIREMENT.search(near(sentence, m)):
                return int(m.group(1))
    for sentence in _sentences(text):
        for m in SEVERAL_YEARS.finditer(sentence):
            window = near(sentence, m)
            if re.search(r"experience|erfahrung|hands-on|professional", window, re.I) \
                    and not NOT_A_REQUIREMENT.search(window):
                return 3
    return None


def _contains(haystack: str, needle: str) -> bool:
    needle = needle.lower().strip()
    if re.fullmatch(r"[\w ]+", needle):
        return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None
    return needle in haystack


# Text from an alert email that was read as a job ("We found these new jobs that match your search ...").
NOT_A_JOB = re.compile(r"we found (?:these )?new jobs|match(?:es)? your search|top tip:|"
                       r"^(?:strong|good|great|possible) fit$", re.I)


# German names of the same roles, so "Datenanalyst (m/w/d)" counts as "data analyst".
GERMAN_TITLES = {
    "data analyst": ["datenanalyst", "datenanalystin", "daten-analyst", "analyst daten"],
    "data engineer": ["dateningenieur", "daten-ingenieur", "datenarchitekt"],
    "data scientist": ["datenwissenschaftler", "data-scientist"],
    "bi analyst": ["bi-analyst", "business-intelligence-analyst", "bi-entwickler", "bi developer"],
    "business intelligence": ["business-intelligence"],
    "analytics engineer": ["analytics-engineer"],
    "reporting": ["reporting-analyst", "berichtswesen"],
    "etl": ["etl-entwickler"],
}


def title_matches(job: Job, cfg: dict) -> str:
    """Return '' if the title is fine, else a reason for dropping it."""
    title = job.title.lower()
    if NOT_A_JOB.search(job.title or "") or NOT_A_JOB.search((job.company or "").strip()):
        return "not a job (text from an alert email)"
    keywords = list(cfg.get("title_keywords", []))
    for kw in list(keywords):
        keywords += GERMAN_TITLES.get(str(kw).lower(), [])
    if not any(_contains(title, kw) for kw in keywords):
        return "title not in target roles"
    for kw in cfg.get("exclude_title_keywords", []):
        if _contains(title, kw):
            return f"title contains '{kw}'"
    return ""


# Countries you can add under  also_countries:  in config.yaml (besides Germany).
COUNTRY_PLACES = {
    "india": r"india|bharat|bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|new delhi|gurgaon|gurugram|"
             r"noida|kolkata|ahmedabad|kochi|cochin|coimbatore|trivandrum|thiruvananthapuram|jaipur|indore|"
             r"chandigarh|visakhapatnam|vizag|vijayawada|warangal|nagpur|mysore|mysuru|bhubaneswar|lucknow",
    "austria": r"austria|österreich|vienna|wien|salzburg|graz|linz|innsbruck",
    "switzerland": r"switzerland|schweiz|zurich|zürich|basel|bern|geneva|genf|lausanne",
    "netherlands": r"netherlands|nederland|holland|amsterdam|rotterdam|utrecht|eindhoven|den haag|the hague",
    "united kingdom": r"united kingdom|uk|england|london|manchester|leeds|birmingham|edinburgh|glasgow",
    "ireland": r"ireland|dublin|cork|galway",
    "france": r"france|paris|lyon|toulouse|[a-zé]+-sur-[a-zé]+|saint-[a-zé]+",
    "spain": r"spain|españa|madrid|barcelona|valencia",
    "portugal": r"portugal|lisbon|lisboa|porto",
    "poland": r"poland|polska|warsaw|warszawa|krakow|kraków|wroclaw|wrocław",
    "usa": r"united states|usa|u\.s\.|new york|san francisco|seattle|boston|austin",
    "canada": r"canada|toronto|vancouver|montreal",
    "uae": r"uae|united arab emirates|dubai|abu dhabi",
    "singapore": r"singapore",
}


def _also_country(loc: str, cfg: dict) -> str:
    """Name of an extra country from  also_countries  that this location is in, else ''."""
    for country in cfg.get("also_countries") or []:
        pattern = COUNTRY_PLACES.get(str(country).strip().lower())
        if pattern and re.search(rf"\b(?:{pattern})\b", loc, re.I):
            return str(country)
    return ""


def location_matches(job: Job, cfg: dict) -> str:
    loc = (job.location or "").lower()
    if not loc:
        return ""
    if _also_country(loc, cfg):
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


# Tools that data job ads often ask for. Ones not in your skills count as gaps in the match score.
OTHER_TOOLS = ["kubernetes", "bigquery", "snowflake", "databricks", "spark", "kafka", "terraform", "aws", "azure",
               "gcp", "google cloud", "dagster", "looker", "fivetran", "redshift", "scala", "java", "flink",
               "airbyte", "sqlmesh", "qlik", "sas", "hadoop", "mongodb", "elasticsearch", "golang",
               # AI and software-engineering tools: their absence keeps AI / backend roles from scoring high
               "pytorch", "tensorflow", "langchain", "llamaindex", "typescript", "javascript", "c++",
               "c#", "rust", "kotlin", "computer vision", "reinforcement learning", "matlab",
               # BI, Microsoft and SAP stacks, web analytics and business tools (Rittal: SSMS, SAP BW, GA4 ...)
               "ssms", "sql server", "t-sql", "ssis", "ssas", "windows server", "windows vms", "windows vm", "vmware",
               "sap bw", "sap hana", "datasphere", "sap analytics cloud", "s/4hana", "sap",
               "ga4", "google analytics", "adobe analytics", "contentsquare", "salesforce", "hubspot",
               "informatica", "talend", "alteryx", "knime", "microstrategy", "cognos", "dax", "power query",
               "vba", "spss", "stata", "sharepoint", "dynamics 365", "oracle", "teradata", "dbeaver",
               "datavault builder", "data vault", "infor", "microsoft fabric", "synapse", "palantir"]
SKILL_ALIASES = {"postgres": ["postgres", "postgresql"], "data modelling": ["data modelling", "data modeling"],
                 "data modeling": ["data modeling", "data modelling"], "airflow": ["airflow"], "etl": ["etl"],
                 "elt": ["elt"], "google cloud": ["google cloud", "gcp"], "gcp": ["gcp", "google cloud"]}


# A requirement the job really insists on ("proven hands-on experience with Databricks", "X is a must").
# Missing one of these caps the score below 70, however many other tools match (the Allianz lesson:
# 7 matching tools gave 79 although the main requirement, Databricks, was missing).
MUST_HAVE = re.compile(
    r"\bproven\b|\brequired\b|\brequirements?\b|\bmust\b|\bmandatory\b|\bessential\b|\bexpert\b|"
    r"in-depth|\bdeep\b|\bsolid\b|\bstrong\b|hands-on experience|extensive|\bseveral years\b|"
    r"\bvoraussetzung\b|zwingend|fundierte|umfassende|sehr gute kenntnisse|mehrjährige|\bconfident\b|"
    r"\bproficient\b|sicherer? umgang|routinierte?r? umgang", re.I)
MUST_HAVE_CAP = {1: 65, 2: 55}
# The ad asks for more German than you have: a real gap, so the score says so ("excellent German" = C1).
GERMAN_CAP = {"B1": 80, "B2": 75, "C1": 60, "C2": 55}


def score_job(job: Job, cfg: dict) -> int:
    """Match percentage (0-100): how well you fit what this job asks for.

    60  tools the job mentions that you have (out of all tools it mentions)
    15  job title is one of your target roles
    10  experience asked for fits (or is not stated)
    15  English is the working language (10 if the ad simply has no German requirement)
    """
    text = f"{job.title}\n{job.description}".lower()
    mine = [s for s in cfg.get("skills", {}) if s not in ("data modeling",)]
    def has(segment: str, skill: str) -> bool:
        return any(_contains(segment, a) for a in SKILL_ALIASES.get(skill, [skill]))

    matched = [s for s in mine if has(text, s)]
    sentences = [x.lower() for x in _sentences(f"{job.title}\n{job.description}")]
    missing: dict[str, float] = {}
    must_missing: list[str] = []
    for tool in OTHER_TOOLS:
        if any(tool in SKILL_ALIASES.get(m, [m]) for m in mine) or not _contains(text, tool):
            continue
        weight = 1.0
        for sentence in (x for x in sentences if _contains(x, tool)):
            alternative = re.search(r"\bor\b|\boder\b|similar|ähnlich|equivalent|comparable", sentence) and \
                any(has(sentence, m) for m in matched)
            optional = OPTIONAL_MARKERS.search(sentence) or re.search(r"\bideally\b|idealerweise", sentence)
            weight = min(weight, 0.0 if alternative else 0.5 if optional else 1.0)
            # "X or a comparable BI platform" accepts what you have; "X (or equivalent Spark platform)"
            # names another tool you lack, so it stays a must-have
            other_way = re.search(r"\b(?:or|oder)\b[^.;]{0,40}\b(?:comparable|similar|equivalent|other|"
                                  r"vergleichbar\w*|ähnlich\w*)\b", sentence) and not any(
                _contains(sentence, t) for t in OTHER_TOOLS if t != tool and not any(
                    t in SKILL_ALIASES.get(m, [m]) for m in mine))
            if not alternative and not optional and not other_way and MUST_HAVE.search(sentence) \
                    and tool not in must_missing:
                must_missing.append(tool)
        if weight:
            missing[tool] = weight
        elif tool in must_missing:
            must_missing.remove(tool)              # an "or similar" elsewhere in the ad: not a hard need
    for tool in list(missing):                      # "sap" when "sap bw" is already listed: count it once
        if any(other != tool and _contains(other, tool) for other in missing):
            missing.pop(tool)
            if tool in must_missing:
                must_missing.remove(tool)
    job.matched_skills = matched
    job.extra["missing_skills"] = list(missing)
    job.extra["missing_must"] = must_missing

    gaps = sum(missing.values())
    mentioned = len(matched) + gaps
    if not job.description.strip():
        skill_part = 20.0                          # alert emails: no ad text to compare yet
    elif mentioned == 0:
        skill_part = 30.0                          # ad names no tools: neutral
    else:
        coverage = len(matched) / mentioned
        evidence = min(1.0, mentioned / 3)         # one matching tool says less than five
        skill_part = 60 * coverage * (0.5 + 0.5 * evidence)

    title = job.title.lower()
    strong = ("data engineer", "analytics engineer", "bi engineer", "data analyst", "bi analyst",
              "business intelligence", "datenanalyst", "dateningenieur", "data platform", "etl")
    title_part = 15 if any(t in title for t in strong) else 8

    years = min_years_required(job.description)
    max_years = cfg.get("max_years_experience", 99)
    exp_part = 7 if years is None else (10 if years <= max_years else 0)

    if ENGLISH_WORKPLACE.search(job.description):
        lang_part = 15
    elif job.description and not written_in_german(job.description, 25) and not german_title(job.title):
        lang_part = 10
    else:
        lang_part = 5

    score = int(min(100, round(skill_part + title_part + exp_part + lang_part)))
    if must_missing:
        score = min(score, MUST_HAVE_CAP.get(len(must_missing), 50))
    german = str(job.extra.get("german") or "")[:2].upper()      # set when the ad asks more German than you have
    if german in GERMAN_CAP:
        score = min(score, GERMAN_CAP[german])
    return score


def job_too_old(job: Job, cfg: dict) -> str:
    """'' if the job is recent enough for  max_job_age_days, else the reason.

    The posting date is used when known; otherwise the day Job Hunter first saw the job
    (a job first seen 10 days ago was posted at least 10 days ago)."""
    from .dates import age_days, date_in_ad
    limit = cfg.get("max_job_age_days")
    if not limit:
        return ""
    posted = job.posted_at or date_in_ad(job.description)
    age = age_days(posted) if posted else age_days(job.extra.get("collected_at", ""))
    if age is not None and age > int(limit):
        return f"posted {age} days ago (limit {limit})" if posted else f"first seen {age} days ago (limit {limit})"
    return ""


def german_ask(title: str, description: str) -> tuple[str, str]:
    """(level, evidence) of the German the ad asks for; ('', '') when none (English ad)."""
    level, evidence = required_german_level(description)
    if not level and not ENGLISH_WORKPLACE.search(description):
        if written_in_german(description, min_words=25):
            level, evidence = "B2", "posting is written in German"
        elif german_title(title) and not (description and not written_in_german(description, 25)
                                          and len(description.split()) >= 60):
            level, evidence = "B2", "job title is in German"
    return level, evidence


def include_german_ads(cfg: dict) -> bool:
    """german_ads: include (default) keeps ads that ask for more German than you have - many companies
    still take English speakers - and marks them; german_ads: skip drops them."""
    return str(cfg.get("german_ads", "include")).strip().lower() != "skip"


def evaluate(job: Job, cfg: dict) -> Job:
    """Fill in score / german_required / excluded_reason on the job."""
    from .hidden import is_hidden
    if is_hidden(job.company, cfg):
        job.excluded_reason = "company hidden by you"
        return job
    job.excluded_reason = title_matches(job, cfg) or location_matches(job, cfg)
    if job.excluded_reason:
        return job

    my_level = cfg.get("german_level", "A2").upper()
    level, evidence = german_ask(job.title, job.description)
    if level and LEVELS.index(level) > LEVELS.index(my_level):
        job.german_required = True
        job.extra["german"] = f"{level}: {evidence}"
        limit = str(cfg.get("german_ads_up_to", "")).upper()           # e.g. B2: hide ads asking C1/C2
        if not include_german_ads(cfg) or (limit in LEVELS and LEVELS.index(level) > LEVELS.index(limit)):
            job.excluded_reason = f"needs German {level}: {evidence}"
            return job
    language = other_language(job.description)
    if language and language.lower() not in [str(x).lower() for x in cfg.get("other_languages", [])]:
        job.excluded_reason = f"needs {language} (written in or asks for it)"
        return job

    years = min_years_required(job.description)
    if years is not None and years > cfg.get("max_years_experience", 99):
        job.excluded_reason = f"asks for {years}+ years experience"
        return job

    too_old = job_too_old(job, cfg)
    if too_old:
        job.excluded_reason = too_old
        return job

    job.score = score_job(job, cfg)
    has_description = len(job.description) > 200
    if not has_description and cfg.get("skip_jobs_without_ad_text"):
        job.excluded_reason = "no ad text to check skills and experience"
    elif has_description and job.score < cfg.get("min_score", 0):
        job.excluded_reason = f"low match score ({job.score})"
    return job


def filter_jobs(jobs: list[Job], cfg: dict) -> tuple[list[Job], list[Job]]:
    kept, dropped = [], []
    for job in jobs:
        evaluate(job, cfg)
        (dropped if job.excluded_reason else kept).append(job)
    kept.sort(key=lambda j: j.score, reverse=True)
    return kept, dropped
