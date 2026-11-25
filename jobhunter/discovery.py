"""Companies Job Hunter finds by itself.

Every job ad that links to a company's own careers platform (Personio, Greenhouse, Lever, Ashby,
SmartRecruiters, Recruitee) adds that company here for good, so its careers page is checked on every
run, also for jobs that never reach the job agency or a job board. Small start-ups and medium-sized
companies mostly use these platforms. The list grows with every search; nothing to maintain.

Stored in data/discovered-companies.json. Companies you list yourself in config.yaml come first.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

from .collectors.ats import FETCHERS, IGNORED_SLUGS, detect_ats
from .config import DATA_DIR

log = logging.getLogger("jobhunter")
FILE = None             # default: data/discovered-companies.json (tests point it elsewhere)


def _path(path) -> Path:
    return Path(path) if path else (FILE or DATA_DIR / "discovered-companies.json")
PER_RUN = 150            # careers pages checked per run; the ones checked longest ago go first


def _key(entry: dict) -> str:
    return f"{entry['ats']}:{entry['slug'].lower()}"


def load(path=None) -> dict:
    try:
        return json.loads(_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def companies(path=None) -> dict:
    """The discovered companies (without bookkeeping entries)."""
    return {k: v for k, v in load(path).items() if not k.startswith("_")}


def save(data: dict, path=None) -> None:
    try:
        path = _path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save the discovered companies: %s", exc)


def learn(items, configured: list[dict] | None = None, path=None) -> int:
    """Add the careers platforms mentioned in jobs (objects or dict rows). Returns how many are new."""
    data = load(path)
    mine = {_key(c) for c in configured or [] if c.get("ats") and c.get("slug")}
    today = dt.date.today().isoformat()
    added = 0
    for item in items:
        get = item.get if isinstance(item, dict) else (lambda k, _i=item: getattr(_i, k, ""))
        text = " ".join(str(get(k) or "") for k in ("url", "apply_url", "description"))
        for entry in detect_ats(text):
            if entry["ats"] not in FETCHERS:
                continue
            key = _key(entry)
            if key in data or key in mine:
                continue
            data[key] = {**entry, "name": str(get("company") or entry["slug"]).strip()[:80],
                         "found": today, "checked": "", "jobs": None}
            added += 1
    if added:
        save(data, path)
        log.info("Found %d new company careers pages (now %d)", added, len(data))
    return added


def due(limit: int = PER_RUN, path=None) -> list[dict]:
    """The companies to check this run: never checked first, then the longest ago."""
    data = load(path)
    items = sorted((c for k, c in data.items() if not k.startswith("_")), key=lambda c: c.get("checked") or "")
    return [c for c in items if not c.get("dead")][:limit]


def mark(company: dict, jobs: int | None, path=None) -> None:
    data = load(path)
    key = _key(company)
    if key in data:
        entry = data[key]
        entry["checked"] = dt.datetime.now().isoformat(timespec="seconds")
        entry["fails"] = 0 if jobs is not None else entry.get("fails", 0) + 1
        entry["jobs"] = jobs
        if entry["fails"] >= 5:                      # page gone for good
            entry["dead"] = True
        save(data, path)


# --------------------------------------------------------------------------- finding careers pages by name
# Most ads (LinkedIn, job agency, job boards) don't link to the company's careers platform, so `learn`
# never sees them (smartclip was one). For every company with a matching ad, guess its careers page on
# the common platforms from the company name and keep the ones that exist and belong to that company.
PROBE_PER_RUN = 40           # company names tried per run
PROBE_AGAIN_DAYS = 60        # a name with no careers page found is tried again after this
LEGAL = re.compile(r"\b(gmbh|mbh|ag|se|kg|kgaa|ug|ohg|gbr|e\.?\s?v|inc|ltd|llc|plc|bv|b\.v|sa|sarl|s\.a|corp|"
                   r"co|group|gruppe|holding|germany|deutschland|international|europe|haftungsbeschränkt|"
                   r"technologies|technology|software|solutions|services|digital|the)\b\.?", re.I)
GERMANY = re.compile(r"germany|deutschland|\bde\b|berlin|m[uü]nchen|munich|hamburg|frankfurt|k[oö]ln|cologne|"
                     r"stuttgart|d[uü]sseldorf|leipzig|dresden|hannover|n[uü]rnberg|bremen|essen|dortmund|"
                     r"karlsruhe|mannheim|heidelberg|bonn|potsdam|freiburg|m[uü]nster|aachen|remote", re.I)


def _fold(text: str) -> str:
    text = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("é", "e"), ("è", "e"), ("&", " ")):
        text = text.replace(a, b)
    return text


def slugs_for(name: str) -> list[str]:
    """Likely careers-page slugs for a company name: 'smartclip Europe GmbH' -> ['smartclip']."""
    words = re.findall(r"[a-z0-9]+", LEGAL.sub(" ", _fold(re.sub(r"\(.*?\)", " ", name or ""))))
    if not words:
        return []
    out = ["".join(words), "-".join(words)]
    if len(words) > 2:
        out.append("".join(words[:2]))                 # 'Trade Republic Bank' -> traderepublic
    if len(words) > 1 and len(words[0]) >= 5:
        out.append(words[0])
    seen = []
    for slug in out:
        if len(slug) >= 3 and slug not in seen and slug not in IGNORED_SLUGS:
            seen.append(slug)
    return seen[:4]


def _same_company(name: str, board_name: str) -> bool:
    a = set(re.findall(r"[a-z0-9]{3,}", LEGAL.sub(" ", _fold(name))))
    b = set(re.findall(r"[a-z0-9]{3,}", LEGAL.sub(" ", _fold(board_name))))
    joined_a, joined_b = "".join(sorted(a)), "".join(sorted(b))
    return bool(a & b) or (joined_a and joined_a in "".join(b)) or (joined_b and joined_b in "".join(a))


def _quiet_get(session, url: str, **kw):
    try:
        resp = session.get(url, timeout=10, **kw)
    except Exception:
        return None
    return resp if resp.status_code == 200 else None


def probe_one(session, name: str, slug: str) -> dict | None:
    """Is there a careers page for `name` at `slug`? Returns the company entry, or None."""
    resp = _quiet_get(session, f"https://boards-api.greenhouse.io/v1/boards/{slug}")
    if resp is not None:
        try:
            if _same_company(name, resp.json().get("name", "")):
                return {"ats": "greenhouse", "slug": slug}
        except ValueError:
            pass
    for domain in ("jobs.personio.de", "jobs.personio.com"):
        resp = _quiet_get(session, f"https://{slug}.{domain}/xml", headers={"Accept": "application/xml"})
        if resp is not None and b"<position" in resp.content:
            subs = re.findall(rb"<subcompany>(.*?)</subcompany>", resp.content)
            if not subs or any(_same_company(name, s.decode("utf-8", "ignore")) for s in subs) \
                    or GERMANY.search(resp.content.decode("utf-8", "ignore")):
                return {"ats": "personio", "slug": slug}
    resp = _quiet_get(session, f"https://{slug}.recruitee.com/api/offers/")
    if resp is not None:
        try:
            offers = resp.json().get("offers") or []
        except ValueError:
            offers = []
        if offers and any(_same_company(name, o.get("company_name") or "") for o in offers):
            return {"ats": "recruitee", "slug": slug}
    resp = _quiet_get(session, f"https://{slug}.teamtailor.com/jobs.rss")
    if resp is not None and "<item>" in resp.text:
        title = re.search(r"<title>(.*?)</title>", resp.text, re.S)
        if title and _same_company(name, title.group(1)):
            return {"ats": "teamtailor", "slug": slug}
    # These boards don't name the company: only keep them when they have jobs in Germany.
    resp = _quiet_get(session, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if resp is not None:
        try:
            jobs = resp.json().get("jobs") or []
        except ValueError:
            jobs = []
        if any(GERMANY.search(str(j.get("location", "")) + str(j.get("address", ""))) for j in jobs):
            return {"ats": "ashby", "slug": slug}
    for host, region in (("https://api.lever.co", ""), ("https://api.eu.lever.co", "eu")):
        resp = _quiet_get(session, f"{host}/v0/postings/{slug}", params={"mode": "json"})
        if resp is not None:
            try:
                jobs = resp.json()
            except ValueError:
                jobs = []
            if isinstance(jobs, list) and any(GERMANY.search(str(j.get("categories", ""))) for j in jobs):
                return {"ats": "lever", "slug": slug, **({"region": region} if region else {})}
    resp = _quiet_get(session, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                      params={"country": "de", "limit": 1})
    if resp is not None:
        try:
            content = resp.json().get("content") or []
        except ValueError:
            content = []
        if content and _same_company(name, (content[0].get("company") or {}).get("name", "")):
            return {"ats": "smartrecruiters", "slug": slug}
    return None


def probe(session, names, configured: list[dict] | None = None, path=None,
          limit: int = PROBE_PER_RUN) -> int:
    """Look up careers pages for companies with matching ads. Returns how many were found."""
    data = load(path)
    tried = data.setdefault("_tried", {})
    known = {_fold(c.get("name", "")) for k, c in list(data.items()) + [("", c) for c in configured or []]
             if not k.startswith("_") and isinstance(c, dict) and c.get("name")}
    known_keys = set(data) | {_key(c) for c in configured or [] if c.get("ats") and c.get("slug")}
    today = dt.date.today()
    found = done = 0
    for name in dict.fromkeys(n.strip() for n in names if n and n.strip()):
        if done >= limit:
            break
        folded = _fold(name)
        last = tried.get(folded)
        if folded in known or (last and (today - dt.date.fromisoformat(last)).days < PROBE_AGAIN_DAYS):
            continue
        done += 1
        tried[folded] = today.isoformat()
        for slug in slugs_for(name):
            entry = probe_one(session, name, slug)
            if entry and _key(entry) not in known_keys:
                data[_key(entry)] = {**entry, "name": name[:80], "found": today.isoformat(),
                                     "checked": "", "jobs": None, "how": "name"}
                known_keys.add(_key(entry))
                found += 1
                log.info("Found the careers page of %s (%s: %s)", name, entry["ats"], entry["slug"])
                break
    save(data, path)
    if done:
        log.info("Looked up the careers pages of %d companies, found %d", done, found)
    return found
