"""AI check: Claude reads the WHOLE ad (any language, years in digits or words) against your profile.

Keyword lists can't know every tool or every way of writing "three years", so every job that passes
the quick filters is read by Claude once. The rule is strict:

  * every REQUIRED skill in the ad must be one you really have (profile.yaml: skills, experience,
    education) - otherwise the job is not shown;
  * nice-to-haves ("desirable", "ideally", "a plus", "von Vorteil", "wünschenswert") never hide a job;
  * "AWS, Azure or GCP" is one requirement: having any one of them is enough;
  * if the ad itself says you don't need to meet every requirement, missing ones don't hide the job;
  * the minimum years of experience and the German level come from the same reading.

Results are cached per job (data/ai-checks/<id>.json) and redone when the ad or your profile changes.
Without an API key Job Hunter keeps using the keyword check.
"""
from __future__ import annotations

import hashlib
import json
import logging

from .config import DATA_DIR

log = logging.getLogger("jobhunter")
FOLDER = None                                    # default: data/ai-checks
MODEL = "claude-haiku-5-5"                       # cheap and fast: about 0.3 cents per ad
LEVELS = ["none", "A1", "A2", "B1", "B2", "C1", "C2"]

SYSTEM = """You check job ads against one candidate's profile, very strictly and honestly.
Read the whole ad, in whatever language it is written (German, English, ...). Numbers may be written as
words ("drei Jahre", "two to three years").

List every concrete requirement: tools, technologies, programming languages, platforms, methods
(e.g. A/B testing, data modelling), domain knowledge, degrees and certifications. Do NOT list soft
skills (communication, teamwork, analytical thinking) or spoken languages (report German separately).
For each requirement:
- required: false only if the ad marks it optional (nice to have, desirable, ideally, a plus, preferred,
  von Vorteil, wünschenswert, idealerweise, gerne). Everything else the ad asks for is required: true.
- alternatives ("AWS, Azure or GCP", "Power BI or Tableau", "or similar") are ONE requirement;
  candidate_has is true if the candidate has any one of them.
- candidate_has: true only if the profile clearly shows it (skills list, experience bullets, education,
  certifications). A related but different tool is false (Metabase is not Tableau, PostgreSQL is not
  SQL Server/SSMS, GCP is not AWS). A degree requirement is met by a related degree the ad accepts.
min_years: the minimum years of professional experience the ad asks for (lowest number of a range;
"mindestens drei Jahre" = 3; "erste Berufserfahrung"/"first experience" = 0; null if not stated).
german_level: the German the ad asks for (none, A1..C2; "sehr gute/fließende/verhandlungssichere
Deutschkenntnisse", "excellent/fluent German" = C1; "gute Deutschkenntnisse" = B2; an ad written in
German without saying = B2).
all_required_needed: false only if the ad says applicants don't need to meet every requirement
("you don't need to tick every box", "nicht alle Anforderungen erfüllen").
Call the save_check tool."""

TOOL = {"name": "save_check", "description": "Save the result of checking the ad against the profile.",
        "input_schema": {"type": "object", "properties": {
            "requirements": {"type": "array", "items": {"type": "object", "properties": {
                "skill": {"type": "string"}, "required": {"type": "boolean"},
                "candidate_has": {"type": "boolean"}}, "required": ["skill", "required", "candidate_has"]}},
            "min_years": {"type": ["integer", "null"]},
            "german_level": {"type": "string", "enum": LEVELS},
            "all_required_needed": {"type": "boolean"}},
            "required": ["requirements", "min_years", "german_level", "all_required_needed"]}}


def _folder():
    return FOLDER or DATA_DIR / "ai-checks"


def _profile_text(profile: dict) -> dict:
    from .tailor import _profile_for_prompt
    p = _profile_for_prompt(profile)
    return {k: p.get(k) for k in ("default_title", "skills", "experience", "projects", "education",
                                  "certifications", "languages") if p.get(k)}


def key_for(job: dict, profile: dict) -> str:
    blob = json.dumps([job.get("title"), job.get("description"), _profile_text(profile), MODEL],
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def cached(job: dict, profile: dict) -> dict | None:
    try:
        data = json.loads((_folder() / f"{job['uid']}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    return data if data.get("key") == key_for(job, profile) else None


def check(job: dict, profile: dict, api_key: str, model: str = "", client=None) -> dict | None:
    """Claude's reading of the ad (cached). None if there is no key or no ad text."""
    if len(job.get("description") or "") < 200 or not (api_key or client):
        return None
    hit = cached(job, profile)
    if hit:
        return hit
    try:
        if client is None:
            from .llm import make_client
            client = make_client(api_key)
        ad = f"Title: {job.get('title')}\nCompany: {job.get('company')}\n\n{(job.get('description') or '')[:12000]}"
        msg = client.messages.create(
            model=model or MODEL, max_tokens=1500, system=SYSTEM, tools=[TOOL], tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": f"<candidate_profile>\n{json.dumps(_profile_text(profile), ensure_ascii=False)}"
                                                  f"\n</candidate_profile>\n\n<job_ad>\n{ad}\n</job_ad>"}])
        data = next((dict(b.input) for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if not data or not isinstance(data.get("requirements"), list):
            return None
    except Exception as exc:
        log.warning("AI check failed for %s: %s", job.get("company"), exc)
        return None
    data["key"] = key_for(job, profile)
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{job['uid']}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def verdict(result: dict, search: dict) -> dict:
    """{'skip': reason or '', 'score': 0-100, 'have': [...], 'missing': [...], 'optional_missing': [...]}."""
    reqs = result.get("requirements") or []
    required = [r for r in reqs if r.get("required")]
    optional = [r for r in reqs if not r.get("required")]
    have = [r["skill"] for r in reqs if r.get("candidate_has")]
    missing = [r["skill"] for r in required if not r.get("candidate_has")]
    optional_missing = [r["skill"] for r in optional if not r.get("candidate_has")]
    years = result.get("min_years")
    max_years = search.get("max_years_experience")
    skip = ""
    if missing and result.get("all_required_needed", True):
        skip = "AI check: missing required " + ", ".join(missing[:6])
    elif years is not None and max_years is not None and years > max_years:
        skip = f"AI check: asks for {years}+ years experience"
    req_part = 70 * (sum(1 for r in required if r.get("candidate_has")) / len(required)) if required else 50
    opt_part = 10 * (sum(1 for r in optional if r.get("candidate_has")) / len(optional)) if optional else 7
    years_part = 10 if years is None or max_years is None or years <= max_years else 0
    my_german = str(search.get("german_level", "A2")).upper()
    asked = str(result.get("german_level") or "none")
    german_gap = asked in LEVELS and my_german in LEVELS and LEVELS.index(asked) > LEVELS.index(my_german)
    score = round(req_part + opt_part + years_part + (2 if german_gap else 10))
    if german_gap:
        from .filters import GERMAN_CAP
        score = min(score, GERMAN_CAP.get(asked, 75))
    return {"skip": skip, "score": max(0, min(100, score)), "have": have, "missing": missing,
            "optional_missing": optional_missing, "german": asked if german_gap else "",
            "years": years}


def summary(result: dict, search: dict) -> str:
    v = verdict(result, search)
    parts = [f"You have: {', '.join(v['have'][:8]) or 'none of the required skills'}"]
    parts.append(f"MISSING REQUIRED: {', '.join(v['missing'][:6])}" if v["missing"] else "All required skills: yes")
    if v["optional_missing"]:
        parts.append(f"Nice to have, missing: {', '.join(v['optional_missing'][:5])}")
    if v["german"]:
        parts.append(f"Asks German {v['german']}")
    parts.append(f"Asks {v['years']} years" if v["years"] else "Years not stated" if v["years"] is None else
                 "No experience needed")
    return " · ".join(parts) + " (AI check)"


def apply_to_open_jobs(store, settings, limit: int = 80) -> int:
    """Check open jobs (new / prepared) now and hide the ones you don't fully match. Returns changes."""
    api_key = settings.env("ANTHROPIC_API_KEY")
    if not api_key:
        return 0
    search, profile = settings.search, settings.profile
    model = settings.env("ANTHROPIC_CHECK_MODEL", MODEL)
    changed = checked = 0
    rows = [r for r in store.list(limit=100_000) if r["status"] in ("new", "prepared")]
    rows.sort(key=lambda r: cached(r, profile) is None)               # cached ones are free: first
    for row in rows:
        result = cached(row, profile)
        if result is None:
            if checked >= limit:
                continue
            checked += 1
            result = check(row, profile, api_key, model)
            if result is None:
                continue
        v = verdict(result, search)
        if v["skip"]:
            store.update(row["uid"], status="skipped", excluded_reason=v["skip"])
            changed += 1
        elif v["score"] != row.get("score") or ",".join(v["have"]) != (row.get("matched_skills") or ""):
            store.update(row["uid"], score=v["score"], matched_skills=",".join(v["have"]))
            changed += 1
    if checked or changed:
        log.info("AI check: read %d ads, %d jobs updated", checked, changed)
    return changed
