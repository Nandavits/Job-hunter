"""Every company in Germany that is hiring IT and data people right now.

Once a day the federal job agency is searched for ALL IT / data job ads in Germany (not only your
job titles) and the employer names are kept in data/employers.json. Job Hunter then looks up the
careers page of each of these companies (a few hundred per day, see discovery.probe) and checks
those pages on every run, so jobs that a company posts only on its own site are found too.
No list to maintain: companies that start hiring are added by themselves.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from .collectors import arbeitsagentur
from .config import DATA_DIR

log = logging.getLogger("jobhunter")
FILE = None                      # default: data/employers.json (tests point it elsewhere)

# Broad searches that together cover IT and data jobs; the agency matches them against title and text.
QUERIES = ["Informatik", "Softwareentwickler", "Software Engineer", "Developer", "Data", "Daten", "Analyst",
           "Business Intelligence", "SQL", "Python", "Cloud", "DevOps", "IT-Consultant", "IT-Berater",
           "Systemadministrator", "Machine Learning", "Künstliche Intelligenz", "Webentwickler",
           "Datenbank", "SAP", "Fachinformatiker", "IT-Projektleiter", "Product Owner", "Analytics"]
PAGES = 10                       # 100 ads per page: up to 1,000 ads per search
PER_RUN = 80                     # careers pages looked up per run (48 runs a day)


def _path():
    return FILE or DATA_DIR / "employers.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=0, ensure_ascii=False), encoding="utf-8")


def harvest(session, days: int = 14, queries=None, pages: int = PAGES, search=None) -> int:
    """Collect the employers of all IT/data ads (once a day). Returns how many employers are new."""
    data = load()
    today = dt.date.today().isoformat()
    if data.get("_last") == today:
        return 0
    search = search or arbeitsagentur.search
    firms = data.setdefault("firms", {})
    new = ads = 0
    for query in queries or QUERIES:
        for page in range(1, pages + 1):
            try:
                jobs = search(session, {"was": query, "angebotsart": 1, "veroeffentlichtseit": days,
                                        "size": 100, "page": page})
            except Exception as exc:
                log.warning("Employer search '%s' failed: %s", query, exc)
                break
            for job in jobs:
                name = (job.company or "").strip()
                if not name:
                    continue
                ads += 1
                key = name.lower()
                if key not in firms:
                    firms[key] = {"name": name[:100], "first": today, "ads": 0}
                    new += 1
                firms[key]["ads"] += 1
                firms[key]["last"] = today
            if len(jobs) < 100:
                break
    data["_last"] = today
    _save(data)
    log.info("IT employers in Germany: %d ads read, %d companies known (%d new today)", ads, len(firms), new)
    return new


def names() -> list[str]:
    """Employer names, the ones with most ads (seen most recently) first."""
    firms = load().get("firms", {})
    order = sorted(firms.values(), key=lambda f: (f.get("last", ""), f.get("ads", 0)), reverse=True)
    return [f["name"] for f in order]
