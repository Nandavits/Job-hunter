"""Collectors for company career pages hosted on public ATS job-board APIs.

All of these are the official, unauthenticated, read-only endpoints that the companies' own
careers pages use: no login, no scraping of job boards, no applications submitted.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
try:                                    # hardened XML parser for feeds from the internet
    from defusedxml import ElementTree
except ImportError:                     # older installs: stdlib (never resolves external entities)
    from xml.etree import ElementTree  # nosec B405

from ..models import Job, html_to_text
from .base import get, log


# --------------------------------------------------------------------------- Greenhouse
GREENHOUSE_HOSTS = ["https://boards-api.greenhouse.io"]  # serves EU-hosted boards too


def parse_greenhouse(data: dict, company: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        offices = ", ".join(o.get("name", "") for o in item.get("offices") or [] if o.get("name"))
        location = (item.get("location") or {}).get("name") or offices
        jobs.append(Job(
            source="greenhouse",
            external_id=str(item.get("id")),
            company=company,
            title=item.get("title", "").strip(),
            url=item.get("absolute_url", ""),
            location=location,
            description=html_to_text(item.get("content")),
            posted_at=(item.get("first_published") or item.get("updated_at") or "")[:10],
            remote="remote" in location.lower(),
        ))
    return jobs


def fetch_greenhouse(session, company: dict) -> list[Job]:
    for host in GREENHOUSE_HOSTS:
        resp = get(session, f"{host}/v1/boards/{company['slug']}/jobs", params={"content": "true"})
        if resp is not None:
            return parse_greenhouse(resp.json(), company["name"])
    log.warning("Greenhouse board not found: %s (%s)", company["name"], company["slug"])
    return []


# --------------------------------------------------------------------------- Lever
LEVER_HOSTS = ["https://api.lever.co", "https://api.eu.lever.co"]


def _ms_to_date(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return ""


def parse_lever(data: list, company: str) -> list[Job]:
    jobs = []
    for item in data:
        cats = item.get("categories") or {}
        locations = cats.get("allLocations") or [cats.get("location", "")]
        location = ", ".join(l for l in locations if l)
        parts = [item.get("descriptionPlain") or html_to_text(item.get("description"))]
        for section in item.get("lists") or []:
            parts.append(section.get("text", ""))
            parts.append(html_to_text(section.get("content")))
        parts.append(item.get("additionalPlain") or "")
        workplace = (item.get("workplaceType") or "").lower()
        jobs.append(Job(
            source="lever",
            external_id=item.get("id", ""),
            company=company,
            title=item.get("text", "").strip(),
            url=item.get("hostedUrl", ""),
            apply_url=item.get("applyUrl", ""),
            location=location,
            description="\n".join(p for p in parts if p).strip(),
            posted_at=_ms_to_date(item.get("createdAt")),
            remote=workplace == "remote" or "remote" in location.lower(),
        ))
    return jobs


def fetch_lever(session, company: dict) -> list[Job]:
    hosts = LEVER_HOSTS[::-1] if company.get("region") == "eu" else LEVER_HOSTS
    for host in hosts:
        resp = get(session, f"{host}/v0/postings/{company['slug']}", params={"mode": "json"})
        if resp is not None and resp.json():
            return parse_lever(resp.json(), company["name"])
    log.warning("Lever board not found or empty: %s (%s)", company["name"], company["slug"])
    return []


# --------------------------------------------------------------------------- Ashby
def parse_ashby(data: dict, company: str) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue
        locs = [item.get("location", "")] + [
            s.get("location", "") for s in item.get("secondaryLocations") or []
        ]
        location = ", ".join(l for l in locs if l)
        job_url = item.get("jobUrl", "")
        jobs.append(Job(
            source="ashby",
            external_id=item.get("id") or job_url.rstrip("/").split("/")[-1],
            company=company,
            title=item.get("title", "").strip(),
            url=job_url,
            apply_url=item.get("applyUrl", ""),
            location=location,
            description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml")),
            posted_at=(item.get("publishedAt") or "")[:10],
            remote=bool(item.get("isRemote")) or "remote" in location.lower(),
        ))
    return jobs


def fetch_ashby(session, company: dict) -> list[Job]:
    resp = get(session, f"https://api.ashbyhq.com/posting-api/job-board/{company['slug']}")
    if resp is None:
        log.warning("Ashby board not found: %s (%s)", company["name"], company["slug"])
        return []
    return parse_ashby(resp.json(), company["name"])


# --------------------------------------------------------------------------- Personio
def _text(node, tag: str) -> str:
    found = node.find(tag)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_personio(xml: str | bytes, company: str, slug: str) -> list[Job]:
    """Pass bytes when possible: the XML declares its own encoding (usually UTF-8)."""
    root = ElementTree.fromstring(xml)  # nosec B314 - defusedxml when installed
    jobs = []
    for pos in root.iter("position"):
        pid = _text(pos, "id")
        offices = [_text(pos, "office")] + [
            (o.text or "").strip() for o in pos.findall("additionalOffices/office")
        ]
        location = ", ".join(o for o in offices if o)
        desc_parts = []
        for jd in pos.findall("jobDescriptions/jobDescription"):
            desc_parts.append(_text(jd, "name"))
            desc_parts.append(html_to_text(_text(jd, "value")))
        extra = {k: _text(pos, k) for k in ("seniority", "yearsOfExperience", "employmentType", "schedule")}
        url = f"https://{slug}.jobs.personio.de/job/{pid}?language=en"
        jobs.append(Job(
            source="personio",
            external_id=pid,
            company=_text(pos, "subcompany") or company,
            title=_text(pos, "name"),
            url=url,
            apply_url=url + "#apply",
            location=location,
            description="\n".join(p for p in desc_parts if p),
            posted_at=_text(pos, "createdAt")[:10],
            remote="remote" in location.lower(),
            extra=extra,
        ))
    return jobs


def fetch_personio(session, company: dict) -> list[Job]:
    for domain in ("jobs.personio.de", "jobs.personio.com"):
        resp = get(session, f"https://{company['slug']}.{domain}/xml", params={"language": "en"},
                   headers={"Accept": "application/xml"})
        if resp is not None and b"<position" in resp.content:
            return parse_personio(resp.content, company["name"], company["slug"])
    log.warning("Personio feed not found or empty: %s (%s)", company["name"], company["slug"])
    return []


# --------------------------------------------------------------------------- SmartRecruiters
# Public postings API used by many large companies (Bosch, Delivery Hero, ...). Only German jobs that
# match the search words are read in full.
def fetch_smartrecruiters(session, company: dict) -> list[Job]:
    slug, jobs = company["slug"], []
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    for query in company.get("queries") or ["data"]:
        resp = get(session, base, params={"q": query, "country": "de", "limit": 100})
        if resp is None:
            log.warning("SmartRecruiters board not found: %s (%s)", company["name"], slug)
            return []
        for item in resp.json().get("content", []):
            loc = item.get("location") or {}
            location = ", ".join(x for x in (loc.get("city"), loc.get("country", "").upper()) if x)
            jobs.append(Job(
                source="smartrecruiters", external_id=str(item.get("id")), company=company["name"],
                title=(item.get("name") or "").strip(),
                url=f"https://jobs.smartrecruiters.com/{slug}/{item.get('id')}",
                apply_url=f"https://jobs.smartrecruiters.com/{slug}/{item.get('id')}",
                location=location.replace(", DE", ", Germany"), posted_at=(item.get("releasedDate") or "")[:10],
                remote=bool(loc.get("remote")),
            ))
    unique = {j.external_id: j for j in jobs}
    for job in list(unique.values())[: int(company.get("max_details", 30))]:
        detail = get(session, f"{base}/{job.external_id}")
        if detail is None:
            continue
        sections = (detail.json().get("jobAd") or {}).get("sections") or {}
        job.description = "\n".join(html_to_text((sections.get(k) or {}).get("text"))
                                    for k in ("jobDescription", "qualifications", "additionalInformation"))
        job.apply_url = detail.json().get("applyUrl") or job.apply_url
    return list(unique.values())


# --------------------------------------------------------------------------- Workday
# Workday career sites (many large employers) answer the same JSON calls their own pages make.
# Config: {name, ats: workday, host: siemens.wd3.myworkdayjobs.com, tenant: siemens, site: Siemens_Careers}
def _workday_country(session, api: str, country: str = "Germany") -> dict:
    """The site's own filter for one country ({"Country": [id]}), so only those jobs come back. {} if none."""
    try:
        resp = session.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                            timeout=20)
        data = resp.json() if resp.status_code == 200 else {}
    except Exception:
        return {}
    names = {country.lower(), {"germany": "deutschland"}.get(country.lower(), country.lower())}

    def walk(facet_list, parameter=None):
        for facet in facet_list or []:
            param = facet.get("facetParameter") or parameter
            for value in facet.get("values") or []:
                if (value.get("descriptor") or "").strip().lower() in names and value.get("id") and \
                        "country" in (param or "").lower():
                    return {param: [value["id"]]}
                nested = walk([value], param) if value.get("values") else None
                if nested:
                    return nested
        return None
    return walk(data.get("facets")) or {}


def fetch_workday(session, company: dict) -> list[Job]:
    if not all(company.get(k) for k in ("host", "tenant", "site")):
        log.warning("Workday entry for %s needs host, tenant and site", company.get("name"))
        return []
    host, tenant, site = company["host"], company["tenant"], company["site"]
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    jobs: dict[str, Job] = {}
    facets = _workday_country(session, api, company.get("country_name", "Germany"))
    for query in company.get("queries") or ["data"]:
        for offset in range(0, int(company.get("max_results", 100)), 20):
            try:
                resp = session.post(f"{api}/jobs", json={"appliedFacets": facets, "limit": 20, "offset": offset,
                                                         "searchText": query}, timeout=20)
            except Exception as exc:
                log.warning("Workday %s failed: %s", company["name"], exc)
                return list(jobs.values())
            if resp.status_code != 200:
                log.warning("Workday board not found: %s (%s, HTTP %s)", company["name"], host, resp.status_code)
                return list(jobs.values())
            postings = resp.json().get("jobPostings") or []
            for item in postings:
                path = item.get("externalPath") or ""
                if not path or path in jobs:
                    continue
                jobs[path] = Job(source="workday", external_id=path.rsplit("_", 1)[-1] or path,
                                 company=company["name"], title=(item.get("title") or "").strip(),
                                 url=f"https://{host}/{site}{path}", apply_url=f"https://{host}/{site}{path}",
                                 location=item.get("locationsText") or "")
            if len(postings) < 20:
                break
    country = re.compile(company.get("country_pattern", r"germany|deutschland|berlin|munich|münchen|hamburg|"
                                                       r"frankfurt|cologne|köln|stuttgart|düsseldorf|ingolstadt|"
                                                       r"remote|\blocations\b"), re.I)
    wanted = list(jobs.values()) if facets else [j for j in jobs.values() if country.search(j.location)]
    for job in wanted[: int(company.get("max_details", 30))]:
        path = job.url.split(site, 1)[-1]
        detail = get(session, f"{api}{path}")
        if detail is None:
            continue
        info = detail.json().get("jobPostingInfo") or {}
        job.description = html_to_text(info.get("jobDescription"))
        job.location = info.get("location") or job.location
        job.posted_at = (info.get("startDate") or "")[:10]
        job.apply_url = info.get("externalUrl") or job.apply_url
    return wanted


def fetch_recruitee(session, company: dict) -> list[Job]:
    """Recruitee careers sites (many German start-ups): https://<slug>.recruitee.com/api/offers/"""
    slug = company["slug"]
    resp = session.get(f"https://{slug}.recruitee.com/api/offers/", timeout=20)
    if resp.status_code != 200:
        return []
    jobs = []
    for item in resp.json().get("offers") or []:
        location = ", ".join(x for x in (item.get("city"), item.get("country")) if x) or item.get("location") or ""
        jobs.append(Job(
            source="recruitee", external_id=str(item.get("id")), company=company["name"],
            title=item.get("title") or "", url=item.get("careers_url") or f"https://{slug}.recruitee.com",
            apply_url=item.get("careers_apply_url") or item.get("careers_url") or "",
            location=location + (", remote" if item.get("remote") else ""),
            description="\n".join(html_to_text(item.get(k)) for k in ("description", "requirements") if item.get(k)),
            posted_at=(item.get("published_at") or item.get("created_at") or "")[:10]))
    return jobs


def parse_teamtailor(xml: str, company: str) -> list[Job]:
    """Teamtailor careers sites (<company>.teamtailor.com or their own domain) publish /jobs.rss with the full ad."""
    import email.utils
    import html as htmllib
    jobs = []
    for item in re.findall(r"<item>(.*?)</item>", xml or "", re.S):
        def tag(name: str) -> str:
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S)
            text = (m.group(1) if m else "").strip()
            text = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", text, flags=re.S)
            return htmllib.unescape(text)
        cities = [htmllib.unescape(c.strip()) for c in re.findall(r"<tt:city>(.*?)</tt:city>", item, re.S) if c.strip()]
        remote = tag("remoteStatus")
        location = ", ".join(dict.fromkeys(cities)) + (", remote" if remote in ("fully", "hybrid") else "")
        posted = ""
        try:
            posted = email.utils.parsedate_to_datetime(tag("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            pass
        link = tag("link")
        jobs.append(Job(source="teamtailor", external_id=tag("guid") or link, company=company,
                        title=tag("title"), url=link, apply_url=link, location=location.strip(", "),
                        description=html_to_text(tag("description")), posted_at=posted))
    return jobs


def fetch_teamtailor(session, company: dict) -> list[Job]:
    host = company["slug"] if "." in company["slug"] else f"{company['slug']}.teamtailor.com"
    resp = session.get(f"https://{host}/jobs.rss", timeout=20)
    if resp.status_code != 200:
        return []
    return parse_teamtailor(resp.text, company["name"])


# --------------------------------------------------------------------------- SAP SuccessFactors
# Career sites of many large German employers (BMW, ZF, Schaeffler, ...) run on SAP SuccessFactors.
# Each has a public RSS feed with the full ad text: https://<host>/services/rss/job/?keywords=...
# It returns the 20 newest matches per search word; checked every run, so new ads are not missed.
# Config: {name: BMW Group, ats: successfactors, host: jobs.bmwgroup.com}
SF_TITLE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")
SF_COUNTRY_NAMES = {"DE": "Germany", "AT": "Austria", "CH": "Switzerland"}


def parse_successfactors(xml: str | bytes, company: str, host: str, country: str = "DE") -> list[Job]:
    from email.utils import parsedate_to_datetime
    jobs = []
    root = ElementTree.fromstring(xml)
    for item in root.iter("item"):
        raw = _text(item, "title")
        link = _text(item, "link") or _text(item, "guid")
        m = SF_TITLE.match(raw)
        title, where = (m.group(1), m.group(2)) if m else (raw, "")
        parts = [p.strip() for p in where.split(",") if p.strip()]
        if country and parts and country.upper() not in [p.upper() for p in parts]:
            continue                                     # e.g. "(Marshall, IL, US, 62441)"
        city = parts[0] if parts else ""
        posted = ""
        try:
            posted = parsedate_to_datetime(_text(item, "pubDate")).date().isoformat()
        except (TypeError, ValueError):
            pass
        ident = re.search(r"/(\d{6,})/?$", link or "")
        jobs.append(Job(
            source="successfactors", external_id=f"{host}:{ident.group(1) if ident else link}",
            company=company, title=title.strip(), url=link, apply_url=link,
            location=", ".join(x for x in (city, SF_COUNTRY_NAMES.get(country.upper(), country)) if x),
            description=html_to_text(_text(item, "description")), posted_at=posted,
        ))
    return jobs


def fetch_successfactors(session, company: dict) -> list[Job]:
    host = (company.get("host") or company.get("slug") or "").strip().strip("/")
    if not host:
        log.warning("SuccessFactors entry for %s needs host", company.get("name"))
        return []
    found: dict[str, Job] = {}
    for query in company.get("queries") or ["data"]:
        resp = get(session, f"https://{host}/services/rss/job/",
                   params={"locale": company.get("locale", "en_US"), "keywords": query})
        if resp is None or b"<rss" not in resp.content[:500]:
            log.warning("SuccessFactors feed not found: %s (%s)", company["name"], host)
            return list(found.values())
        for job in parse_successfactors(resp.content, company["name"], host, company.get("country", "DE")):
            found.setdefault(job.external_id, job)
    return list(found.values())


FETCHERS = {
    "teamtailor": fetch_teamtailor,
    "recruitee": fetch_recruitee,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "personio": fetch_personio,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "successfactors": fetch_successfactors,
}


def fetch_company(session, company: dict) -> list[Job]:
    fetcher = FETCHERS.get(company.get("ats", "").lower())
    if not fetcher:
        log.warning("Unsupported ATS '%s' for %s", company.get("ats"), company.get("name"))
        return []
    return fetcher(session, company)


# --------------------------------------------------------------------------- discovery
ATS_PATTERNS = [
    (re.compile(r"job-boards\.eu\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"), "greenhouse", "eu"),
    (re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"), "greenhouse", "us"),
    (re.compile(r"boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/([\w-]+)"), "greenhouse", "us"),
    (re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)"), "greenhouse", "us"),
    (re.compile(r"jobs\.eu\.lever\.co/([\w.-]+)"), "lever", "eu"),
    (re.compile(r"jobs\.lever\.co/([\w.-]+)"), "lever", "us"),
    (re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)"), "ashby", ""),
    (re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([\w.%-]+)"), "ashby", ""),
    (re.compile(r"([\w-]+)\.jobs\.personio\.(?:de|com)"), "personio", ""),
    (re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"), "smartrecruiters", ""),
    (re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)"), "smartrecruiters", ""),
    (re.compile(r"([\w-]+)\.recruitee\.com"), "recruitee", ""),
    (re.compile(r"([\w-]+)\.teamtailor\.com"), "teamtailor", ""),
]

IGNORED_SLUGS = {"embed", "v1", "js", "api", "www", "jobs", "job", "careers", "app", "static", "assets", "cdn",
                 "oneclick-ui", "company", "companies", "search"}


WORKDAY_LINK = re.compile(r"https?://(([\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)")
SUCCESSFACTORS_LINK = re.compile(r"https?://([\w.-]+\.[a-z]{2,})/job/[^/\s\"'<>?]+/\d{8,11}/")


def detect_ats(text: str) -> list[dict]:
    """Find ATS + slug references in a URL or a page's HTML."""
    found, seen = [], set()
    for host, tenant, site in WORKDAY_LINK.findall(text or ""):
        if site.lower() in {"wday", "job", "jobs", "login"} or ("workday", f"{tenant}/{site}".lower()) in seen:
            continue
        seen.add(("workday", f"{tenant}/{site}".lower()))
        found.append({"ats": "workday", "slug": f"{tenant}/{site}", "host": host, "tenant": tenant, "site": site})
    for host in SUCCESSFACTORS_LINK.findall(text or ""):
        if ("successfactors", host.lower()) not in seen and "myworkdayjobs" not in host:
            seen.add(("successfactors", host.lower()))
            found.append({"ats": "successfactors", "slug": host.lower(), "host": host.lower()})
    for pattern, ats, region in ATS_PATTERNS:
        for slug in pattern.findall(text):
            key = (ats, slug.lower())
            if slug.lower() in IGNORED_SLUGS or key in seen:
                continue
            seen.add(key)
            entry = {"ats": ats, "slug": slug}
            if region == "eu":
                entry["region"] = "eu"
            found.append(entry)
    return found
